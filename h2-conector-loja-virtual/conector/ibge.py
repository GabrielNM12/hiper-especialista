"""CEP → código IBGE do município, exigido em enderecoDeCobranca/enderecoDeEntrega do pedido Hiper.

O WooCommerce não guarda o código IBGE. A fonte é o ViaCEP (campo "ibge"), com cache em SQLite.
Sem internet, usa uma tabela de reserva por faixa de CEP das principais cidades do Sul.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from .banco import Banco

# faixa de CEP (prefixo de 5 dígitos, inclusive) → (código IBGE, cidade, UF)
RESERVA = [
    ((88000, 88099), 4205407, "Florianópolis", "SC"),
    ((88800, 88819), 4204608, "Criciúma", "SC"),
    ((88300, 88319), 4208203, "Itajaí", "SC"),
    ((88350, 88359), 4202909, "Brusque", "SC"),
    ((89010, 89099), 4202404, "Blumenau", "SC"),
    ((89200, 89239), 4209102, "Joinville", "SC"),
    ((88700, 88719), 4207007, "Tubarão", "SC"),
    ((80000, 82999), 4106902, "Curitiba", "PR"),
    ((90000, 91999), 4314902, "Porto Alegre", "RS"),
    ((1000, 5999), 3550308, "São Paulo", "SP"),
    ((20000, 23799), 3304557, "Rio de Janeiro", "RJ"),
]


class ResolvedorIbge:
    def __init__(self, banco: Banco, usar_viacep: bool = True, http: Optional[httpx.Client] = None):
        self.banco = banco
        self.usar_viacep = usar_viacep
        self.http = http or httpx.Client(timeout=8)

    def codigo(self, cep: str) -> Optional[int]:
        cep = re.sub(r"\D", "", cep or "")
        if len(cep) != 8:
            return None
        cache = self.banco.ibge(cep)
        if cache:
            return cache["codigo_ibge"]
        if self.usar_viacep:
            try:
                dados = self.http.get(f"https://viacep.com.br/ws/{cep}/json/").json()
                if dados.get("ibge"):
                    self.banco.salvar_ibge(cep, int(dados["ibge"]), dados.get("localidade"), dados.get("uf"))
                    return int(dados["ibge"])
            except (httpx.HTTPError, ValueError):
                pass
        prefixo = int(cep[:5])
        for (ini, fim), codigo, cidade, uf in RESERVA:
            if ini <= prefixo <= fim:
                self.banco.salvar_ibge(cep, codigo, cidade, uf)
                return codigo
        return None
