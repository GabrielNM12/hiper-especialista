"""Gera o XML (nfeProc, layout 4.00) do documento fiscal emitido ao faturar um pedido.

Venda de loja virtual com entrega é feita por NF-e (modelo 55), indPres=2 (internet):
- destino em SC → operação interna, CFOP 5102;
- destino em outra UF para consumidor final não contribuinte → CFOP 6108, idDest=2.
O emitente do exemplo é do Simples Nacional (CRT=1), então os itens usam CSOSN 102.

É um XML de HOMOLOGAÇÃO simulado (tpAmb=2), sem assinatura digital: serve para
alimentar o H3 e mostrar o fluxo, não tem validade fiscal.
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .util_fiscal import gtin_valido, montar_chave, so_digitos, uf_do_ibge

if TYPE_CHECKING:
    from .estado import Estado, Pedido

NS = "http://www.portalfiscal.inf.br/nfe"
ET.register_namespace("", NS)

# Hiper idMeioDePagamento → tPag da NF-e
TPAG = {1: "01", 2: "02", 3: "90", 4: "03", 5: "04", 6: "05", 11: "10", 12: "17"}

CUF = {"SC": 42, "PR": 41, "RS": 43, "SP": 35}


def _sub(pai: ET.Element, tag: str, texto=None) -> ET.Element:
    el = ET.SubElement(pai, f"{{{NS}}}{tag}")
    if texto is not None:
        el.text = str(texto)
    return el


def _v(valor: float) -> str:
    return f"{valor:.2f}"


def gerar_nfe(estado: "Estado", pedido: "Pedido", numero: int) -> tuple[str, str, str]:
    from .estado import agora

    conta = estado.conta
    corpo = pedido.corpo
    emissao = agora()
    rng = random.Random(pedido.id)
    cnf = rng.randint(10_000_000, 99_999_999)
    uf_emit = conta["uf"]
    chave = montar_chave(CUF[uf_emit], emissao.strftime("%y%m"), conta["cnpj"], 55, 1, numero, cnf)

    entrega = corpo["enderecoDeEntrega"]
    uf_dest = uf_do_ibge(entrega["codigoIbge"]) or uf_emit
    interna = uf_dest == uf_emit
    cfop = "5102" if interna else "6108"

    nfe_proc = ET.Element(f"{{{NS}}}nfeProc", {"versao": "4.00"})
    nfe = _sub(nfe_proc, "NFe")
    inf = _sub(nfe, "infNFe")
    inf.set("Id", f"NFe{chave}")
    inf.set("versao", "4.00")

    ide = _sub(inf, "ide")
    for tag, val in [
        ("cUF", CUF[uf_emit]), ("cNF", f"{cnf:08d}"), ("natOp", "Venda de mercadoria"), ("mod", 55),
        ("serie", 1), ("nNF", numero), ("dhEmi", emissao.isoformat(timespec="seconds")), ("tpNF", 1),
        ("idDest", 1 if interna else 2), ("cMunFG", conta["codigoIbge"]), ("tpImp", 1), ("tpEmis", 1),
        ("cDV", chave[-1]), ("tpAmb", 2), ("finNFe", 1), ("indFinal", 1), ("indPres", 2), ("indIntermed", 1),
        ("procEmi", 0), ("verProc", "MockHiper 0.1"),
    ]:
        _sub(ide, tag, val)

    emit = _sub(inf, "emit")
    _sub(emit, "CNPJ", conta["cnpj"])
    _sub(emit, "xNome", conta["razaoSocial"])
    _sub(emit, "xFant", conta["nomeFantasia"])
    ender = _sub(emit, "enderEmit")
    for tag, val in [("xLgr", "Rua Henrique Lage"), ("nro", "500"), ("xBairro", "Centro"), ("cMun", conta["codigoIbge"]),
                     ("xMun", "Criciuma"), ("UF", uf_emit), ("CEP", "88801010"), ("cPais", 1058), ("xPais", "Brasil")]:
        _sub(ender, tag, val)
    _sub(emit, "IE", "261234560")
    _sub(emit, "CRT", 1)

    cli = corpo["cliente"]
    doc = so_digitos(cli["documento"])
    dest = _sub(inf, "dest")
    _sub(dest, "CPF" if len(doc) == 11 else "CNPJ", doc)
    _sub(dest, "xNome", "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL")
    end_dest = _sub(dest, "enderDest")
    for tag, val in [("xLgr", entrega["logradouro"]), ("nro", entrega["numero"]), ("xBairro", entrega["bairro"]),
                     ("cMun", entrega["codigoIbge"]), ("xMun", "Municipio"), ("UF", uf_dest),
                     ("CEP", so_digitos(entrega["cep"])), ("cPais", 1058), ("xPais", "Brasil")]:
        _sub(end_dest, tag, val)
    _sub(dest, "indIEDest", 9)
    if cli.get("email"):
        _sub(dest, "email", cli["email"])

    itens = corpo["itens"]
    frete_total = float(corpo.get("valorDoFrete") or 0)
    brutos = [round(float(i["precoUnitarioBruto"]) * float(i["quantidade"]), 2) for i in itens]
    soma_bruta = sum(brutos) or 1
    fretes = [round(frete_total * b / soma_bruta, 2) for b in brutos]
    if fretes:
        fretes[-1] = round(frete_total - sum(fretes[:-1]), 2)

    tot_prod = tot_desc = 0.0
    for n, (item, v_prod, v_frete) in enumerate(zip(itens, brutos, fretes), start=1):
        p = estado.produtos[item["produtoId"]]
        qtd = float(item["quantidade"])
        v_desc = round((float(item["precoUnitarioBruto"]) - float(item["precoUnitarioLiquido"])) * qtd, 2)
        tot_prod += v_prod
        tot_desc += v_desc
        det = _sub(inf, "det")
        det.set("nItem", str(n))
        prod = _sub(det, "prod")
        ean = p["codigoDeBarras"] if gtin_valido(p["codigoDeBarras"]) and not p["codigoDeBarras"].startswith("999") else "SEM GTIN"
        for tag, val in [
            ("cProd", f"{p['codigo']}" + (f"-{p['codigoDeBarras'][-4:]}" if p["paiId"] else "")),
            ("cEAN", ean), ("xProd", p["nome"]), ("NCM", so_digitos(p["ncm"])), ("CFOP", cfop), ("uCom", p["unidade"]),
            ("qCom", f"{qtd:.4f}"), ("vUnCom", f"{float(item['precoUnitarioBruto']):.10f}"), ("vProd", _v(v_prod)),
            ("cEANTrib", ean), ("uTrib", p["unidade"]), ("qTrib", f"{qtd:.4f}"),
            ("vUnTrib", f"{float(item['precoUnitarioBruto']):.10f}"),
        ]:
            _sub(prod, tag, val)
        if v_frete:
            _sub(prod, "vFrete", _v(v_frete))
        if v_desc:
            _sub(prod, "vDesc", _v(v_desc))
        _sub(prod, "indTot", 1)
        imposto = _sub(det, "imposto")
        icms = _sub(_sub(imposto, "ICMS"), "ICMSSN102")
        _sub(icms, "orig", 0)
        _sub(icms, "CSOSN", "102")
        pis = _sub(_sub(imposto, "PIS"), "PISOutr")
        for tag, val in [("CST", "49"), ("vBC", "0.00"), ("pPIS", "0.0000"), ("vPIS", "0.00")]:
            _sub(pis, tag, val)
        cofins = _sub(_sub(imposto, "COFINS"), "COFINSOutr")
        for tag, val in [("CST", "49"), ("vBC", "0.00"), ("pCOFINS", "0.0000"), ("vCOFINS", "0.00")]:
            _sub(cofins, tag, val)

    v_nf = round(tot_prod - tot_desc + frete_total, 2)
    total = _sub(_sub(inf, "total"), "ICMSTot")
    for tag, val in [("vBC", 0), ("vICMS", 0), ("vICMSDeson", 0), ("vFCP", 0), ("vBCST", 0), ("vST", 0), ("vFCPST", 0),
                     ("vFCPSTRet", 0), ("vProd", tot_prod), ("vFrete", frete_total), ("vSeg", 0), ("vDesc", tot_desc),
                     ("vII", 0), ("vIPI", 0), ("vIPIDevol", 0), ("vPIS", 0), ("vCOFINS", 0), ("vOutro", 0), ("vNF", v_nf)]:
        _sub(total, tag, _v(float(val)))

    transp = _sub(inf, "transp")
    _sub(transp, "modFrete", 0 if frete_total else 9)

    pag = _sub(inf, "pag")
    for m in corpo["meiosDePagamento"]:
        det_pag = _sub(pag, "detPag")
        _sub(det_pag, "indPag", 0 if m["idMeioDePagamento"] in (1, 5, 12) else 1)
        _sub(det_pag, "tPag", TPAG.get(m["idMeioDePagamento"], "99"))
        _sub(det_pag, "vPag", _v(float(m["valor"])))

    mk = corpo.get("marketplace") or corpo.get("Marketplace") or {}
    if mk:
        inter = _sub(inf, "infIntermed")
        _sub(inter, "CNPJ", so_digitos(mk.get("cnpj") or mk.get("Cnpj") or ""))
        _sub(inter, "idCadIntTran", mk.get("nome") or mk.get("Nome") or "")

    obs = corpo.get("observacaoDoPedidoDeVenda") or corpo.get("observacao")
    adic = _sub(inf, "infAdic")
    _sub(adic, "infCpl", f"Pedido {pedido.codigo} / loja virtual {corpo.get('numeroPedidoDeVenda') or '-'}"
                         + (f" | {obs}" if obs else ""))

    prot = _sub(_sub(nfe_proc, "protNFe", None), "infProt")
    nfe_proc.find(f"{{{NS}}}protNFe").set("versao", "4.00")
    for tag, val in [("tpAmb", 2), ("verAplic", "SVRS-MOCK"), ("chNFe", chave), ("dhRecbto", emissao.isoformat(timespec="seconds")),
                     ("nProt", f"342{numero:012d}"), ("digVal", "bW9jaw=="), ("cStat", 100), ("xMotivo", "Autorizado o uso da NF-e")]:
        _sub(prot, tag, val)

    xml = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(nfe_proc, encoding="unicode")
    return chave, "NF-e", xml
