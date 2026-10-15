"""Dados de exemplo: uma loja de moda de pequeno porte em Criciúma (SC).

Gera produtos com e sem grade, códigos de barras com dígito verificador válido,
NCMs reais de vestuário/acessórios e casos de borda que aparecem em lojas de verdade:
produto fora da loja virtual, produto inativo, estoque negativo, descrição nula.
"""

from __future__ import annotations

import random
import uuid

from .util_fiscal import gtin13

# Chave de segurança de 64 caracteres, como a real. Use-a para gerar o token no mock.
CHAVE_PADRAO = "4d6f636b48697065724c6f6a614d6f646153756c4372696369756d6132303236"

CONTA_PADRAO = {
    "contaHiperId": "6f1c8a2e-3b7d-4c55-9e10-1a2b3c4d5e6f",
    "razaoSocial": "Moda Sul Comércio de Vestuário Ltda",
    "nomeFantasia": "Moda Sul Criciúma",
    "cnpj": "45723174000110",
    "uf": "SC",
    "codigoIbge": 4204608,  # Criciúma
    "regime": "Simples Nacional",
}

IMG = "https://placehold.co/800x800/png?text="

CATEGORIAS = {
    "Camisetas": "0b3f6a10-1c2d-4e5f-8a9b-000000000001",
    "Calças": "0b3f6a10-1c2d-4e5f-8a9b-000000000002",
    "Vestidos": "0b3f6a10-1c2d-4e5f-8a9b-000000000003",
    "Calçados": "0b3f6a10-1c2d-4e5f-8a9b-000000000004",
    "Agasalhos": "0b3f6a10-1c2d-4e5f-8a9b-000000000005",
    "Acessórios": "0b3f6a10-1c2d-4e5f-8a9b-000000000006",
}

