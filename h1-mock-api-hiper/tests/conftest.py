import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_hiper.app import criar_app  # noqa: E402
from mock_hiper.estado import Config  # noqa: E402
from mock_hiper.seed import CHAVE_PADRAO  # noqa: E402


@pytest.fixture
def app():
    return criar_app(Config(atraso_processamento_s=0), despachar_webhooks=False)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth(client):
    token = client.get(f"/api/v1/auth/gerar-token/{CHAVE_PADRAO}").json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def estado(app):
    return app.state.estado


def pedido_exemplo(estado, qtd=1, desconto=0.0, frete=10.0):
    filho = next(p for p in estado.produtos.values() if p["paiId"] and p["estoque"] > 2)
    liquido = round(filho["preco"] - desconto, 2)
    total = round(liquido * qtd + frete, 2)
    return filho, {
        "cliente": {"documento": "19561920000", "email": "cliente@exemplo.com.br", "inscricaoEstadual": "",
                    "nomeDoCliente": "Cliente do E-commerce", "nomeFantasia": ""},
        "enderecoDeCobranca": {"bairro": "Centro", "cep": "88801010", "codigoIbge": 4204608, "complemento": "",
                               "logradouro": "Rua Principal", "numero": "01"},
        "enderecoDeEntrega": {"bairro": "Centro", "cep": "88801010", "codigoIbge": 4204608, "complemento": "Sala 2",
                              "logradouro": "Rua Principal", "numero": "22"},
        "itens": [{"produtoId": filho["id"], "quantidade": qtd, "precoUnitarioBruto": filho["preco"],
                   "precoUnitarioLiquido": liquido}],
        "meiosDePagamento": [{"idMeioDePagamento": 12, "parcelas": 1, "valor": total}],
        "numeroPedidoDeVenda": "WEB-1001",
        "observacaoDoPedidoDeVenda": "",
        "valorDoFrete": frete,
        "Marketplace": {"Cnpj": "12605982000124", "Nome": "Loja Virtual"},
    }
