"""Análises gerenciais a partir dos XMLs: faturamento, curva ABC, margem por NCM, CFOPs, cancelamentos."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .leitor import Lote
from .validacoes import Achado, cnpj_da_loja, validar

CFOP_DESCRICAO = {
    "1101": "Compra para industrialização", "1102": "Compra para comercialização", "1202": "Devolução de venda",
    "1403": "Compra p/ comercialização com ST", "1411": "Devolução de venda com ST",
    "2101": "Compra para industrialização (interestadual)", "2102": "Compra para comercialização (interestadual)",
    "2403": "Compra p/ comercialização com ST (interestadual)",
    "5101": "Venda de produção do estabelecimento", "5102": "Venda de mercadoria adquirida de terceiros",
    "5202": "Devolução de compra", "5401": "Venda de produção com ST (substituto)", "5403": "Venda com ST (substituto)",
    "5405": "Venda com ST já retida (substituído)", "5656": "Venda de combustível a consumidor final",
    "5929": "Lançamento relativo a cupom fiscal", "5949": "Outra saída não especificada",
    "6101": "Venda de produção (interestadual)", "6102": "Venda de mercadoria de terceiros (interestadual)",
    "6107": "Venda de produção a não contribuinte (interestadual)",
    "6108": "Venda de mercadoria a não contribuinte (interestadual)", "6401": "Venda de produção com ST (interestadual)",
    "6403": "Venda com ST (interestadual)", "6404": "Venda com ST já retida (interestadual)",
}

TPAG = {"01": "Dinheiro", "02": "Cheque", "03": "Cartão de crédito", "04": "Cartão de débito", "05": "Crédito loja",
        "10": "Vale alimentação", "11": "Vale refeição", "15": "Boleto", "16": "Depósito", "17": "Pix",
        "18": "Transferência", "90": "Sem pagamento", "99": "Outros"}


@dataclass
class Resultado:
    cnpj_loja: Optional[str]
    nome_loja: Optional[str]
    periodo: tuple[Optional[str], Optional[str]]
    documentos: pd.DataFrame
    itens: pd.DataFrame
    resumo: dict
    abc: pd.DataFrame
    margem_ncm: pd.DataFrame
    cfops: pd.DataFrame
    canceladas: pd.DataFrame
    vendas_dia: pd.DataFrame
    vendas_hora: pd.DataFrame
    pagamentos: pd.DataFrame
    achados: list[Achado] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)

    @property
    def achados_df(self) -> pd.DataFrame:
        cols = ["severidade", "regra", "titulo", "documento", "item", "produto", "mensagem", "sugestao", "chave"]
        return pd.DataFrame([a.dict() for a in self.achados], columns=cols)


def _frames(lote: Lote) -> tuple[pd.DataFrame, pd.DataFrame]:
    docs = pd.DataFrame([{
        "chave": d.chave, "tipo": d.tipo, "modelo": d.modelo, "serie": d.serie, "numero": d.numero, "emissao": d.emissao,
        "tp_nf": d.tp_nf, "emit_cnpj": d.emit_cnpj, "emit_nome": d.emit_nome, "emit_uf": d.emit_uf, "emit_crt": d.emit_crt,
        "dest_doc": d.dest_doc, "dest_uf": d.dest_uf, "v_prod": d.v_prod, "v_desc": d.v_desc, "v_frete": d.v_frete,
        "v_st": d.v_st, "v_nf": d.v_nf, "cancelada": d.cancelada, "autorizada": d.autorizada, "arquivo": d.arquivo,
        "pagamentos": d.pagamentos,
    } for d in lote.documentos])
    itens = pd.DataFrame([{
        "chave": i.chave, "produto": i.chave_produto, "c_prod": i.c_prod, "gtin": i.c_ean, "descricao": i.x_prod,
        "ncm": i.ncm, "cest": i.cest, "cfop": i.cfop, "csosn": i.csosn, "cst": i.cst_icms, "qtd": i.q_com,
        "v_prod": i.v_prod, "v_desc": i.v_desc, "v_frete": i.v_frete, "v_liquido": i.v_liquido, "v_icms_st": i.v_icms_st,
        "ibscbs": i.ibscbs, "v_ibs": i.v_ibs, "v_cbs": i.v_cbs,
    } for d in lote.documentos for i in d.itens])
    if not itens.empty:
        itens = itens.merge(docs[["chave", "tipo", "tp_nf", "emit_cnpj", "emissao", "cancelada", "emit_nome", "dest_doc"]],
                            on="chave", how="left")
    return docs, itens


def _curva_abc(vendas: pd.DataFrame) -> pd.DataFrame:
    if vendas.empty:
        return pd.DataFrame(columns=["produto", "descricao", "qtd", "receita", "participacao", "acumulado", "classe"])
    abc = (vendas.groupby("produto")
           .agg(descricao=("descricao", "first"), ncm=("ncm", "first"), qtd=("qtd", "sum"), receita=("v_liquido", "sum"))
           .sort_values("receita", ascending=False).reset_index())
    total = abc["receita"].sum()
    abc["participacao"] = abc["receita"] / total
    abc["acumulado"] = abc["participacao"].cumsum()
    # Classe pelo acumulado ANTES do item: o item que cruza 80% ainda é A
    anterior = abc["acumulado"] - abc["participacao"]
    abc["classe"] = ["A" if a < 0.80 else "B" if a < 0.95 else "C" for a in anterior]
    return abc


def _custos(itens: pd.DataFrame, cnpj_loja: Optional[str]) -> pd.DataFrame:
    """Custo médio ponderado de entrada por produto (GTIN): (vProd − vDesc + vFrete + vST) / quantidade."""
    entradas = itens[(itens["dest_doc"] == cnpj_loja) & (~itens["cancelada"])] if cnpj_loja else itens.iloc[0:0]
    if entradas.empty:
        return pd.DataFrame(columns=["produto", "custo_unitario"])
    entradas = entradas.assign(custo_total=entradas["v_liquido"] + entradas["v_frete"] + entradas["v_icms_st"])
    g = entradas.groupby("produto").agg(custo_total=("custo_total", "sum"), qtd=("qtd", "sum")).reset_index()
    g["custo_unitario"] = g["custo_total"] / g["qtd"]
    return g[["produto", "custo_unitario"]]


def _margem_ncm(vendas: pd.DataFrame, custos: pd.DataFrame) -> pd.DataFrame:
    if vendas.empty:
        return pd.DataFrame(columns=["ncm", "receita", "custo", "margem", "margem_pct", "cobertura_custo"])
    v = vendas.merge(custos, on="produto", how="left")
    v["custo"] = v["custo_unitario"] * v["qtd"]
    v["tem_custo"] = v["custo_unitario"].notna()
    g = v.groupby("ncm").apply(lambda x: pd.Series({
        "descricao": x["descricao"].mode().iat[0],
        "receita": x["v_liquido"].sum(),
        "receita_com_custo": x.loc[x["tem_custo"], "v_liquido"].sum(),
        "custo": x.loc[x["tem_custo"], "custo"].sum(),
    }), include_groups=False).reset_index()
    g["margem"] = g["receita_com_custo"] - g["custo"]
    g["margem_pct"] = (g["margem"] / g["receita_com_custo"]).where(g["receita_com_custo"] > 0)
    g["cobertura_custo"] = g["receita_com_custo"] / g["receita"]
    return g.sort_values("receita", ascending=False).drop(columns=["receita_com_custo"])


def analisar(lote: Lote, cnpj_loja: Optional[str] = None) -> Resultado:
    cnpj_loja = cnpj_loja or cnpj_da_loja(lote)
    docs, itens = _frames(lote)
    achados = validar(lote)
    vazio = pd.DataFrame()
    if docs.empty:
        return Resultado(cnpj_loja, None, (None, None), docs, itens, {}, vazio, vazio, vazio, vazio, vazio, vazio, vazio,
                         achados, lote.falhas)

    # Perspectiva da empresa analisada: nota de fornecedor (tpNF=1 para ele) é ENTRADA para a loja.
    docs["operacao"] = [
        "Saída" if (e == cnpj_loja and t == 1) else "Entrada" if (dd == cnpj_loja or (e == cnpj_loja and t == 0)) else "Terceiros"
        for e, t, dd in zip(docs["emit_cnpj"], docs["tp_nf"], docs["dest_doc"])]
    if not itens.empty:
        itens = itens.merge(docs[["chave", "operacao"]], on="chave", how="left")
    saidas_docs = docs[docs["operacao"] == "Saída"]
    validas = saidas_docs[~saidas_docs["cancelada"] & saidas_docs["autorizada"]]
    vendas = itens[(itens["tp_nf"] == 1) & (itens["emit_cnpj"] == cnpj_loja) & (~itens["cancelada"])
                   & itens["chave"].isin(validas["chave"])] if not itens.empty else itens
    nome_loja = saidas_docs["emit_nome"].iat[0] if not saidas_docs.empty else None

    receita_liquida = float(vendas["v_liquido"].sum()) if not vendas.empty else 0.0
    custos = _custos(itens, cnpj_loja)
    abc = _curva_abc(vendas)
    margem = _margem_ncm(vendas, custos)
    entradas = docs[docs["operacao"] == "Entrada"]

    resumo = {
        "documentos": int(len(docs)),
        "nfce_emitidas": int((saidas_docs["modelo"] == 65).sum()),
        "nfe_emitidas": int((saidas_docs["modelo"] == 55).sum()),
        "notas_de_entrada": int(len(entradas)),
        "canceladas": int(saidas_docs["cancelada"].sum()),
        "faturamento_bruto": round(float(validas["v_prod"].sum()), 2),
        "descontos": round(float(validas["v_desc"].sum()), 2),
        "receita_liquida": round(receita_liquida, 2),
        "frete_cobrado": round(float(validas["v_frete"].sum()), 2),
        "ticket_medio": round(float(validas["v_nf"].mean()), 2) if len(validas) else 0.0,
        "compras": round(float(entradas["v_nf"].sum()), 2),
        "itens_vendidos": round(float(vendas["qtd"].sum()), 0) if not vendas.empty else 0,
        "produtos_distintos": int(vendas["produto"].nunique()) if not vendas.empty else 0,
        "margem_bruta_estimada": round(float(margem["margem"].sum()), 2) if not margem.empty else None,
        "cobertura_custo": round(float((margem["cobertura_custo"] * margem["receita"]).sum() / margem["receita"].sum()), 4)
        if not margem.empty and margem["receita"].sum() else None,
        "vendas_interestaduais": int((validas["dest_uf"].notna() & (validas["dest_uf"] != validas["emit_uf"])).sum()),
        "erros": sum(1 for a in achados if a.severidade == "erro"),
        "avisos": sum(1 for a in achados if a.severidade == "aviso"),
    }

    cfops = (itens[~itens["cancelada"]].groupby(["operacao", "cfop"])
             .agg(itens=("cfop", "size"), valor=("v_liquido", "sum")).reset_index()
             .sort_values(["operacao", "valor"], ascending=[False, False]))
    cfops["descricao"] = cfops["cfop"].map(CFOP_DESCRICAO).fillna("—")
    cfops.loc[cfops["operacao"] == "Entrada", "descricao"] = "CFOP do fornecedor: " + cfops["descricao"]

    eventos = {e.chave: e for e in lote.eventos if e.tp_evento == "110111"}
    canc = saidas_docs[saidas_docs["cancelada"]].copy()
    canc["data_cancelamento"] = canc["chave"].map(lambda c: eventos[c].data if c in eventos else None)
    canc["justificativa"] = canc["chave"].map(lambda c: eventos[c].justificativa if c in eventos else None)
    canc["minutos_ate_cancelar"] = [
        round((dc - em).total_seconds() / 60, 1) if dc is not None else None
        for dc, em in zip(canc["data_cancelamento"], canc["emissao"])]
    canc = canc[["tipo", "serie", "numero", "emissao", "v_nf", "data_cancelamento", "minutos_ate_cancelar", "justificativa", "chave"]]

    por_dia = validas.assign(dia=validas["emissao"].map(lambda d: d.date()))
    vendas_dia = (por_dia.groupby(["dia", "tipo"])["v_nf"].sum().unstack(fill_value=0).reset_index())
    por_hora = validas.assign(hora=validas["emissao"].map(lambda d: d.hour))
    vendas_hora = por_hora.groupby("hora").agg(cupons=("chave", "size"), valor=("v_nf", "sum")).reset_index()

    pag = [(t, v) for lista in validas["pagamentos"] for t, v in lista]
    pagamentos = (pd.DataFrame(pag, columns=["tpag", "valor"]).groupby("tpag")["valor"].agg(["sum", "size"]).reset_index()
                  .rename(columns={"sum": "valor", "size": "quantidade"}).sort_values("valor", ascending=False))
    pagamentos["meio"] = pagamentos["tpag"].map(TPAG).fillna("Outro")

    inicio, fim = docs["emissao"].min(), docs["emissao"].max()
    return Resultado(
        cnpj_loja=cnpj_loja, nome_loja=nome_loja, periodo=(f"{inicio:%d/%m/%Y}", f"{fim:%d/%m/%Y}"), documentos=docs,
        itens=itens, resumo=resumo, abc=abc, margem_ncm=margem, cfops=cfops, canceladas=canc, vendas_dia=vendas_dia,
        vendas_hora=vendas_hora, pagamentos=pagamentos, achados=achados, falhas=lote.falhas,
    )
