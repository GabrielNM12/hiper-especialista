"""Roda os testes de todos os projetos (Windows, Mac e Linux).

    python scripts/testar_tudo.py
"""

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PROJETOS = ["hiper-sdk", "h1-mock-api-hiper", "h2-conector-loja-virtual", "h3-analisador-xml-fiscal", "h4-bot-whatsapp"]

total = 0
for projeto in PROJETOS:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore"],
                       cwd=RAIZ / projeto, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ultima = (r.stdout.strip().splitlines() or ["(sem saída)"])[-1]
    print(f"{projeto:<28} {ultima}")
    m = re.search(r"(\d+) passed", ultima)
    total += int(m.group(1)) if m else 0
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-2000:], sep="\n")
        sys.exit(f"\nFalhou em {projeto}.")
print(f"\nTotal: {total} testes passando")
