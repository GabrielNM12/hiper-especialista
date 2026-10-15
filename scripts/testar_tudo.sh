#!/usr/bin/env bash
# Roda os testes de todos os projetos. Uso: ./scripts/testar_tudo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
total=0
for projeto in hiper-sdk h1-mock-api-hiper h2-conector-loja-virtual h3-analisador-xml-fiscal h4-bot-whatsapp; do
  echo "== $projeto"
  saida=$(cd "$projeto" && python -m pytest -q -p no:cacheprovider -W ignore 2>&1 | tail -1)
  echo "   $saida"
  n=$(echo "$saida" | grep -oE '^[0-9]+ passed' | grep -oE '[0-9]+' || echo 0)
  total=$((total + n))
  echo "$saida" | grep -q failed && { echo "Falhou em $projeto"; exit 1; }
done
echo "Total: $total testes passando"
