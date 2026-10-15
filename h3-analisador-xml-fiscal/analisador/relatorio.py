"""Relatórios do analisador: HTML autocontido (gráficos SVG com tooltip, modo escuro), PDF e CSVs."""

from __future__ import annotations

import html
import io
from datetime import datetime
from pathlib import Path

import pandas as pd

from .analises import Resultado

# Paleta de referência (validada para daltonismo nas posições usadas): azul, laranja, água.
SERIE = ["#2a78d6", "#eb6834", "#1baf7a"]
SERIE_ESCURO = ["#3987e5", "#d95926", "#199e70"]
ABC = {"A": "#184f95", "B": "#3987e5", "C": "#86b6ef"}  # rampa ordinal do azul
STATUS = {"erro": "#d03b3b", "aviso": "#fab219", "info": "#2a78d6"}
ICONE = {"erro": "✕", "aviso": "!", "info": "i"}


def brl(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v, casas: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v * 100:.{casas}f}%".replace(".", ",")


def num(v) -> str:
    return f"{float(v):,.0f}".replace(",", ".")


e = html.escape


# ------------------------------------------------------------------ SVG
def svg_barras(categorias: list[str], series: list[tuple[str, list[float]]], *, empilhado: bool = True,
               altura: int = 220, formato=brl, rotulo_cada: int = 1) -> str:
    """Barras verticais (empilhadas ou simples), cores por papel (var(--s1..)), tooltip nativo por barra."""
    largura = 760
    m_esq, m_dir, m_top, m_base = 64, 8, 10, 26
    w, h = largura - m_esq - m_dir, altura - m_top - m_base
    totais = [sum(s[1][i] for s in series) if empilhado else max(s[1][i] for s in series) for i in range(len(categorias))]
    topo = max(totais or [1]) or 1
    passo = 10 ** max(0, len(str(int(topo))) - 1)
    topo = (int(topo / passo) + 1) * passo
    n = max(1, len(categorias))
    banda = w / n
    larg_barra = max(3.0, banda * 0.68)
    partes = [f'<svg viewBox="0 0 {largura} {altura}" role="img" class="grafico" preserveAspectRatio="xMidYMid meet">']
    for k in range(5):
        valor = topo * k / 4
        y = m_top + h - h * k / 4
        partes.append(f'<line x1="{m_esq}" x2="{largura - m_dir}" y1="{y:.1f}" y2="{y:.1f}" class="grade"/>')
        partes.append(f'<text x="{m_esq - 6}" y="{y + 4:.1f}" text-anchor="end" class="eixo">{e(num(valor))}</text>')
    for i, cat in enumerate(categorias):
        x = m_esq + banda * i + (banda - larg_barra) / 2
        base = m_top + h
        for j, (nome, valores) in enumerate(series):
            v = valores[i]
            if v <= 0:
                continue
            alt = h * v / topo
            y = base - alt
            topo_seg = j == len(series) - 1 or all(series[k][1][i] <= 0 for k in range(j + 1, len(series)))
            r = 3 if topo_seg else 0
            altura_seg = max(alt - (2 if (empilhado and not topo_seg) else 0), 0.5)
            partes.append(
                f'<path class="s{j + 1}" d="{_barra(x, y, larg_barra, altura_seg, r)}"><title>{e(cat)} · {e(nome)}: {e(formato(v))}</title></path>')
            if empilhado:
                base = y
        if i % rotulo_cada == 0:
            partes.append(f'<text x="{x + larg_barra / 2:.1f}" y="{altura - 8}" text-anchor="middle" class="eixo">{e(cat)}</text>')
    partes.append("</svg>")
    return "".join(partes)


def _barra(x, y, w, h, r) -> str:
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")


def barra_inline(valor: float, maximo: float, cor_var: str = "var(--s1)") -> str:
    largura = 0 if not maximo else max(2, 100 * valor / maximo)
    return f'<span class="mini"><span style="width:{largura:.1f}%;background:{cor_var}"></span></span>'


def legenda(series: list[str]) -> str:
    return '<div class="legenda">' + "".join(
        f'<span><i style="background:var(--s{i + 1})"></i>{e(n)}</span>' for i, n in enumerate(series)) + "</div>"


