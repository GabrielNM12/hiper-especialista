"""Validações fiscais sobre os documentos lidos.

Cada regra tem código, severidade e sugestão de correção. São checagens de consistência que um contador
ou consultor faria à mão; não substituem o validador da SEFAZ nem a análise do contador.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Optional

from .leitor import Documento, Lote
from .util_fiscal import cnpj_valido, documento_valido, dv_chave, gtin_valido, so_digitos

CSOSN_ST = {"201", "202", "203", "500"}
CST_ST = {"10", "30", "60", "70"}

REGRAS = {
    "R01": ("Chave de acesso com dígito verificador inválido", "erro",
            "A chave foi digitada/alterada; baixe o XML original no portal da SEFAZ."),
    "R02": ("Chave de acesso não confere com os dados da nota", "erro",
            "CNPJ, modelo, série ou número dentro da chave diferem do XML: o arquivo pode ter sido editado."),
    "R03": ("CNPJ do emitente inválido", "erro", "Corrija o cadastro da empresa emitente."),
    "R04": ("CPF/CNPJ do destinatário inválido", "aviso", "Revise o cadastro do cliente/fornecedor."),
    "R05": ("Nota sem protocolo de autorização", "erro",
            "Sem protocolo a nota não tem validade. Verifique se foi transmitida ou se está em contingência."),
    "R06": ("Documentos emitidos em homologação", "info",
            "Notas de homologação (tpAmb=2) não têm valor fiscal. Esperado só em ambientes de teste."),
    "R07": ("Total da nota (vNF) não fecha com os itens", "erro",
            "vNF deve ser vProd − vDesc + vFrete + vSeg + vOutro + vST + vIPI. Verifique arredondamentos e acréscimos."),
    "R08": ("CFOP incompatível com o tipo de operação", "erro",
            "Saídas usam CFOP 5/6/7; entradas usam 1/2/3."),
    "R09": ("CFOP incompatível com o destino da operação", "erro",
            "Operação interna (idDest=1) usa CFOP 5xxx; interestadual (idDest=2) usa 6xxx."),
    "R10": ("NFC-e com CFOP fora de operação interna", "erro",
            "NFC-e (modelo 65) só ampara venda presencial ou entrega dentro do estado: CFOP 5xxx."),
    "R11": ("Regime tributário (CRT) incompatível com o grupo de ICMS", "erro",
            "Simples Nacional (CRT 1/2) usa CSOSN; regime normal (CRT 3) usa CST."),
    "R12": ("Substituição tributária incoerente entre CSOSN/CST e CFOP", "erro",
            "Mercadoria com ICMS-ST já retido (CSOSN 500 / CST 60) sai com CFOP 5405; CFOP 5102 indica tributação normal."),
    "R13": ("Produto com ST sem CEST", "aviso",
            "Itens sujeitos a substituição tributária devem informar o CEST (Convênio ICMS 142/2018)."),
    "R14": ("NCM em formato inválido", "erro", "NCM deve ter 8 dígitos e não pode ser 00000000 em mercadorias."),
    "R15": ("GTIN (código de barras) com dígito verificador inválido", "aviso",
            "Corrija o código de barras no cadastro ou use SEM GTIN; a SEFAZ valida o GTIN no Cadastro Centralizado."),
    "R16": ("Regime normal sem o grupo IBS/CBS", "aviso",
            "Desde 2026 o grupo IBSCBS (NT 2025.002-RTC) é exigido de emitentes do regime normal; a rejeição em "
            "produção está suspensa, mas o ERP precisa estar pronto. Confira a versão vigente da NT."),
    "R17": ("Alíquota de teste IBS/CBS diferente do previsto para 2026", "aviso",
            "Em 2026 o destaque é informativo: CBS 0,9% e IBS 0,1% (LC 214/2025). Revise a parametrização."),
    "R18": ("Valor de IBS/CBS não confere com base × alíquota", "aviso", "Revise o cálculo do item."),
    "R19": ("Mesmo produto vendido com NCM diferentes", "erro",
            "Um produto tem um único NCM. Duas classificações costumam indicar cadastro duplicado."),
    "R20": ("Mesmo produto vendido com tributação de ICMS diferente", "aviso",
            "O mesmo item saiu com CSOSN/CST diferentes. Padronize o cadastro tributário."),
    "R21": ("Simples Nacional: grupo IBS/CBS ainda não exigido", "info",
            "Para CRT 1 a validação do grupo em produção aguarda NT futura. Use o período para classificar os "
            "produtos (cClassTrib) e testar em homologação."),
}


@dataclass
class Achado:
    regra: str
    severidade: str
    titulo: str
    mensagem: str
    sugestao: str
    chave: Optional[str] = None
    documento: Optional[str] = None
    item: Optional[int] = None
    produto: Optional[str] = None

    def dict(self) -> dict:
        return asdict(self)


def _r(v: float) -> str:
    """Valor em reais no formato brasileiro."""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _achado(regra: str, mensagem: str, doc: Optional[Documento] = None, item=None) -> Achado:
    titulo, severidade, sugestao = REGRAS[regra]
    return Achado(regra, severidade, titulo, mensagem, sugestao,
                  chave=doc.chave if doc else None,
                  documento=f"{doc.tipo} {doc.serie}/{doc.numero}" if doc else None,
                  item=item.n_item if item else None,
                  produto=f"{item.c_prod} {item.x_prod}" if item else None)


def cnpj_da_loja(lote: Lote) -> Optional[str]:
    """O emitente mais frequente das saídas é a empresa analisada."""
    contagem = Counter(d.emit_cnpj for d in lote.documentos if d.tp_nf == 1)
    return contagem.most_common(1)[0][0] if contagem else None


def validar_documento(doc: Documento) -> list[Achado]:
    a: list[Achado] = []
    chave = so_digitos(doc.chave)
    if len(chave) != 44 or dv_chave(chave[:43]) != int(chave[43]):
        a.append(_achado("R01", f"Chave {doc.chave} com DV inválido.", doc))
    else:
        if chave[6:20] != so_digitos(doc.emit_cnpj).zfill(14) or int(chave[20:22]) != doc.modelo \
                or int(chave[22:25]) != doc.serie or int(chave[25:34]) != doc.numero:
            a.append(_achado("R02", "Campos codificados na chave diferem do conteúdo da nota.", doc))
    if len(so_digitos(doc.emit_cnpj)) == 14 and not cnpj_valido(doc.emit_cnpj):
        a.append(_achado("R03", f"CNPJ {doc.emit_cnpj} inválido.", doc))
    if doc.dest_doc and so_digitos(doc.dest_doc) == doc.dest_doc and not documento_valido(doc.dest_doc):
        a.append(_achado("R04", f"Documento {doc.dest_doc} inválido.", doc))
    if not doc.autorizada and not doc.cancelada:
        a.append(_achado("R05", f"cStat {doc.c_stat or 'ausente'}.", doc))

    esperado = round(doc.v_prod - doc.v_desc + doc.v_frete + doc.v_seg + doc.v_outro + doc.v_st + doc.v_ipi, 2)
    if abs(esperado - doc.v_nf) > 0.01:
        a.append(_achado("R07", f"vNF {_r(doc.v_nf)}; esperado {_r(esperado)} (diferença {_r(doc.v_nf - esperado)}).", doc))
    soma_itens = round(sum(i.v_prod for i in doc.itens), 2)
    if abs(soma_itens - doc.v_prod) > 0.01:
        a.append(_achado("R07", f"Soma dos itens {_r(soma_itens)} difere de vProd {_r(doc.v_prod)}.", doc))

    simples = doc.emit_crt in (1, 2, 4)
    faltando_ibscbs = False
    for it in doc.itens:
        cfop = it.cfop
        if cfop and cfop[0] in "567" and doc.tp_nf == 0 or cfop and cfop[0] in "123" and doc.tp_nf == 1:
            a.append(_achado("R08", f"CFOP {cfop} em nota de {'entrada' if doc.tp_nf == 0 else 'saída'}.", doc, it))
        if cfop and doc.modelo == 65 and cfop[0] != "5":
            a.append(_achado("R10", f"CFOP {cfop} em NFC-e.", doc, it))
        elif cfop and ((cfop[0] in "15" and doc.id_dest == 2) or (cfop[0] in "26" and doc.id_dest == 1)):
            a.append(_achado("R09", f"CFOP {cfop} com idDest={doc.id_dest}.", doc, it))
        if simples and it.cst_icms and not it.csosn:
            a.append(_achado("R11", f"CRT {doc.emit_crt} com CST {it.cst_icms}.", doc, it))
        if doc.emit_crt == 3 and it.csosn:
            a.append(_achado("R11", f"CRT 3 com CSOSN {it.csosn}.", doc, it))
        if doc.tp_nf == 1 and cfop[1:] in ("102", "108"):
            if it.csosn == "500" or it.cst_icms == "60":
                a.append(_achado("R12", f"{'CSOSN ' + it.csosn if it.csosn else 'CST ' + it.cst_icms} com CFOP {cfop}.", doc, it))
        if doc.tp_nf == 1 and cfop[1:] == "405" and (it.csosn in ("101", "102", "103") or it.cst_icms in ("00", "20")):
            a.append(_achado("R12", f"CFOP {cfop} (ST) com {'CSOSN ' + it.csosn if it.csosn else 'CST ' + it.cst_icms}.", doc, it))
        eh_st = (it.csosn in CSOSN_ST) or (it.cst_icms in CST_ST)
        if eh_st and not it.cest:
            a.append(_achado("R13", f"{'CSOSN ' + it.csosn if it.csosn else 'CST ' + it.cst_icms} sem CEST.", doc, it))
        if not (len(it.ncm) == 8 and it.ncm.isdigit()) or it.ncm == "00000000":
            a.append(_achado("R14", f"NCM '{it.ncm}'.", doc, it))
        if it.c_ean and it.c_ean.upper() != "SEM GTIN" and not gtin_valido(it.c_ean):
            a.append(_achado("R15", f"GTIN {it.c_ean}.", doc, it))
        # Reforma tributária (2026)
        if doc.emit_crt == 3 and doc.emissao.year >= 2026:
            if not it.ibscbs:
                faltando_ibscbs = True
            else:
                if doc.emissao.year == 2026:
                    p_ibs = round(it.p_ibs_uf + it.p_ibs_mun, 4)
                    if abs(it.p_cbs - 0.9) > 0.0001 or abs(p_ibs - 0.1) > 0.0001:
                        a.append(_achado("R17", f"pCBS {it.p_cbs:.4f}% e pIBS {p_ibs:.4f}%.", doc, it))
                if it.p_cbs and abs(round(it.v_bc_ibscbs * it.p_cbs / 100, 2) - it.v_cbs) > 0.01:
                    a.append(_achado("R18", f"vCBS {_r(it.v_cbs)}; esperado {_r(it.v_bc_ibscbs * it.p_cbs / 100)}.", doc, it))
    if faltando_ibscbs:
        a.append(_achado("R16", f"{doc.emit_nome} (CRT 3) emitiu sem IBSCBS em {doc.emissao:%d/%m/%Y}.", doc))
    return a


def validar_cruzado(lote: Lote, cnpj_loja: Optional[str]) -> list[Achado]:
    """Regras que olham o conjunto: mesmo produto com classificações diferentes nas vendas da loja."""
    achados: list[Achado] = []
    variantes: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    nomes: dict[str, str] = {}
    for doc in lote.documentos:
        if doc.tp_nf != 1 or doc.cancelada or (cnpj_loja and doc.emit_cnpj != cnpj_loja):
            continue
        for it in doc.itens:
            k = it.chave_produto
            nomes.setdefault(k, f"{it.c_prod} {it.x_prod}")
            variantes[k]["ncm"][it.ncm] += 1
            variantes[k]["icms"][it.csosn and f"CSOSN {it.csosn}" or f"CST {it.cst_icms}"] += 1
    for k, v in variantes.items():
        if len(v["ncm"]) > 1:
            detalhe = ", ".join(f"{ncm} ({n}x)" for ncm, n in v["ncm"].most_common())
            achados.append(_geral("R19", f"NCMs usados: {detalhe}.", produto=nomes[k]))
        if len(v["icms"]) > 1:
            detalhe = ", ".join(f"{t} ({n}x)" for t, n in v["icms"].most_common())
            achados.append(_geral("R20", f"Tributações usadas: {detalhe}.", produto=nomes[k]))
    return achados


def _geral(regra: str, mensagem: str, produto: Optional[str] = None) -> Achado:
    titulo, severidade, sugestao = REGRAS[regra]
    return Achado(regra, severidade, titulo, mensagem, sugestao, produto=produto)


def validar(lote: Lote) -> list[Achado]:
    achados: list[Achado] = []
    for doc in lote.documentos:
        achados += validar_documento(doc)
    homolog = [d for d in lote.documentos if d.tp_amb == 2]
    if homolog:
        achados.append(_geral("R06", f"{len(homolog)} de {len(lote.documentos)} documentos."))
    simples_2026 = {d.emit_nome for d in lote.documentos if d.emit_crt in (1, 2, 4) and d.emissao.year == 2026}
    if simples_2026:
        achados.append(_geral("R21", f"Emitentes do Simples: {', '.join(sorted(simples_2026))}."))
    achados += validar_cruzado(lote, cnpj_da_loja(lote))
    return achados
