"""Compara o contrato salvo (contrato/contrato_oficial.json) com o Swagger oficial publicado pela Hiper.

Rode periodicamente (ou no CI) para descobrir se a Hiper adicionou/removeu rotas ou campos.
    python scripts/comparar_com_swagger_oficial.py
Sai com código 1 se houver diferença.
"""
import json
import sys
from pathlib import Path

import httpx

URL = "https://ms-ecommerce.hiper.com.br/swagger/v1/swagger.json"
CONTRATO = Path(__file__).resolve().parents[1] / "contrato" / "contrato_oficial.json"


def main() -> int:
    try:
        oficial = httpx.get(URL, timeout=30).json()
    except httpx.HTTPError as exc:
        print(f"Não foi possível baixar o Swagger oficial: {exc}")
        return 2
    salvo = json.loads(CONTRATO.read_text(encoding="utf-8"))

    rotas_oficiais = {f"{m.upper()} {p}" for p, ms in oficial["paths"].items() for m in ms}
    rotas_salvas = set(salvo["rotas"]) | set(salvo["rotas_fora_do_escopo_do_mock"])
    diferencas = []
    for r in sorted(rotas_oficiais - rotas_salvas):
        diferencas.append(f"+ rota nova no Hiper: {r}")
    for r in sorted(rotas_salvas - rotas_oficiais):
        diferencas.append(f"- rota removida do Hiper: {r}")

    schemas = oficial.get("components", {}).get("schemas", {})
    for dto, campos in salvo["dtos"].items():
        if dto not in schemas:
            diferencas.append(f"- DTO ausente no Swagger: {dto}")
            continue
        atuais = set(schemas[dto].get("properties", {}))
        for c in sorted(atuais - set(campos)):
            diferencas.append(f"+ {dto}.{c} (campo novo)")
        for c in sorted(set(campos) - atuais):
            diferencas.append(f"- {dto}.{c} (campo removido)")

    if diferencas:
        print("O contrato oficial mudou:\n" + "\n".join(diferencas))
        return 1
    print(f"Contrato em dia: {len(rotas_oficiais)} rotas e {len(salvo['dtos'])} DTOs conferidos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