# ------------------------------------------------------------------ HTML
CSS = """
:root { --bg:#fcfcfb; --card:#ffffff; --tx:#0b0b0b; --tx2:#52514e; --mut:#7a7975; --ln:#e6e5e1; --grade:#ecebe7;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --erro:#d03b3b; --aviso:#fab219; --info:#2a78d6;
  --A:#184f95; --B:#3987e5; --C:#86b6ef; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { --bg:#1a1a19; --card:#222220; --tx:#f0efec; --tx2:#c3c2b7;
  --mut:#9a998f; --ln:#34332f; --grade:#2c2b28; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --info:#3987e5; --A:#6da7ec; --B:#3987e5; --C:#1c5cab; } }
:root[data-theme="dark"] { --bg:#1a1a19; --card:#222220; --tx:#f0efec; --tx2:#c3c2b7; --mut:#9a998f; --ln:#34332f; --grade:#2c2b28;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --info:#3987e5; --A:#6da7ec; --B:#3987e5; --C:#1c5cab; }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--tx); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif }
.pagina { max-width:1080px; margin:0 auto; padding:28px 16px 60px }
h1 { font-size:22px; margin:0 } h2 { font-size:16px; margin:34px 0 10px } .sub { color:var(--tx2); margin:4px 0 0 }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; margin-top:20px }
.kpi { background:var(--card); border:1px solid var(--ln); border-radius:10px; padding:12px 14px } .kpi span { color:var(--tx2); font-size:12px } .kpi b { display:block; font-size:18px; white-space:nowrap; margin-top:2px; font-variant-numeric:tabular-nums }
.card { background:var(--card); border:1px solid var(--ln); border-radius:10px; padding:14px 16px; overflow-x:auto }
table { border-collapse:collapse; width:100% } th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--ln); vertical-align:top; font-size:13px }
th { color:var(--tx2); font-weight:600; font-size:12px } td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }
.grafico { width:100%; height:auto; display:block } .grafico .grade { stroke:var(--grade) } .grafico .eixo { fill:var(--mut); font-size:11px }
.grafico .s1 { fill:var(--s1) } .grafico .s2 { fill:var(--s2) } .grafico .s3 { fill:var(--s3) } .grafico path:hover { opacity:.8 }
.legenda { display:flex; gap:16px; font-size:12px; color:var(--tx2); margin:0 0 6px } .legenda i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:-1px }
.mini { display:inline-block; width:120px; height:8px; background:var(--grade); border-radius:4px; overflow:hidden; vertical-align:middle; margin-right:8px } .mini span { display:block; height:100% }
.classe { display:inline-block; min-width:22px; text-align:center; border-radius:5px; color:#fff; font-weight:700; font-size:12px; padding:0 6px }
.sev { display:inline-flex; align-items:center; gap:6px; font-weight:600; font-size:12px; white-space:nowrap } .sev i { width:16px; height:16px; border-radius:50%; display:inline-grid; place-items:center; font-style:normal; font-size:10px; color:#fff }
.sev.erro i { background:var(--erro) } .sev.aviso i { background:var(--aviso); color:#000 } .sev.info i { background:var(--info) }
.mut { color:var(--mut) } .nota { font-size:12px; color:var(--tx2) } code { font-size:11px; word-break:break-all }
.regra { border-left:3px solid var(--ln); padding:8px 12px; margin:10px 0; background:var(--card); border-radius:0 8px 8px 0 }
.regra.erro { border-left-color:var(--erro) } .regra.aviso { border-left-color:var(--aviso) } .regra.info { border-left-color:var(--info) }
footer { margin-top:40px; color:var(--mut); font-size:12px }
@media print { .card, .kpi { break-inside:avoid } }
"""


