"""Dashboard Streamlit do analisador.  Rode com:  python -m analisador dashboard"""

from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analisador.analises import analisar  # noqa: E402
from analisador.gerador import gerar_exemplos  # noqa: E402
from analisador.leitor import Lote, aplicar_eventos, ler, ler_bytes  # noqa: E402
from analisador.relatorio import ABC, SERIE, brl, gerar_html, gerar_pdf, pct  # noqa: E402

EXEMPLOS = Path(__file__).resolve().parents[1] / "exemplos" / "xml"
SEV = {"erro": "🔴 erro", "aviso": "🟡 aviso", "info": "🔵 info"}


def curto(v) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1_000_000:
        return f"R$ {v / 1_000_000:.2f} mi".replace(".", ",")
    if abs(v) >= 10_000:
        return f"R$ {v / 1_000:.1f} mil".replace(".", ",")
    return brl(v)

st.set_page_config(page_title="Analisador de XMLs fiscais", page_icon="🧾", layout="wide")


@st.cache_data(show_spinner="Lendo XMLs…")
def _ler_exemplos():
    if not any(EXEMPLOS.rglob("*.xml")):
        gerar_exemplos(EXEMPLOS)
    return analisar(ler(EXEMPLOS))


@st.cache_data(show_spinner="Lendo XMLs…")
def _ler_enviados(arquivos: list[tuple[str, bytes]], cnpj: str | None):
    lote = Lote()
    for nome, conteudo in arquivos:
        if nome.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
                for n in z.namelist():
                    if n.lower().endswith(".xml"):
                        ler_bytes(f"{nome}:{n}", z.read(n), lote)
        else:
            ler_bytes(nome, conteudo, lote)
    aplicar_eventos(lote)
    return analisar(lote, cnpj or None)


with st.sidebar:
    st.header("Origem dos XMLs")
    origem = st.radio("Dados", ["Exemplo (mês sintético)", "Enviar meus XMLs"], label_visibility="collapsed")
    cnpj = st.text_input("CNPJ da empresa (opcional)", help="Por padrão, é o emitente mais frequente das notas de saída.")
    enviados = None
    if origem == "Enviar meus XMLs":
        enviados = st.file_uploader("XMLs de NF-e/NFC-e, eventos ou um .zip", type=["xml", "zip"], accept_multiple_files=True)
        st.caption("Exporte a pasta de XMLs do Painel do Contador do Hiper (ou de qualquer ERP) e envie aqui.")

if origem == "Enviar meus XMLs":
    if not enviados:
        st.info("Envie XMLs ou um arquivo .zip na barra lateral.")
        st.stop()
    r = _ler_enviados([(f.name, f.getvalue()) for f in enviados], cnpj.strip())
else:
    r = _ler_exemplos()

if r.documentos.empty:
    st.warning("Nenhum documento fiscal válido encontrado.")
    st.stop()

s = r.resumo
st.title("Análise fiscal e de vendas")
st.caption(f"{r.nome_loja or 'Empresa'} · CNPJ {r.cnpj_loja} · {r.periodo[0]} a {r.periodo[1]} · {s['documentos']} documentos")
c = st.columns(3) + st.columns(3)
c[0].metric("Receita líquida", curto(s["receita_liquida"]), help=brl(s["receita_liquida"]))
c[1].metric("Ticket médio", curto(s["ticket_medio"]))
c[2].metric("Margem estimada", curto(s["margem_bruta_estimada"]), help=brl(s["margem_bruta_estimada"]))
c[3].metric("NFC-e / NF-e", f"{s['nfce_emitidas']} / {s['nfe_emitidas']}")
c[4].metric("Canceladas", s["canceladas"])
c[5].metric("Erros / avisos", f"{s['erros']} / {s['avisos']}")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "relatorio.pdf"
    gerar_pdf(r, pdf)
    d = st.columns(3)
    d[0].download_button("Relatório HTML", gerar_html(r), "relatorio.html", "text/html")
    d[1].download_button("Relatório PDF", pdf.read_bytes(), "relatorio.pdf", "application/pdf")
    d[2].download_button("Problemas (CSV)", r.achados_df.to_csv(sep=";", decimal=",", index=False).encode("utf-8-sig"),
                         "achados.csv", "text/csv")

abas = st.tabs(["Vendas", "Curva ABC", "Margem por NCM", "Problemas fiscais", "CFOP e pagamentos", "Canceladas", "Reforma tributária"])

with abas[0]:
    vd = r.vendas_dia.melt(id_vars="dia", var_name="Documento", value_name="Valor")
    vd["dia"] = vd["dia"].map(lambda d: d.strftime("%d/%m"))
    cores = alt.Scale(domain=["NFC-e", "NF-e"], range=SERIE[:2])
    st.subheader("Vendas por dia")
    st.altair_chart(alt.Chart(vd).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("dia:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)), y=alt.Y("Valor:Q", title="R$", stack=True),
        color=alt.Color("Documento:N", scale=cores, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("dia:O", title="Dia"), "Documento", alt.Tooltip("Valor:Q", format=",.2f")]),
        use_container_width=True)
    st.subheader("Vendas por hora")
    st.altair_chart(alt.Chart(r.vendas_hora).mark_bar(color=SERIE[0], cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("hora:O", title="Hora"), y=alt.Y("valor:Q", title="R$"),
        tooltip=["hora", "cupons", alt.Tooltip("valor:Q", format=",.2f")]), use_container_width=True)