# nome, categoria, marca, ncm, preço, custo, (tipoA, valoresA), (tipoB, valoresB), extras
CATALOGO = [
    ("Camiseta Básica Algodão", "Camisetas", "Sul Basics", "6109.10.00", 59.90, 22.40,
     ("Cores", ["Branca", "Preta", "Azul Marinho"]), ("Tamanhos", ["P", "M", "G", "GG"]),
     {"atacado": [(3, 54.90), (6, 49.90), (12, 44.90)]}),
    ("Calça Jeans Skinny", "Calças", "Urbana Jeans", "6204.62.00", 189.90, 78.00,
     ("Tamanhos", ["36", "38", "40", "42", "44"]), None, {}),
    ("Vestido Midi Floral", "Vestidos", "Flor de Lis", "6204.44.00", 249.90, 96.50,
     ("Tamanhos", ["P", "M", "G"]), None, {}),
    ("Tênis Casual Couro", "Calçados", "Passo Firme", "6403.99.90", 299.90, 131.00,
     ("Tamanhos", ["34", "35", "36", "37", "38", "39", "40"]), None, {}),
    ("Jaqueta Corta-Vento", "Agasalhos", "Vento Sul", "6201.40.00", 229.90, 92.00,
     ("Cores", ["Verde Musgo", "Preta"]), ("Tamanhos", ["P", "M", "G", "GG"]), {}),
    ("Moletom Canguru", "Agasalhos", "Sul Basics", "6110.30.00", 159.90, 61.30,
     ("Cores", ["Cinza Mescla", "Off White"]), ("Tamanhos", ["P", "M", "G"]),
     {"atacado": [(4, 149.90), (10, 139.90)]}),
    ("Bermuda Sarja", "Calças", "Urbana Jeans", "6203.43.00", 119.90, 47.80,
     ("Tamanhos", ["38", "40", "42", "44"]), None, {"fora_da_loja_virtual": True}),
    ("Boné Aba Curva", "Acessórios", "Vento Sul", "6505.00.90", 79.90, 28.00, None, None, {}),
    ("Cinto de Couro Legítimo", "Acessórios", "Passo Firme", "4203.30.00", 99.90, 37.50, None, None, {}),
    ("Kit Meias Cano Médio 3 Pares", "Acessórios", "Sul Basics", "6115.95.00", 39.90, 13.20, None, None,
     {"atacado": [(5, 35.90), (10, 32.90)]}),
    ("Bolsa Transversal Matelassê", "Acessórios", "Flor de Lis", "4202.22.10", 189.90, 74.00, None, None, {}),
    ("Óculos de Sol Retrô", "Acessórios", "Vento Sul", "9004.10.00", 149.90, 41.00, None, None,
     {"descricao_nula": True}),
    ("Carteira Slim", "Acessórios", "Passo Firme", "4202.31.00", 89.90, 30.10, None, None,
     {"estoque_negativo": True}),
    ("Pulseira Banhada", "Acessórios", "Flor de Lis", "7117.19.00", 49.90, 12.80, None, None,
     {"inativo": True}),
]


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def gerar_produtos(semente: int = 2026) -> list[dict]:
    """Devolve a lista de produtos internos (pais e filhos) do mock."""
    rng = random.Random(semente)
    produtos: list[dict] = []
    codigo = 3001
    seq_ean = 1
    seq_interno = 170

    for nome, categoria, marca, ncm, preco, custo, var_a, var_b, extras in CATALOGO:
        pai_id = _uuid(rng)
        tem_grade = var_a is not None
        base = {
            "id": pai_id,
            "codigo": codigo,
            "nome": nome,
            "descricao": None if extras.get("descricao_nula") else f"{nome} da marca {marca}. Produto de demonstração.",
            "categoria": categoria,
            "categoriaDoProdutoId": CATEGORIAS[categoria],
            "marca": marca,
            "ncm": ncm,
            "unidade": "UN",
            "preco": preco,
            "custo": custo,
            "altura": round(rng.uniform(3, 15), 3),
            "largura": round(rng.uniform(15, 35), 3),
            "comprimento": round(rng.uniform(20, 40), 3),
            "peso": round(rng.uniform(0.1, 1.2), 3),
            "imagem": IMG + nome.replace(" ", "+"),
            "imagensAdicionais": [IMG + nome.replace(" ", "+") + f"+{i}" for i in (2, 3)],
            "ativo": not extras.get("inativo", False),
            "enviarParaLojaVirtual": not extras.get("fora_da_loja_virtual", False),
            "grade": tem_grade,
            "paiId": None,
            "tipoVariacaoA": None, "nomeVariacaoA": None, "tipoVariacaoB": None, "nomeVariacaoB": None,
            "variacaoAtiva": True,
            "estoque": 0.0,
            "reservado": 0.0,
            "estoqueMinimo": 0.0,
            "atacado": extras.get("atacado", []),
        }

        if not tem_grade:
            base["codigoDeBarras"] = gtin13(f"789{4561:04d}{seq_ean:05d}")
            seq_ean += 1
            base["estoque"] = -2.0 if extras.get("estoque_negativo") else float(rng.randint(0, 40))
            base["estoqueMinimo"] = 5.0
            produtos.append(base)
        else:
            # O pai de grade usa o próprio código interno como código de barras (igual ao exemplo oficial).
            base["codigoDeBarras"] = str(codigo)
            produtos.append(base)
            combinacoes = [(a, b) for a in var_a[1] for b in (var_b[1] if var_b else [None])]
            for a, b in combinacoes:
                filho = dict(base)
                filho.update(
                    id=_uuid(rng),
                    paiId=pai_id,
                    grade=True,
                    nome=f"{nome} {a}" + (f" {b}" if b else ""),
                    # Filhos recebem código de barras interno 999… (padrão do Hiper para itens de grade)
                    codigoDeBarras=gtin13(f"999{seq_interno:09d}"),
                    tipoVariacaoA=var_a[0], nomeVariacaoA=a,
                    tipoVariacaoB=var_b[0] if var_b else None, nomeVariacaoB=b,
                    estoque=float(rng.choice([0, 0, 1, 2, 3, 5, 8, 12, 18])),
                    estoqueMinimo=2.0,
                    imagensAdicionais=list(base["imagensAdicionais"]),
                    atacado=list(base["atacado"]),
                )
                seq_interno += 1
                produtos.append(filho)
        codigo += 1
    return produtos