def _tabela(df: pd.DataFrame, colunas: list[tuple[str, str, object]], limite: int | None = None) -> str:
    linhas = df.head(limite) if limite else df
    cab = "".join(f'<th class="{"n" if num_ else ""}">{e(t)}</th>' for t, _, num_ in [(c[0], c[1], c[2] is not None) for c in colunas])
    corpo = []
    for _, row in linhas.iterrows():
        tds = []
        for titulo, campo, fmt in colunas:
            valor = row[campo] if campo in row else ""
            if callable(fmt):
                tds.append(f'<td class="n">{fmt(valor)}</td>')
            else:
                tds.append(f"<td>{e(str(valor)) if valor is not None and not (isinstance(valor, float) and pd.isna(valor)) else '—'}</td>")
        corpo.append("<tr>" + "".join(tds) + "</tr>")
    return f'<table><thead><tr>{cab}</tr></thead><tbody>{"".join(corpo)}</tbody></table>'


def gerar_html(r: Resultado) -> str:
    s = r.resumo
    k = lambda t, v: f'<div class="kpi"><span>{e(t)}</span><b>{v}</b></div>'  # noqa: E731
    kpis = "".join([
        k("Receita líquida de vendas", brl(s["receita_liquida"])), k("Ticket médio", brl(s["ticket_medio"])),
        k("Margem bruta estimada", brl(s["margem_bruta_estimada"])),
        k("Documentos emitidos", f'{num(s["nfce_emitidas"])} NFC-e · {num(s["nfe_emitidas"])} NF-e'),
        k("Canceladas", num(s["canceladas"])), k("Erros · avisos fiscais", f'{s["erros"]} · {s["avisos"]}'),
    ])

    vd = r.vendas_dia
    cats = [d.strftime("%d") for d in vd["dia"]]
    series = [(t, list(vd[t])) for t in ("NFC-e", "NF-e") if t in vd.columns]
    graf_dia = legenda([n for n, _ in series]) + svg_barras(cats, series, empilhado=True)
    vh = r.vendas_hora
    graf_hora = svg_barras([f"{h}h" for h in vh["hora"]], [("Vendas", list(vh["valor"]))], empilhado=False, altura=190)

    abc = r.abc.copy()
    mx = abc["receita"].max() if not abc.empty else 0
    abc["barra"] = [barra_inline(v, mx, f"var(--{c})") for v, c in zip(abc["receita"], abc["classe"])]
    linhas_abc = "".join(
        f'<tr><td><span class="classe" style="background:var(--{c})">{c}</span></td><td>{e(d)}<div class="mut">{e(p)}</div></td>'
        f'<td class="n">{num(q)}</td><td>{b}</td><td class="n">{brl(v)}</td><td class="n">{pct(pp)}</td><td class="n">{pct(ac)}</td></tr>'
        for c, d, p, q, b, v, pp, ac in zip(abc["classe"], abc["descricao"], abc["produto"], abc["qtd"], abc["barra"],
                                            abc["receita"], abc["participacao"], abc["acumulado"]))
    resumo_abc = ""
    if not abc.empty:
        cont = abc.groupby("classe").agg(n=("produto", "size"), rec=("receita", "sum"))
        tot = cont["rec"].sum()
        resumo_abc = " · ".join(f"<b>{c}</b>: {int(cont.loc[c, 'n'])} produtos, {pct(cont.loc[c, 'rec'] / tot)} da receita"
                                for c in ("A", "B", "C") if c in cont.index)

    mg = r.margem_ncm
    linhas_mg = "".join(
        f'<tr><td><code>{e(n[:4] + "." + n[4:6] + "." + n[6:])}</code></td><td>{e(d)}</td><td class="n">{brl(rec)}</td>'
        f'<td class="n">{brl(c)}</td><td class="n">{barra_inline(max(m or 0, 0), 1, "var(--s1)")}{pct(m)}</td>'
        f'<td class="n">{pct(cob, 0)}</td></tr>'
        for n, d, rec, c, m, cob in zip(mg["ncm"], mg["descricao"], mg["receita"], mg["custo"], mg["margem_pct"], mg["cobertura_custo"]))

    cf = r.cfops
    linhas_cf = "".join(
        f'<tr><td>{e(o)}</td><td><code>{e(c)}</code></td><td>{e(d)}</td><td class="n">{num(i)}</td><td class="n">{brl(v)}</td></tr>'
        for o, c, d, i, v in zip(cf["operacao"], cf["cfop"], cf["descricao"], cf["itens"], cf["valor"]))

    pg = r.pagamentos
    mxp = pg["valor"].max() if not pg.empty else 0
    linhas_pg = "".join(
        f'<tr><td>{e(m)}</td><td class="n">{num(q)}</td><td class="n">{barra_inline(v, mxp)}{brl(v)}</td></tr>'
        for m, q, v in zip(pg["meio"], pg["quantidade"], pg["valor"]))

    cn = r.canceladas
    linhas_cn = "".join(
        f'<tr><td>{e(t)} {s_}/{n}</td><td>{em:%d/%m %H:%M}</td><td class="n">{brl(v)}</td><td class="n">{"—" if pd.isna(mi) else f"{mi:.0f} min"}</td>'
        f'<td class="mut">{e(j or "")}</td></tr>'
        for t, s_, n, em, v, mi, j in zip(cn["tipo"], cn["serie"], cn["numero"], cn["emissao"], cn["v_nf"],
                                          cn["minutos_ate_cancelar"], cn["justificativa"]))

    # achados agrupados por regra
    ordem = {"erro": 0, "aviso": 1, "info": 2}
    grupos: dict[str, list] = {}
    for a in sorted(r.achados, key=lambda a: (ordem[a.severidade], a.regra)):
        grupos.setdefault(a.regra, []).append(a)
    blocos = []
    for regra, lista in grupos.items():
        a0 = lista[0]
        def _origem(a):
            partes = [a.documento, f"item {a.item}" if a.item else None, a.produto]
            return " · ".join(e(x) for x in partes if x)
        exemplos = "".join(f"<li>{_origem(a) + ' — ' if _origem(a) else ''}{e(a.mensagem)}</li>" for a in lista[:6])
        mais = f'<li class="mut">… e mais {len(lista) - 6}</li>' if len(lista) > 6 else ""
        blocos.append(
            f'<div class="regra {a0.severidade}"><span class="sev {a0.severidade}"><i>{ICONE[a0.severidade]}</i>{a0.severidade}</span> '
            f'<b>{e(a0.titulo)}</b> <span class="mut">{regra} · {len(lista)} ocorrência(s)</span>'
            f'<ul class="nota">{exemplos}{mais}</ul><div class="nota"><b>Como corrigir:</b> {e(a0.sugestao)}</div></div>')

    falhas = "".join(f"<li><code>{e(n)}</code>: {e(m)}</li>" for n, m in r.falhas)
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Análise fiscal · {e(r.nome_loja or "")}</title><style>{CSS}</style></head><body><div class="pagina">
<h1>Análise fiscal e de vendas</h1>
<p class="sub">{e(r.nome_loja or "Empresa")} · CNPJ {e(_cnpj(r.cnpj_loja))} · {r.periodo[0]} a {r.periodo[1]} · {num(s["documentos"])} documentos lidos</p>
<div class="kpis">{kpis}</div>