with abas[1]:
    abc = r.abc.copy()
    repetidos = abc["descricao"].duplicated(keep=False)
    abc["rotulo"] = abc["descricao"].where(~repetidos, abc["descricao"] + " · " + abc["produto"].str[-6:])
    resumo = abc.groupby("classe").agg(produtos=("produto", "size"), receita=("receita", "sum")).reset_index()
    cols = st.columns(3)
    for i, row in resumo.iterrows():
        cols[i].metric(f"Classe {row['classe']} · {pct(row['receita'] / abc['receita'].sum())} da receita", f"{row['produtos']} produtos")
    st.altair_chart(alt.Chart(abc).mark_bar(cornerRadiusEnd=3).encode(
        y=alt.Y("rotulo:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)), x=alt.X("receita:Q", title="Receita (R$)"),
        color=alt.Color("classe:N", scale=alt.Scale(domain=list(ABC), range=list(ABC.values())), legend=alt.Legend(orient="top")),
        tooltip=["descricao", "produto", "qtd", alt.Tooltip("receita:Q", format=",.2f"), alt.Tooltip("acumulado:Q", format=".1%")]),
        use_container_width=True)
    st.dataframe(abc.drop(columns=["rotulo"]), use_container_width=True, hide_index=True)

with abas[2]:
    st.caption("Custo médio ponderado das notas de entrada do mesmo GTIN, com frete e ICMS-ST. Cobertura = parte da receita com custo conhecido.")
    st.dataframe(r.margem_ncm.style.format({"receita": brl, "custo": brl, "margem": brl, "margem_pct": pct, "cobertura_custo": pct}),
                 use_container_width=True, hide_index=True)

with abas[3]:
    df = r.achados_df
    filtro = st.multiselect("Severidade", ["erro", "aviso", "info"], default=["erro", "aviso"])
    df = df[df["severidade"].isin(filtro)]
    resumo = df.groupby(["severidade", "regra", "titulo"]).size().reset_index(name="ocorrências")
    resumo = resumo.sort_values(["severidade", "regra"], key=lambda c: c.map({"erro": 0, "aviso": 1, "info": 2}) if c.name == "severidade" else c)
    resumo["severidade"] = resumo["severidade"].map(SEV)
    st.dataframe(resumo, use_container_width=True, hide_index=True)
    ordem = {"erro": 0, "aviso": 1, "info": 2}
    for regra, g in sorted(df.groupby("regra"), key=lambda kv: (ordem[kv[1]["severidade"].iat[0]], kv[0])):
        with st.expander(f"{SEV[g['severidade'].iat[0]]} · {regra} · {g['titulo'].iat[0]} ({len(g)})"):
            st.write("**Como corrigir:**", g["sugestao"].iat[0])
            st.dataframe(g[["documento", "item", "produto", "mensagem", "chave"]], use_container_width=True, hide_index=True)

with abas[4]:
    st.subheader("CFOPs")
    st.dataframe(r.cfops, use_container_width=True, hide_index=True)
    st.subheader("Meios de pagamento")
    st.altair_chart(alt.Chart(r.pagamentos).mark_bar(color=SERIE[0], cornerRadiusEnd=3).encode(
        y=alt.Y("meio:N", sort="-x", title=None), x=alt.X("valor:Q", title="R$"),
        tooltip=["meio", "quantidade", alt.Tooltip("valor:Q", format=",.2f")]), use_container_width=True)

with abas[5]:
    st.dataframe(r.canceladas, use_container_width=True, hide_index=True)

with abas[6]:
    st.markdown("""
Em **2026** a NF-e/NFC-e passa a ter o grupo **IBSCBS** por item (NT 2025.002-RTC), com destaque **informativo**
de **CBS 0,9%** e **IBS 0,1%** (LC 214/2025). Para o **regime normal (CRT 3)** o grupo é exigido, com a rejeição em
produção suspensa até nova data; para o **Simples Nacional** a validação aguarda NT futura.
Confira sempre a versão vigente da NT.""")
    itens = r.itens.copy()
    if not itens.empty:
        docs = r.documentos[["chave", "emit_nome", "emit_crt"]]
        itens = itens.drop(columns=[c for c in ("emit_nome",) if c in itens.columns]).merge(docs, on="chave")
        quadro = itens.groupby(["emit_nome", "emit_crt"]).agg(itens=("ibscbs", "size"), com_ibscbs=("ibscbs", "sum"),
                                                                 v_cbs=("v_cbs", "sum"), v_ibs=("v_ibs", "sum")).reset_index()
        quadro["cobertura"] = quadro["com_ibscbs"] / quadro["itens"]
        st.dataframe(quadro.style.format({"v_cbs": brl, "v_ibs": brl, "cobertura": pct}), use_container_width=True, hide_index=True)
