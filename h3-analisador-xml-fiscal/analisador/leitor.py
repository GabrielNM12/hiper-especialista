"""Leitura de XMLs fiscais (NF-e modelo 55 e NFC-e modelo 65, layout 4.00) e eventos de cancelamento.

Aceita: nfeProc (nota + protocolo), NFe sem protocolo, procEventoNFe / evento, de pastas (recursivo) ou ZIPs.
O analisador não depende do nome do arquivo: identifica o tipo pelo conteúdo.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from lxml import etree

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True)


@dataclass
class Item:
    chave: str
    n_item: int
    c_prod: str
    c_ean: str
    x_prod: str
    ncm: str
    cest: Optional[str]
    cfop: str
    u_com: str
    q_com: float
    v_un_com: float
    v_prod: float
    v_desc: float
    v_frete: float
    origem: Optional[str]
    cst_icms: Optional[str]
    csosn: Optional[str]
    v_bc_icms: float
    p_icms: float
    v_icms: float
    v_icms_st: float
    cst_pis: Optional[str]
    cst_cofins: Optional[str]
    ibscbs: bool
    cst_ibscbs: Optional[str]
    c_class_trib: Optional[str]
    v_bc_ibscbs: float
    p_ibs_uf: float
    p_ibs_mun: float
    p_cbs: float
    v_ibs: float
    v_cbs: float

    @property
    def v_liquido(self) -> float:
        return round(self.v_prod - self.v_desc, 2)

    @property
    def chave_produto(self) -> str:
        """Identidade do produto entre notas: GTIN quando existe, senão o código interno."""
        if self.c_ean and self.c_ean.upper() != "SEM GTIN":
            return self.c_ean
        return f"cod:{self.c_prod}"


@dataclass
class Documento:
    arquivo: str
    chave: str
    modelo: int
    serie: int
    numero: int
    emissao: datetime
    tp_nf: int
    id_dest: int
    tp_amb: int
    nat_op: str
    ind_pres: Optional[int]
    emit_cnpj: str
    emit_nome: str
    emit_uf: str
    emit_crt: int
    dest_doc: Optional[str]
    dest_uf: Optional[str]
    v_prod: float
    v_desc: float
    v_frete: float
    v_st: float
    v_nf: float
    v_seg: float
    v_outro: float
    v_ipi: float
    c_stat: Optional[int]
    protocolo: Optional[str]
    pagamentos: list[tuple[str, float]] = field(default_factory=list)
    itens: list[Item] = field(default_factory=list)
    tem_total_ibscbs: bool = False
    cancelada: bool = False

    @property
    def autorizada(self) -> bool:
        return self.c_stat in (100, 150)

    @property
    def tipo(self) -> str:
        return "NFC-e" if self.modelo == 65 else "NF-e"


@dataclass
class Evento:
    arquivo: str
    chave: str
    tp_evento: str
    descricao: str
    data: Optional[datetime]
    c_stat: Optional[int]
    justificativa: Optional[str]


@dataclass
class Lote:
    documentos: list[Documento] = field(default_factory=list)
    eventos: list[Evento] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)


def _t(el, caminho: str) -> Optional[str]:
    if el is None:
        return None
    achado = el.find(caminho, NS)
    return achado.text.strip() if achado is not None and achado.text else None


def _f(el, caminho: str) -> float:
    valor = _t(el, caminho)
    try:
        return float(valor) if valor else 0.0
    except ValueError:
        return 0.0


def _i(el, caminho: str) -> Optional[int]:
    valor = _t(el, caminho)
    return int(valor) if valor and valor.lstrip("-").isdigit() else None


def _data(texto: Optional[str]) -> Optional[datetime]:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def _item(chave: str, det) -> Item:
    prod = det.find("n:prod", NS)
    imposto = det.find("n:imposto", NS)
    icms = imposto.find("n:ICMS", NS) if imposto is not None else None
    grupo = icms[0] if icms is not None and len(icms) else None
    pis = imposto.find("n:PIS", NS) if imposto is not None else None
    cofins = imposto.find("n:COFINS", NS) if imposto is not None else None
    ibs = imposto.find("n:IBSCBS", NS) if imposto is not None else None
    g_ibs = ibs.find("n:gIBSCBS", NS) if ibs is not None else None
    return Item(
        chave=chave,
        n_item=int(det.get("nItem", "0")),
        c_prod=_t(prod, "n:cProd") or "",
        c_ean=_t(prod, "n:cEAN") or "",
        x_prod=_t(prod, "n:xProd") or "",
        ncm=_t(prod, "n:NCM") or "",
        cest=_t(prod, "n:CEST"),
        cfop=_t(prod, "n:CFOP") or "",
        u_com=_t(prod, "n:uCom") or "",
        q_com=_f(prod, "n:qCom"),
        v_un_com=_f(prod, "n:vUnCom"),
        v_prod=_f(prod, "n:vProd"),
        v_desc=_f(prod, "n:vDesc"),
        v_frete=_f(prod, "n:vFrete"),
        origem=_t(grupo, "n:orig"),
        cst_icms=_t(grupo, "n:CST"),
        csosn=_t(grupo, "n:CSOSN"),
        v_bc_icms=_f(grupo, "n:vBC"),
        p_icms=_f(grupo, "n:pICMS"),
        v_icms=_f(grupo, "n:vICMS"),
        v_icms_st=_f(grupo, "n:vICMSST"),
        cst_pis=_t(pis[0], "n:CST") if pis is not None and len(pis) else None,
        cst_cofins=_t(cofins[0], "n:CST") if cofins is not None and len(cofins) else None,
        ibscbs=ibs is not None,
        cst_ibscbs=_t(ibs, "n:CST"),
        c_class_trib=_t(ibs, "n:cClassTrib"),
        v_bc_ibscbs=_f(g_ibs, "n:vBC"),
        p_ibs_uf=_f(g_ibs, "n:gIBSUF/n:pIBSUF"),
        p_ibs_mun=_f(g_ibs, "n:gIBSMun/n:pIBSMun"),
        p_cbs=_f(g_ibs, "n:gCBS/n:pCBS"),
        v_ibs=_f(g_ibs, "n:vIBS") or (_f(g_ibs, "n:gIBSUF/n:vIBSUF") + _f(g_ibs, "n:gIBSMun/n:vIBSMun")),
        v_cbs=_f(g_ibs, "n:gCBS/n:vCBS"),
    )


def _documento(arquivo: str, raiz) -> Optional[Documento]:
    nfe = raiz if etree.QName(raiz).localname == "NFe" else raiz.find("n:NFe", NS)
    if nfe is None:
        return None
    inf = nfe.find("n:infNFe", NS)
    ide, emit, dest = inf.find("n:ide", NS), inf.find("n:emit", NS), inf.find("n:dest", NS)
    tot = inf.find("n:total/n:ICMSTot", NS)
    prot = raiz.find("n:protNFe/n:infProt", NS)
    chave = (inf.get("Id") or "").replace("NFe", "") or (_t(prot, "n:chNFe") or "")
    dest_doc = (_t(dest, "n:CPF") or _t(dest, "n:CNPJ") or _t(dest, "n:idEstrangeiro")) if dest is not None else None
    doc = Documento(
        arquivo=arquivo,
        chave=chave,
        modelo=_i(ide, "n:mod") or 0,
        serie=_i(ide, "n:serie") or 0,
        numero=_i(ide, "n:nNF") or 0,
        emissao=_data(_t(ide, "n:dhEmi") or _t(ide, "n:dEmi")) or datetime.min,
        tp_nf=_i(ide, "n:tpNF") or 0,
        id_dest=_i(ide, "n:idDest") or 1,
        tp_amb=_i(ide, "n:tpAmb") or 1,
        nat_op=_t(ide, "n:natOp") or "",
        ind_pres=_i(ide, "n:indPres"),
        emit_cnpj=_t(emit, "n:CNPJ") or _t(emit, "n:CPF") or "",
        emit_nome=_t(emit, "n:xNome") or "",
        emit_uf=_t(emit, "n:enderEmit/n:UF") or "",
        emit_crt=_i(emit, "n:CRT") or 0,
        dest_doc=dest_doc,
        dest_uf=_t(dest, "n:enderDest/n:UF") if dest is not None else None,
        v_prod=_f(tot, "n:vProd"),
        v_desc=_f(tot, "n:vDesc"),
        v_frete=_f(tot, "n:vFrete"),
        v_st=_f(tot, "n:vST"),
        v_nf=_f(tot, "n:vNF"),
        v_seg=_f(tot, "n:vSeg"),
        v_outro=_f(tot, "n:vOutro"),
        v_ipi=_f(tot, "n:vIPI"),
        c_stat=_i(prot, "n:cStat"),
        protocolo=_t(prot, "n:nProt"),
        pagamentos=[(_t(p, "n:tPag") or "", _f(p, "n:vPag")) for p in inf.findall("n:pag/n:detPag", NS)],
        tem_total_ibscbs=inf.find("n:total/n:IBSCBSTot", NS) is not None,
    )
    doc.itens = [_item(chave, det) for det in inf.findall("n:det", NS)]
    if doc.c_stat in (101, 135, 151, 155):
        doc.cancelada = True
    return doc


def _evento(arquivo: str, raiz) -> Optional[Evento]:
    inf = raiz.find(".//n:evento/n:infEvento", NS) if etree.QName(raiz).localname != "evento" else raiz.find("n:infEvento", NS)
    if inf is None:
        return None
    ret = raiz.find(".//n:retEvento/n:infEvento", NS)
    return Evento(
        arquivo=arquivo,
        chave=_t(inf, "n:chNFe") or "",
        tp_evento=_t(inf, "n:tpEvento") or "",
        descricao=_t(inf, "n:detEvento/n:descEvento") or "",
        data=_data(_t(inf, "n:dhEvento")),
        c_stat=_i(ret, "n:cStat"),
        justificativa=_t(inf, "n:detEvento/n:xJust"),
    )


def ler_bytes(nome: str, conteudo: bytes, lote: Lote) -> None:
    try:
        raiz = etree.parse(io.BytesIO(conteudo), PARSER).getroot()
    except etree.XMLSyntaxError as exc:
        lote.falhas.append((nome, f"XML inválido: {exc}"))
        return
    local = etree.QName(raiz).localname
    if local in ("nfeProc", "NFe"):
        doc = _documento(nome, raiz)
        if doc:
            lote.documentos.append(doc)
            return
    if local in ("procEventoNFe", "evento", "envEvento"):
        ev = _evento(nome, raiz)
        if ev:
            lote.eventos.append(ev)
            return
    lote.falhas.append((nome, f"tipo de XML não suportado: {local}"))


def _arquivos(origem: Path) -> Iterator[tuple[str, bytes]]:
    if origem.is_file() and origem.suffix.lower() == ".zip":
        with zipfile.ZipFile(origem) as z:
            for info in z.infolist():
                if info.filename.lower().endswith(".xml") and info.file_size < 20 * 1024 * 1024:
                    yield f"{origem.name}:{info.filename}", z.read(info)
    elif origem.is_file():
        yield origem.name, origem.read_bytes()
    else:
        for caminho in sorted(origem.rglob("*")):
            if caminho.suffix.lower() in (".xml", ".zip"):
                yield from _arquivos(caminho)


def ler(origens: Iterable[str | Path] | str | Path) -> Lote:
    if isinstance(origens, (str, Path)):
        origens = [origens]
    lote = Lote()
    for origem in origens:
        for nome, conteudo in _arquivos(Path(origem)):
            ler_bytes(nome, conteudo, lote)
    aplicar_eventos(lote)
    return lote


def aplicar_eventos(lote: Lote) -> None:
    canceladas = {e.chave for e in lote.eventos if e.tp_evento == "110111" and (e.c_stat in (135, 136, 155, None))}
    for doc in lote.documentos:
        if doc.chave in canceladas:
            doc.cancelada = True