<h2>Vendas por dia</h2><div class="card">{graf_dia}<p class="nota">Valor total das notas autorizadas e não canceladas. Passe o mouse nas barras para ver o valor.</p></div>
<h2>Vendas por hora do dia</h2><div class="card">{graf_hora}</div>

<h2>Curva ABC de produtos</h2><div class="card"><p class="nota">{resumo_abc}</p>
<table><thead><tr><th>Classe</th><th>Produto</th><th class="n">Qtd</th><th></th><th class="n">Receita</th><th class="n">Part.</th><th class="n">Acum.</th></tr></thead><tbody>{linhas_abc}</tbody></table></div>

<h2>Margem por NCM</h2><div class="card"><p class="nota">Custo = custo médio ponderado das notas de entrada do mesmo GTIN (inclui frete e ICMS-ST pagos). Cobertura = parte da receita cujo custo foi encontrado.</p>
<table><thead><tr><th>NCM</th><th>Produto principal</th><th class="n">Receita</th><th class="n">Custo</th><th class="n">Margem</th><th class="n">Cobertura</th></tr></thead><tbody>{linhas_mg}</tbody></table></div>

<h2>Problemas fiscais encontrados</h2>{"".join(blocos) or '<p class="nota">Nenhum problema encontrado.</p>'}

