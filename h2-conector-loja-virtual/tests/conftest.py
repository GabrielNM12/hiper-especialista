import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[2]
for pasta in ("hiper-sdk", "h1-mock-api-hiper", "h2-conector-loja-virtual"):
    sys.path.insert(0, str(RAIZ / pasta))

from conector.banco import Banco  # noqa: E402
from conector.config import Config  # noqa: E402
from conector.fabrica import criar_sincronizador  # noqa: E402
from mock_hiper.app import criar_app as criar_mock_hiper  # noqa: E402
from mock_hiper.estado import Config as ConfigMock  # noqa: E402
from mock_hiper.seed import CHAVE_PADRAO  # noqa: E402
from mock_woocommerce.app import CK, CS, criar_app as criar_mock_woo  # noqa: E402


class Ambiente:
    def __init__(self):
        self.app_hiper = criar_mock_hiper(ConfigMock(atraso_processamento_s=0), despachar_webhooks=False)
        self.app_woo = criar_mock_woo()
        self.http_hiper = TestClient(self.app_hiper)
        self.http_woo = TestClient(self.app_woo)
        self.hiper = self.app_hiper.state.estado
        self.loja = self.app_woo.state.loja
        self.config = Config(hiper_url="http://testserver", hiper_chave=CHAVE_PADRAO, woo_url="http://testserver",
                             woo_consumer_key=CK, woo_consumer_secret=CS, marketplace_cnpj="12605982000124",
                             marketplace_nome="Loja Teste", banco=":memory:", usar_viacep=False)
        self.banco = Banco(":memory:")
        self.sinc = self.novo_sincronizador(self.banco)

    def novo_sincronizador(self, banco):
        sinc = criar_sincronizador(self.config, http_hiper=self.http_hiper, http_woo=self.http_woo, banco=banco)
        sinc.hiper._dormir = lambda s: None
        sinc.woo.dormir = lambda s: None
        return sinc

    def comprar(self, itens, cupom=None, pagamento="pix", cpf="529.982.247-25", cep="88802-000", parcelas=1):
        corpo = {
            "payment_method": pagamento, "installments": parcelas,
            "billing": {"first_name": "Ana", "last_name": "Souza", "email": "ana@exemplo.com.br", "cpf": cpf,
                        "postcode": cep, "address_1": "Rua Coronel Pedro Benedet", "number": "120",
                        "neighborhood": "Centro", "city": "Criciúma", "state": "SC"},
            "line_items": itens, "coupon_lines": [{"code": cupom}] if cupom else [],
        }
        return self.app_woo.state.checkout(corpo)

    def woo_item_da_variacao(self, nome_produto, qtd=1, indice=0):
        p = next(p for p in self.loja.produtos.values() if p["name"] == nome_produto)
        variacoes = [v for v in self.loja.variacoes[p["id"]].values() if (v["stock_quantity"] or 0) >= qtd]
        return {"product_id": p["id"], "variation_id": variacoes[indice]["id"], "quantity": qtd}

    def woo_item_simples(self, nome_produto, qtd=1):
        p = next(p for p in self.loja.produtos.values() if p["name"] == nome_produto)
        return {"product_id": p["id"], "variation_id": 0, "quantity": qtd}


@pytest.fixture
def amb():
    return Ambiente()
