"""Gera um mês de XMLs fiscais sintéticos de uma loja de moda do Simples Nacional em Criciúma (SC).

Conteúdo (layout 4.00, ambiente de homologação, sem assinatura, sem valor fiscal):
- NFC-e (modelo 65) das vendas de balcão, com picos no fim de semana e no horário de almoço;
- NF-e (modelo 55) das vendas da loja virtual, incluindo vendas para outros estados (CFOP 6108);
- NF-e de entrada (compras) de quatro fornecedores, um deles do regime normal já com o grupo IBS/CBS de 2026;
- eventos de cancelamento (procEventoNFe, tpEvento 110111);
- ERROS PLANTADOS de propósito, para o analisador encontrar (lista em ERROS_PLANTADOS).
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .util_fiscal import gerar_cpf, gtin13, montar_chave

NS = "http://www.portalfiscal.inf.br/nfe"
ET.register_namespace("", NS)
FUSO = timezone(timedelta(hours=-3))

LOJA = {"cnpj": "45723174000110", "nome": "Moda Sul Comercio de Vestuario Ltda", "fantasia": "Moda Sul Criciuma",
        "ie": "261234560", "uf": "SC", "cmun": 4204608, "xmun": "Criciuma", "crt": 1}

FORNECEDORES = [
    {"cnpj": "10433218000193", "nome": "Malharia Vale do Itajai Ltda", "uf": "SC", "cmun": 4202404, "xmun": "Blumenau", "crt": 1, "ie": "254000111"},
    {"cnpj": "19600133000127", "nome": "Jeans Urbano Confeccoes Ltda", "uf": "PR", "cmun": 4106902, "xmun": "Curitiba", "crt": 1, "ie": "9012345678"},
    {"cnpj": "89083863000183", "nome": "Calcados Passo Firme Industria SA", "uf": "RS", "cmun": 4314902, "xmun": "Porto Alegre", "crt": 3, "ie": "0961234567"},
    {"cnpj": "33014556000196", "nome": "Aromas do Sul Cosmeticos Ltda", "uf": "SC", "cmun": 4205407, "xmun": "Florianopolis", "crt": 3, "ie": "255111222"},
]

# código, nome, NCM, CEST, preço venda, custo, fornecedor, peso de venda, substituição tributária
PRODUTOS = [
    ("3001", "Camiseta Basica Algodao", "61091000", None, 59.90, 22.40, 0, 30, False),
    ("3002", "Calca Jeans Skinny", "62046200", None, 189.90, 78.00, 1, 10, False),
    ("3003", "Vestido Midi Floral", "62044400", None, 249.90, 96.50, 0, 6, False),
    ("3004", "Tenis Casual Couro", "64039990", None, 299.90, 131.00, 2, 6, False),
    ("3005", "Jaqueta Corta-Vento", "62014000", None, 229.90, 92.00, 0, 4, False),
    ("3006", "Moletom Canguru", "61103000", None, 159.90, 61.30, 0, 9, False),
    ("3007", "Bermuda Sarja", "62034300", None, 119.90, 47.80, 1, 8, False),
    ("3008", "Bone Aba Curva", "65050090", None, 79.90, 28.00, 0, 7, False),
    ("3009", "Cinto de Couro Legitimo", "42033000", None, 99.90, 37.50, 2, 5, False),
    ("3010", "Kit Meias Cano Medio 3 Pares", "61159500", None, 39.90, 13.20, 0, 18, False),
    ("3011", "Bolsa Transversal Matelasse", "42022210", None, 189.90, 74.00, 2, 4, False),
    ("3012", "Oculos de Sol Retro", "90041000", None, 149.90, 41.00, 2, 3, False),
    ("3013", "Body Splash Floral 200ml", "33030020", "2000100", 49.90, 18.70, 3, 10, True),
    ("3014", "Hidratante Corporal 250ml", "33049910", "2002800", 39.90, 15.10, 3, 8, True),
]

GTIN = {cod: gtin13(f"78945610{int(cod) % 10000:04d}") for cod, *_ in PRODUTOS}

ERROS_PLANTADOS = [
    "Produto 3008 (Bone) vendido algumas vezes com NCM 65059000 em vez de 65050090 (cadastro duplicado).",
    "Produto 3013 (Body Splash, ST) vendido algumas vezes com CSOSN 102 e CFOP 5102 em vez de CSOSN 500 / CFOP 5405.",
    "Produto 3014 (Hidratante, ST) sem CEST em parte das vendas.",
    "Uma NFC-e com CFOP interestadual 6102 (NFC-e só admite operação interna).",
    "Uma NF-e com total vNF diferente da soma dos itens.",
    "Uma venda com GTIN de dígito verificador inválido.",
    "Fornecedor do regime normal (Calçados Passo Firme) sem o grupo IBS/CBS em uma das notas.",
]


@dataclass
class ItemVenda:
    codigo: str
    nome: str
    ncm: str
    cest: str | None
    preco: float
    qtd: int
    st: bool
    desconto: float = 0.0


def _sub(pai, tag, texto=None):
    el = ET.SubElement(pai, f"{{{NS}}}{tag}")
    if texto is not None:
        el.text = str(texto)
    return el


def _v(x: float) -> str:
    return f"{x:.2f}"


class Gerador:
    def __init__(self, ano: int = 2026, mes: int = 9, semente: int = 7):
        self.ano, self.mes = ano, mes
        self.rng = random.Random(semente)
        self.seq = {55: 0, 65: 0}
        self.arquivos: dict[str, str] = {}

    # ------------------------------------------------------------ montagem
    def _emissao(self, dia: date, hora_pico: bool = True) -> datetime:
        if hora_pico and self.rng.random() < 0.35:
            hora = self.rng.choice([12, 13])
        else:
            hora = self.rng.randint(9, 19)
        return datetime(dia.year, dia.month, dia.day, hora, self.rng.randint(0, 59), self.rng.randint(0, 59), tzinfo=FUSO)

    def _nota(self, *, modelo: int, emit: dict, dest: dict | None, itens: list[dict], dh: datetime, serie: int,
              numero: int, natop: str, tpnf: int = 1, ind_pres: int = 1, id_dest: int = 1, frete: float = 0.0,
              pagamentos: list[tuple[str, float | None]] | None = None, ibscbs: bool = False, vnf_errado: float = 0.0,
              com_protocolo: bool = True) -> tuple[str, str]:
        cnf = self.rng.randint(10_000_000, 99_999_999)
        cuf = {"SC": 42, "PR": 41, "RS": 43, "SP": 35}[emit["uf"]]
        chave = montar_chave(cuf, dh.strftime("%y%m"), emit["cnpj"], modelo, serie, numero, cnf)
        proc = ET.Element(f"{{{NS}}}nfeProc", {"versao": "4.00"})
        inf = _sub(_sub(proc, "NFe"), "infNFe")
        inf.set("Id", f"NFe{chave}")
        inf.set("versao", "4.00")
        ide = _sub(inf, "ide")
        for t, v in [("cUF", cuf), ("cNF", f"{cnf:08d}"), ("natOp", natop), ("mod", modelo), ("serie", serie),
                     ("nNF", numero), ("dhEmi", dh.isoformat()), ("tpNF", tpnf), ("idDest", id_dest),
                     ("cMunFG", emit["cmun"]), ("tpImp", 4 if modelo == 65 else 1), ("tpEmis", 1), ("cDV", chave[-1]),
                     ("tpAmb", 2), ("finNFe", 1), ("indFinal", 1 if dest is None or len(dest.get("doc", "")) == 11 else 0),
                     ("indPres", ind_pres), ("procEmi", 0), ("verProc", "GeradorSintetico 1.0")]:
            _sub(ide, t, v)
        e = _sub(inf, "emit")
        _sub(e, "CNPJ", emit["cnpj"])
        _sub(e, "xNome", emit["nome"])
        end = _sub(e, "enderEmit")
        for t, v in [("xLgr", "Rua Exemplo"), ("nro", "100"), ("xBairro", "Centro"), ("cMun", emit["cmun"]),
                     ("xMun", emit["xmun"]), ("UF", emit["uf"]), ("CEP", "88800000"), ("cPais", 1058), ("xPais", "Brasil")]:
            _sub(end, t, v)
        _sub(e, "IE", emit.get("ie", "ISENTO"))
        _sub(e, "CRT", emit["crt"])
        if dest:
            d = _sub(inf, "dest")
            _sub(d, "CPF" if len(dest["doc"]) == 11 else "CNPJ", dest["doc"])
            if modelo == 55:
                _sub(d, "xNome", "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL")
                de = _sub(d, "enderDest")
                for t, v in [("xLgr", "Rua Destino"), ("nro", "1"), ("xBairro", "Centro"), ("cMun", dest["cmun"]),
                             ("xMun", dest["xmun"]), ("UF", dest["uf"]), ("CEP", "88800000")]:
                    _sub(de, t, v)
                _sub(d, "indIEDest", 1 if len(dest["doc"]) == 14 else 9)
                if len(dest["doc"]) == 14:
                    _sub(d, "IE", dest.get("ie", ""))

        tot = {"vProd": 0.0, "vDesc": 0.0, "vFrete": 0.0, "vBC": 0.0, "vICMS": 0.0, "vST": 0.0, "vBCST": 0.0,
               "vIBS": 0.0, "vCBS": 0.0, "vBCIBSCBS": 0.0}
        brutos = [round(i["vUn"] * i["q"], 2) for i in itens]
        fretes = [round(frete * b / (sum(brutos) or 1), 2) for b in brutos]
        if fretes:
            fretes[-1] = round(frete - sum(fretes[:-1]), 2)
        for n, (it, v_prod, v_frete) in enumerate(zip(itens, brutos, fretes), start=1):
            det = _sub(inf, "det")
            det.set("nItem", str(n))
            p = _sub(det, "prod")
            for t, v in [("cProd", it["cProd"]), ("cEAN", it["cEAN"]), ("xProd", it["xProd"]), ("NCM", it["ncm"])]:
                _sub(p, t, v)
            if it.get("cest"):
                _sub(p, "CEST", it["cest"])
            for t, v in [("CFOP", it["cfop"]), ("uCom", "UN"), ("qCom", f"{it['q']:.4f}"), ("vUnCom", f"{it['vUn']:.10f}"),
                         ("vProd", _v(v_prod)), ("cEANTrib", it["cEAN"]), ("uTrib", "UN"), ("qTrib", f"{it['q']:.4f}"),
                         ("vUnTrib", f"{it['vUn']:.10f}")]:
                _sub(p, t, v)
            if v_frete:
                _sub(p, "vFrete", _v(v_frete))
            if it.get("desc"):
                _sub(p, "vDesc", _v(it["desc"]))
            _sub(p, "indTot", 1)
            imp = _sub(det, "imposto")
            icms = _sub(imp, "ICMS")
            if "csosn" in it:
                g = _sub(icms, f"ICMSSN{it['csosn']}")
                _sub(g, "orig", 0)
                _sub(g, "CSOSN", it["csosn"])
                if it["csosn"] == "500":
                    for t, v in [("vBCSTRet", _v(v_prod * 0.8)), ("pST", "17.0000"), ("vICMSSubstituto", "0.00"),
                                 ("vICMSSTRet", _v(v_prod * 0.8 * 0.17))]:
                        _sub(g, t, v)
            else:
                cst = it["cst"]
                g = _sub(icms, f"ICMS{cst}")
                _sub(g, "orig", 0)
                _sub(g, "CST", cst)
                _sub(g, "modBC", 3)
                base = round(v_prod - it.get("desc", 0) + v_frete, 2)
                aliq = it.get("picms", 17.0)
                _sub(g, "vBC", _v(base))
                _sub(g, "pICMS", f"{aliq:.4f}")
                _sub(g, "vICMS", _v(base * aliq / 100))
                tot["vBC"] += base
                tot["vICMS"] += round(base * aliq / 100, 2)
                if cst == "10":
                    bst = round(base * 1.7, 2)
                    vst = round(bst * 0.17 - base * aliq / 100, 2)
                    for t, v in [("modBCST", 4), ("pMVAST", "70.0000"), ("vBCST", _v(bst)), ("pICMSST", "17.0000"), ("vICMSST", _v(vst))]:
                        _sub(g, t, v)
                    tot["vBCST"] += bst
                    tot["vST"] += vst
            pis = _sub(_sub(imp, "PIS"), "PISOutr" if emit["crt"] == 1 else "PISAliq")
            cofins = _sub(_sub(imp, "COFINS"), "COFINSOutr" if emit["crt"] == 1 else "COFINSAliq")
            if emit["crt"] == 1:
                for g_, nome in ((pis, "PIS"), (cofins, "COFINS")):
                    for t, v in [("CST", "49"), ("vBC", "0.00"), (f"p{nome}", "0.0000"), (f"v{nome}", "0.00")]:
                        _sub(g_, t, v)
            else:
                base = round(v_prod - it.get("desc", 0), 2)
                for g_, nome, aliq in ((pis, "PIS", 1.65), (cofins, "COFINS", 7.6)):
                    for t, v in [("CST", "01"), ("vBC", _v(base)), (f"p{nome}", f"{aliq:.4f}"), (f"v{nome}", _v(base * aliq / 100))]:
                        _sub(g_, t, v)
            if ibscbs:
                base = round(v_prod - it.get("desc", 0), 2)
                g = _sub(imp, "IBSCBS")
                _sub(g, "CST", "000")
                _sub(g, "cClassTrib", "000001")
                gi = _sub(g, "gIBSCBS")
                _sub(gi, "vBC", _v(base))
                uf = _sub(gi, "gIBSUF")
                _sub(uf, "pIBSUF", "0.1000")
                _sub(uf, "vIBSUF", _v(base * 0.001))
                mun = _sub(gi, "gIBSMun")
                _sub(mun, "pIBSMun", "0.0000")
                _sub(mun, "vIBSMun", "0.00")
                _sub(gi, "vIBS", _v(base * 0.001))
                cbs = _sub(gi, "gCBS")
                _sub(cbs, "pCBS", "0.9000")
                _sub(cbs, "vCBS", _v(base * 0.009))
                tot["vBCIBSCBS"] += base
                tot["vIBS"] += round(base * 0.001, 2)
                tot["vCBS"] += round(base * 0.009, 2)
            tot["vProd"] += v_prod
            tot["vDesc"] += it.get("desc", 0)
            tot["vFrete"] += v_frete

        v_nf = round(tot["vProd"] - tot["vDesc"] + tot["vFrete"] + tot["vST"] + vnf_errado, 2)
        t = _sub(_sub(inf, "total"), "ICMSTot")
        for tag in ("vBC", "vICMS"):
            _sub(t, tag, _v(tot[tag]))
        _sub(t, "vICMSDeson", "0.00")
        _sub(t, "vFCP", "0.00")
        _sub(t, "vBCST", _v(tot["vBCST"]))
        _sub(t, "vST", _v(tot["vST"]))
        for tag in ("vFCPST", "vFCPSTRet"):
            _sub(t, tag, "0.00")
        _sub(t, "vProd", _v(tot["vProd"]))
        _sub(t, "vFrete", _v(tot["vFrete"]))
        _sub(t, "vSeg", "0.00")
        _sub(t, "vDesc", _v(tot["vDesc"]))
        for tag in ("vII", "vIPI", "vIPIDevol", "vPIS", "vCOFINS", "vOutro"):
            _sub(t, tag, "0.00")
        _sub(t, "vNF", _v(v_nf))
        if ibscbs:
            ti = _sub(inf.find(f"{{{NS}}}total"), "IBSCBSTot")
            _sub(ti, "vBCIBSCBS", _v(tot["vBCIBSCBS"]))
            gi = _sub(ti, "gIBS")
            _sub(gi, "vIBS", _v(tot["vIBS"]))
            gc = _sub(ti, "gCBS")
            _sub(gc, "vCBS", _v(tot["vCBS"]))
        _sub(_sub(inf, "transp"), "modFrete", 0 if frete else 9)
        pag = _sub(inf, "pag")
        for tpag, valor in (pagamentos or [("17", v_nf)]):
            dp = _sub(pag, "detPag")
            _sub(dp, "tPag", tpag)
            _sub(dp, "vPag", _v(v_nf if valor is None else valor))
        if com_protocolo:
            prot = _sub(proc, "protNFe")
            prot.set("versao", "4.00")
            ip = _sub(prot, "infProt")
            for tag, v in [("tpAmb", 2), ("verAplic", "SVRS"), ("chNFe", chave), ("dhRecbto", (dh + timedelta(seconds=3)).isoformat()),
                           ("nProt", f"342{numero:012d}"), ("digVal", "c2ludGV0aWNv"), ("cStat", 100),
                           ("xMotivo", "Autorizado o uso da NF-e")]:
                _sub(ip, tag, v)
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(proc, encoding="unicode")
        return chave, xml

    def _evento_cancelamento(self, chave: str, dh: datetime) -> str:
        proc = ET.Element(f"{{{NS}}}procEventoNFe", {"versao": "1.00"})
        ev = _sub(proc, "evento")
        ev.set("versao", "1.00")
        inf = _sub(ev, "infEvento")
        inf.set("Id", f"ID110111{chave}01")
        for t, v in [("cOrgao", 42), ("tpAmb", 2), ("CNPJ", LOJA["cnpj"]), ("chNFe", chave), ("dhEvento", dh.isoformat()),
                     ("tpEvento", "110111"), ("nSeqEvento", 1), ("verEvento", "1.00")]:
            _sub(inf, t, v)
        det = _sub(inf, "detEvento")
        det.set("versao", "1.00")
        _sub(det, "descEvento", "Cancelamento")
        _sub(det, "nProt", f"342{int(chave[25:34]):012d}")
        _sub(det, "xJust", "Venda cancelada a pedido do cliente no caixa")
        ret = _sub(_sub(proc, "retEvento"), "infEvento")
        for t, v in [("tpAmb", 2), ("cStat", 135), ("xMotivo", "Evento registrado e vinculado a NF-e"), ("chNFe", chave),
                     ("tpEvento", "110111"), ("dhRegEvento", (dh + timedelta(seconds=2)).isoformat())]:
            _sub(ret, t, v)
        return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(proc, encoding="unicode")

    # --------------------------------------------------------------- itens
    def _item_venda(self, produto, qtd: int, *, cfop_base: str = "5", desconto: float = 0.0, erro: str | None = None) -> dict:
        cod, nome, ncm, cest, preco, _, _, _, st = produto
        it = {"cProd": cod, "cEAN": GTIN[cod], "xProd": nome, "ncm": ncm, "cest": cest, "q": qtd, "vUn": preco,
              "desc": round(desconto, 2)}
        if st:
            it.update(csosn="500", cfop=f"{cfop_base}405")
        else:
            it.update(csosn="102", cfop=f"{cfop_base}102")
        if cfop_base == "6" and not st:
            it["cfop"] = "6108"
        if erro == "ncm" and cod == "3008":
            it["ncm"] = "65059000"
        if erro == "st" and st:
            it.update(csosn="102", cfop="5102")
        if erro == "cest" and cod == "3014":
            it["cest"] = None
        if erro == "gtin":
            it["cEAN"] = it["cEAN"][:-1] + str((int(it["cEAN"][-1]) + 1) % 10)
        return it

    def _escolher_produtos(self) -> list:
        pesos = [p[7] for p in PRODUTOS]
        n = self.rng.choices([1, 2, 3], weights=[60, 30, 10])[0]
        escolhidos = []
        for _ in range(n):
            p = self.rng.choices(PRODUTOS, weights=pesos)[0]
            if p not in escolhidos:
                escolhidos.append(p)
        return escolhidos

    # -------------------------------------------------------------- cenário
    def gerar(self) -> dict[str, str]:
        inicio = date(self.ano, self.mes, 1)
        dias = [(inicio + timedelta(days=i)) for i in range(31) if (inicio + timedelta(days=i)).month == self.mes]
        planta = {"ncm": 6, "st": 4, "cest": 5}
        cancelar = []

        # compras no início do mês e no meio
        for dia in (dias[1], dias[14]):
            for idx, forn in enumerate(FORNECEDORES):
                itens_forn = [p for p in PRODUTOS if p[6] == idx]
                itens = []
                for p in itens_forn:
                    cod, nome, ncm, cest, _, custo, *_rest, st = p
                    interna = forn["uf"] == "SC"
                    if forn["crt"] == 1:
                        it = {"csosn": "101" if not st else "201", "cfop": ("5" if interna else "6") + "102"}
                    else:
                        it = {"cst": "10" if st else "00", "cfop": ("5" if interna else "6") + ("401" if st else "101"),
                              "picms": 17.0 if interna else 12.0}
                    it.update(cProd=f"F{idx}{cod}", cEAN=GTIN[cod], xProd=nome.upper(), ncm=ncm, cest=cest,
                              q=self.rng.choice([12, 18, 24, 36]), vUn=custo)
                    itens.append(it)
                self.seq[55] += 1
                sem_ibs = forn["cnpj"] == "89083863000183" and dia == dias[14]
                chave, xml = self._nota(modelo=55, emit=forn, dest={"doc": LOJA["cnpj"], "uf": LOJA["uf"], "cmun": LOJA["cmun"],
                                                                   "xmun": LOJA["xmun"], "ie": LOJA["ie"]},
                                        itens=itens, dh=self._emissao(dia, False), serie=1, numero=1000 + self.seq[55],
                                        natop="Venda de mercadoria", id_dest=1 if forn["uf"] == "SC" else 2, ind_pres=9,
                                        pagamentos=[("15", None)], ibscbs=forn["crt"] == 3 and not sem_ibs)
                self.arquivos[f"entradas/{chave}-nfe.xml"] = xml

        # vendas de balcão (NFC-e) e loja virtual (NF-e)
        for dia in dias:
            fim_de_semana = dia.weekday() >= 5
            qtd_cupons = self.rng.randint(22, 34) if fim_de_semana else self.rng.randint(12, 22)
            for _ in range(qtd_cupons):
                erro = None
                if planta["ncm"] and self.rng.random() < 0.04:
                    erro, planta["ncm"] = "ncm", planta["ncm"] - 1
                elif planta["st"] and self.rng.random() < 0.03:
                    erro, planta["st"] = "st", planta["st"] - 1
                elif planta["cest"] and self.rng.random() < 0.03:
                    erro, planta["cest"] = "cest", planta["cest"] - 1
                produtos = self._escolher_produtos()
                if erro == "ncm":
                    produtos = [PRODUTOS[7]]
                elif erro in ("st", "cest"):
                    produtos = [PRODUTOS[12] if erro == "st" else PRODUTOS[13]]
                itens = []
                for p in produtos:
                    q = self.rng.choices([1, 2, 3], weights=[80, 15, 5])[0]
                    desconto = round(p[4] * q * 0.1, 2) if self.rng.random() < 0.15 else 0.0
                    itens.append(self._item_venda(p, q, desconto=desconto, erro=erro))
                total = round(sum(i["vUn"] * i["q"] - i["desc"] for i in itens), 2)
                tpag = self.rng.choices(["17", "03", "04", "01"], weights=[45, 25, 20, 10])[0]
                self.seq[65] += 1
                dest = {"doc": gerar_cpf(self.rng)} if self.rng.random() < 0.3 else None
                chave, xml = self._nota(modelo=65, emit=LOJA, dest=dest, itens=itens, dh=self._emissao(dia), serie=1,
                                        numero=self.seq[65], natop="Venda", ind_pres=1, pagamentos=[(tpag, total)])
                self.arquivos[f"saidas/{chave}-nfce.xml"] = xml
                if self.rng.random() < 0.02:
                    cancelar.append((chave, xml))

            # 1 a 2 vendas online por dia
            for _ in range(self.rng.randint(1, 2)):
                destinos = [("SC", 4202404, "Blumenau"), ("SC", 4205407, "Florianopolis"), ("PR", 4106902, "Curitiba"),
                            ("RS", 4314902, "Porto Alegre"), ("SP", 3550308, "Sao Paulo")]
                uf, cmun, xmun = self.rng.choices(destinos, weights=[30, 25, 20, 15, 10])[0]
                interna = uf == "SC"
                itens = [self._item_venda(p, 1, cfop_base="5" if interna else "6") for p in self._escolher_produtos()
                         if not p[8] or interna]  # ST interestadual fica fora do exemplo
                if not itens:
                    continue
                frete = 19.90
                total = round(sum(i["vUn"] * i["q"] for i in itens) + frete, 2)
                self.seq[55] += 1
                chave, xml = self._nota(modelo=55, emit=LOJA, dest={"doc": gerar_cpf(self.rng), "uf": uf, "cmun": cmun, "xmun": xmun},
                                        itens=itens, dh=self._emissao(dia, False), serie=2, numero=self.seq[55], natop="Venda pela internet",
                                        ind_pres=2, id_dest=1 if interna else 2, frete=frete, pagamentos=[("17", total)])
                self.arquivos[f"saidas/{chave}-nfe.xml"] = xml

        # erros pontuais
        dia = dias[20]
        p = PRODUTOS[0]
        it = self._item_venda(p, 1)
        it["cfop"] = "6102"
        self.seq[65] += 1
        chave, xml = self._nota(modelo=65, emit=LOJA, dest=None, itens=[it], dh=self._emissao(dia), serie=1, numero=self.seq[65],
                                natop="Venda", pagamentos=[("17", p[4])])
        self.arquivos[f"saidas/{chave}-nfce.xml"] = xml
        it = self._item_venda(PRODUTOS[1], 1)
        self.seq[55] += 1
        chave, xml = self._nota(modelo=55, emit=LOJA, dest={"doc": gerar_cpf(self.rng), "uf": "SC", "cmun": 4204608, "xmun": "Criciuma"},
                                itens=[it], dh=self._emissao(dia), serie=2, numero=self.seq[55], natop="Venda", ind_pres=2,
                                pagamentos=[("17", 199.90)], vnf_errado=10.0)
        self.arquivos[f"saidas/{chave}-nfe.xml"] = xml
        it = self._item_venda(PRODUTOS[9], 2, erro="gtin")
        self.seq[65] += 1
        chave, xml = self._nota(modelo=65, emit=LOJA, dest=None, itens=[it], dh=self._emissao(dias[22]), serie=1,
                                numero=self.seq[65], natop="Venda", pagamentos=[("01", 79.80)])
        self.arquivos[f"saidas/{chave}-nfce.xml"] = xml

        for chave, xml in cancelar:
            emissao = datetime.fromisoformat(xml.split("<dhEmi>")[1].split("<")[0])
            self.arquivos[f"eventos/{chave}-cancelamento.xml"] = self._evento_cancelamento(
                chave, emissao + timedelta(minutes=self.rng.randint(2, 15)))
        return self.arquivos


def gerar_exemplos(destino: str | Path, ano: int = 2026, mes: int = 9, semente: int = 7) -> int:
    destino = Path(destino)
    arquivos = Gerador(ano, mes, semente).gerar()
    for nome, xml in arquivos.items():
        caminho = destino / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(xml, encoding="utf-8")
    (destino / "LEIA-ME.txt").write_text(
        "XMLs SINTÉTICOS de homologação, sem valor fiscal, gerados por analisador.gerador.\n\nErros plantados:\n- "
        + "\n- ".join(ERROS_PLANTADOS) + "\n", encoding="utf-8")
    return len(arquivos)