<h2>CFOPs utilizados</h2><div class="card"><table><thead><tr><th>Operação</th><th>CFOP</th><th>Descrição</th><th class="n">Itens</th><th class="n">Valor</th></tr></thead><tbody>{linhas_cf}</tbody></table></div>
<h2>Meios de pagamento</h2><div class="card"><table><thead><tr><th>Meio</th><th class="n">Pagamentos</th><th class="n">Valor</th></tr></thead><tbody>{linhas_pg}</tbody></table></div>
<h2>Notas canceladas</h2><div class="card"><table><thead><tr><th>Documento</th><th>Emissão</th><th class="n">Valor</th><th class="n">Cancelada após</th><th>Justificativa</th></tr></thead><tbody>{linhas_cn or '<tr><td colspan="5" class="mut">Nenhuma.</td></tr>'}</tbody></table></div>
{f'<h2>Arquivos não lidos</h2><ul class="nota">{falhas}</ul>' if falhas else ''}
<footer>Gerado em {datetime.now():%d/%m/%Y %H:%M} pelo Analisador de XMLs Fiscais. Checagens de consistência para apoio à gestão;
não substituem a validação da SEFAZ nem a análise do contador.</footer>
</div></body></html>"""


def _cnpj(c: str | None) -> str:
    if not c or len(c) != 14:
        return c or "—"
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


# ------------------------------------------------------------------- PDF
def _png_barras(categorias, series, empilhado=True, altura=2.4, horizontal=False, cores=None) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.2, altura), dpi=160)
    base = [0.0] * len(categorias)
    for j, (nome, valores) in enumerate(series):
        cor = (cores or SERIE)[j % len(cores or SERIE)]
        if horizontal:
            ax.barh(categorias, valores, color=cor, height=0.62, label=nome)
        else:
            ax.bar(categorias, valores, bottom=base if empilhado else None, color=cor, width=0.68, label=nome,
                   edgecolor="white", linewidth=1.0)
            if empilhado:
                base = [b + v for b, v in zip(base, valores)]
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.spines["left"].set_color("#cfcdc6")
    ax.spines["bottom"].set_color("#cfcdc6")
    ax.tick_params(colors="#52514e", labelsize=7)
    ax.grid(axis="x" if horizontal else "y", color="#ecebe7", linewidth=0.8)
    ax.set_axisbelow(True)
    fmt = lambda v, _: f"{v:,.0f}".replace(",", ".")  # noqa: E731
    (ax.xaxis if horizontal else ax.yaxis).set_major_formatter(matplotlib.ticker.FuncFormatter(fmt))
    if horizontal:
        ax.invert_yaxis()
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=7, loc="upper left", ncol=len(series))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def gerar_pdf(r: Resultado, caminho: str | Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    est = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=est["Heading1"], fontSize=16, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=est["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=6)
    p = ParagraphStyle("p", parent=est["BodyText"], fontSize=8.5, leading=11, textColor=colors.HexColor("#52514e"))
    cel = ParagraphStyle("cel", parent=p, fontSize=7.5, leading=9, textColor=colors.black)

    def tabela(dados, larguras, alinhar_dir=()):
        t = Table(dados, colWidths=larguras, repeatRows=1)
        estilo = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#52514e")),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e6e5e1")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for c in alinhar_dir:
            estilo.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(estilo))
        return t

    s = r.resumo
    hist = [Paragraph("Análise fiscal e de vendas", h1),
            Paragraph(f"{e(r.nome_loja or '')} · CNPJ {_cnpj(r.cnpj_loja)} · {r.periodo[0]} a {r.periodo[1]}", p),
            Spacer(1, 8)]
    kp = [["Receita líquida", "Ticket médio", "Margem estimada", "NFC-e / NF-e", "Canceladas", "Erros / avisos"],
          [brl(s["receita_liquida"]), brl(s["ticket_medio"]), brl(s["margem_bruta_estimada"]),
           f'{s["nfce_emitidas"]} / {s["nfe_emitidas"]}', str(s["canceladas"]), f'{s["erros"]} / {s["avisos"]}']]
    t = Table(kp, colWidths=[3 * cm] * 6)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica", 7), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#52514e")),
                           ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 10), ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e6e5e1")),
                           ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e6e5e1")), ("TOPPADDING", (0, 0), (-1, -1), 5)]))
    hist += [t, Paragraph("Vendas por dia", h2)]
    vd = r.vendas_dia
    series = [(c, list(vd[c])) for c in ("NFC-e", "NF-e") if c in vd.columns]
    hist.append(Image(io.BytesIO(_png_barras([d.strftime("%d") for d in vd["dia"]], series)), width=17 * cm, height=5.6 * cm))
    hist.append(Paragraph("Curva ABC", h2))
    abc = r.abc
    hist.append(tabela([["Classe", "Produto", "Qtd", "Receita", "Part.", "Acum."]] + [
        [c, Paragraph(e(d), cel), num(q), brl(v), pct(pp), pct(ac)]
        for c, d, q, v, pp, ac in zip(abc["classe"], abc["descricao"], abc["qtd"], abc["receita"], abc["participacao"], abc["acumulado"])],
        [1.3 * cm, 7.2 * cm, 1.5 * cm, 2.8 * cm, 1.7 * cm, 1.7 * cm], alinhar_dir=(2, 3, 4, 5)))
    hist.append(Paragraph("Margem por NCM", h2))
    mg = r.margem_ncm
    hist.append(tabela([["NCM", "Produto principal", "Receita", "Custo", "Margem", "Cobertura"]] + [
        [n, Paragraph(e(d), cel), brl(rec), brl(c), pct(m), pct(cob, 0)]
        for n, d, rec, c, m, cob in zip(mg["ncm"], mg["descricao"], mg["receita"], mg["custo"], mg["margem_pct"], mg["cobertura_custo"])],
        [2 * cm, 6.3 * cm, 2.6 * cm, 2.6 * cm, 1.6 * cm, 1.8 * cm], alinhar_dir=(2, 3, 4, 5)))
    hist.append(Paragraph("Problemas fiscais encontrados", h2))
    vistos: dict[str, list] = {}
    for a in r.achados:
        vistos.setdefault(a.regra, []).append(a)
    ordem = {"erro": 0, "aviso": 1, "info": 2}
    linhas = [["Severidade", "Regra", "Ocorr.", "Exemplo"]]
    for regra, lista in sorted(vistos.items(), key=lambda kv: (ordem[kv[1][0].severidade], kv[0])):
        a0 = lista[0]
        origem = " ".join(x for x in (a0.documento, a0.produto) if x)
        exemplo = f"{origem} — {a0.mensagem}" if origem else a0.mensagem
        linhas.append([a0.severidade.upper(), Paragraph(f"<b>{e(a0.titulo)}</b> ({regra})<br/>{e(a0.sugestao)}", cel),
                       str(len(lista)), Paragraph(e(exemplo), cel)])
    hist.append(tabela(linhas, [1.8 * cm, 8 * cm, 1.2 * cm, 6 * cm], alinhar_dir=(2,)))
    hist += [Spacer(1, 14), Paragraph("Checagens de consistência para apoio à gestão; não substituem a validação da SEFAZ "
                                      "nem a análise do contador.", p)]
    SimpleDocTemplate(str(caminho), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm,
                      bottomMargin=1.4 * cm, title="Análise fiscal e de vendas").build(hist)


# ------------------------------------------------------------------ CSV
def exportar(r: Resultado, pasta: str | Path, pdf: bool = True) -> dict[str, Path]:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    saidas = {"html": pasta / "relatorio.html"}
    saidas["html"].write_text(gerar_html(r), encoding="utf-8")
    if pdf:
        saidas["pdf"] = pasta / "relatorio.pdf"
        gerar_pdf(r, saidas["pdf"])
    # CSV no padrão do Excel brasileiro: ponto e vírgula, vírgula decimal, UTF-8 com BOM
    for nome, df in (("achados", r.achados_df), ("curva_abc", r.abc), ("margem_ncm", r.margem_ncm), ("cfops", r.cfops),
                     ("canceladas", r.canceladas)):
        caminho = pasta / f"{nome}.csv"
        df.to_csv(caminho, sep=";", decimal=",", index=False, encoding="utf-8-sig")
        saidas[nome] = caminho
    return saidas
