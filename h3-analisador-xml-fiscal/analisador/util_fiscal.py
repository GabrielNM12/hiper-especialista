"""Utilitários fiscais brasileiros: dígitos verificadores de CPF, CNPJ, GTIN e chave de acesso."""

from __future__ import annotations

import random
import re


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def cpf_valido(cpf: str) -> bool:
    cpf = so_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        dv = (soma * 10) % 11 % 10
        if dv != int(cpf[i]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    cnpj = so_digitos(cnpj)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(d) * p for d, p in zip(cnpj[:pos], pesos))
        resto = soma % 11
        dv = 0 if resto < 2 else 11 - resto
        if dv != int(cnpj[pos]):
            return False
    return True


def documento_valido(doc: str) -> bool:
    d = so_digitos(doc)
    return cpf_valido(d) if len(d) == 11 else cnpj_valido(d) if len(d) == 14 else False


def gerar_cpf(rng: random.Random) -> str:
    base = [rng.randint(0, 9) for _ in range(9)]
    for i in (9, 10):
        soma = sum(base[n] * ((i + 1) - n) for n in range(i))
        base.append((soma * 10) % 11 % 10)
    return "".join(map(str, base))


def gerar_cnpj(rng: random.Random, filial: str = "0001") -> str:
    base = [rng.randint(0, 9) for _ in range(8)] + [int(c) for c in filial]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pesos in (pesos1, [6] + pesos1):
        soma = sum(d * p for d, p in zip(base, pesos))
        resto = soma % 11
        base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


def dv_gtin(corpo: str) -> int:
    soma = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(corpo)))
    return (10 - soma % 10) % 10


def gtin13(corpo12: str) -> str:
    return corpo12 + str(dv_gtin(corpo12))


def gtin_valido(codigo: str | None) -> bool:
    c = so_digitos(codigo)
    return len(c) in (8, 12, 13, 14) and c == (codigo or "") and dv_gtin(c[:-1]) == int(c[-1])


def dv_chave(chave43: str) -> int:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(d) * pesos[i % 8] for i, d in enumerate(reversed(chave43)))
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def montar_chave(cuf: int, aamm: str, cnpj: str, modelo: int, serie: int, numero: int, codigo: int, tp_emis: int = 1) -> str:
    chave43 = f"{cuf:02d}{aamm}{so_digitos(cnpj):0>14}{modelo:02d}{serie:03d}{numero:09d}{tp_emis}{codigo:08d}"
    return chave43 + str(dv_chave(chave43))


UF_POR_CODIGO_IBGE = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
    "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS",
    "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


def uf_do_ibge(codigo_ibge: int | str) -> str | None:
    return UF_POR_CODIGO_IBGE.get(str(codigo_ibge)[:2])
