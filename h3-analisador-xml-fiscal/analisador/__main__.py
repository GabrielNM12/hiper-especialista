"""Linha de comando.

    python -m analisador exemplos [pasta]                 gera um mês de XMLs sintéticos (com erros plantados)
    python -m analisador analisar <pasta|zip> [--saida relatorios] [--cnpj 00000000000000] [--sem-pdf]
    python -m analisador importar-hiper --url http://localhost:8000 --destino xml_hiper
                                                         baixa os XMLs das notas faturadas no mock do Hiper (H1)
    python -m analisador dashboard                        abre o dashboard (Streamlit)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)


def main() -> None:
    p = argparse.ArgumentParser(prog="analisador", description="Analisador de XMLs fiscais (NF-e / NFC-e)")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("exemplos")
    g.add_argument("pasta", nargs="?", default="exemplos/xml")
    a = sub.add_parser("analisar")
    a.add_argument("origem", nargs="+")
    a.add_argument("--saida", default="relatorios")
    a.add_argument("--cnpj")
    a.add_argument("--sem-pdf", action="store_true")
    h = sub.add_parser("importar-hiper")
    h.add_argument("--url", default="http://localhost:8000")
    h.add_argument("--destino", default="xml_hiper")
    sub.add_parser("dashboard")
    args = p.parse_args()

    if args.cmd == "exemplos":
        from .gerador import ERROS_PLANTADOS, gerar_exemplos
        n = gerar_exemplos(args.pasta)
        print(f"{n} arquivos gerados em {args.pasta}\nErros plantados:\n- " + "\n- ".join(ERROS_PLANTADOS))
    elif args.cmd == "analisar":
        from .analises import analisar
        from .leitor import ler
        from .relatorio import brl, exportar
        r = analisar(ler(args.origem), args.cnpj)
        if r.documentos.empty:
            sys.exit("Nenhum documento fiscal encontrado.")
        saidas = exportar(r, args.saida, pdf=not args.sem_pdf)
        s = r.resumo
        print(f"{r.nome_loja} · {r.periodo[0]} a {r.periodo[1]} · {s['documentos']} documentos")
        print(f"Receita líquida {brl(s['receita_liquida'])} · ticket médio {brl(s['ticket_medio'])} · "
              f"{s['canceladas']} canceladas · {s['erros']} erros e {s['avisos']} avisos fiscais")
        if r.falhas:
            print(f"{len(r.falhas)} arquivo(s) não lido(s); veja o relatório.")
        for nome, caminho in saidas.items():
            print(f"  {nome:<11} {caminho}")
    elif args.cmd == "importar-hiper":
        from .importar import importar_do_mock
        n = importar_do_mock(args.url, args.destino)
        print(f"{n} XML(s) salvos em {args.destino}")
    elif args.cmd == "dashboard":
        app = Path(__file__).with_name("dashboard.py")
        print("Dashboard em http://localhost:8501  (Ctrl+C para sair)")
        # headless evita a pergunta de e-mail que o Streamlit faz na primeira execução
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.headless=true",
                        "--browser.gatherUsageStats=false", "--server.port=8501"], check=False)


if __name__ == "__main__":
    main()
