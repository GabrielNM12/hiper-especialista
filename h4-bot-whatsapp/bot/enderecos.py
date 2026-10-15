"""CEP → endereço e código IBGE (exigido no pedido do Hiper). ViaCEP com reserva offline para o Sul."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

RESERVA = [
    ((88800, 88819), 4204608, "Criciúma", "SC"), ((88000, 88099), 4205407, "Florianópolis", "SC"),
    ((88300, 88319), 4208203, "Itajaí", "SC"), ((88350, 88359), 4202909, "Brusque", "SC"),
    ((89010, 89099), 4202404, "Blumenau", "SC"), ((89200, 89239), 4209102, "Joinville", "SC"),
    ((88700, 88719), 4207007, "Tubarão", "SC"), ((80000, 82999), 4106902, "Curitiba", "PR"),
    ((90000, 91999), 4314902, "Porto Alegre", "RS"), ((1000, 5999), 3550308, "São Paulo", "SP"),
]


@dataclass
class Endereco:
    cep: str
    codigo_ibge: int
    cidade: str
    uf: str
    logradouro: str = ""
    bairro: str = ""


def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def buscar_cep(cep: str, usar_viacep: bool = True, http: Optional[httpx.Client] = None) -> Optional[Endereco]:
    cep = so_digitos(cep)
    if len(cep) != 8:
        return None
    if usar_viacep:
        try:
            cliente = http or httpx.Client(timeout=6)
            d = cliente.get(f"https://viacep.com.br/ws/{cep}/json/").json()
            if d.get("ibge"):
                return Endereco(cep, int(d["ibge"]), d.get("localidade", ""), d.get("uf", ""), d.get("logradouro", ""),
                                d.get("bairro", ""))
        except (httpx.HTTPError, ValueError):
            pass
    prefixo = int(cep[:5])
    for (ini, fim), ibge, cidade, uf in RESERVA:
        if ini <= prefixo <= fim:
            return Endereco(cep, ibge, cidade, uf)
    return None
