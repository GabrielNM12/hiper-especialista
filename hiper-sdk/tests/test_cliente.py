"""Testes do SDK contra o mock H1 rodando em processo (sem rede)."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "hiper-sdk"))
sys.path.insert(0, str(RAIZ / "h1-mock-api-hiper"))

from hiper_sdk import (  # noqa: E402
    Cliente, Endereco, HiperClient, HiperEnvioIncerto, HiperErroAPI, HiperNaoAutorizado,
    ItemDoPedido, Marketplace, MeioDePagamento, PedidoDeVenda,
)
from mock_hiper.app import criar_app  # noqa: E402
from mock_hiper.estado import Config  # noqa: E402
from mock_hiper.seed import CHAVE_PADRAO  # noqa: E402


@pytest.fixture
def mock():
    app = criar_app(Config(atraso_processamento_s=0), despachar_webhooks=False)
    return app, TestClient(app)


@pytest.fixture
def hiper(mock):
    _, http = mock
    esperas = []
    cliente = HiperClient(CHAVE_PADRAO, base_url="http://testserver", http=http, dormir=esperas.append)
    cliente.esperas = esperas
    return cliente


def montar_pedido(produto_id, preco, frete=0.0):
    endereco = Endereco(bairro="Centro", cep="88801010", codigoIbge=4204608, logradouro="Rua A", numero="1")
    return PedidoDeVenda(
        cliente=Cliente(documento="19561920000", nomeDoCliente="Fulana"),
        enderecoDeCobranca=endereco, enderecoDeEntrega=endereco,
        itens=[ItemDoPedido(produtoId=produto_id, quantidade=1, precoUnitarioBruto=preco, precoUnitarioLiquido=preco)],
        meiosDePagamento=[MeioDePagamento(idMeioDePagamento=12, valor=round(preco + frete, 2))],
        valorDoFrete=frete, numeroPedidoDeVenda="T-1",
        marketplace=Marketplace(cnpj="12605982000124", nome="Loja"),
    )


def test_produtos_e_grade(hiper):
    lista = hiper.produtos_desde(0)
    assert lista.pontoDeSincronizacao > 0
    pais = [p for p in lista.produtos if p.eh_pai_de_grade]
    filhos = [p for p in lista.produtos if p.eh_filho_de_grade]
    assert pais and filhos
    assert all(v.id in {f.id for f in filhos} for v in pais[0].variacao)


def test_iterar_listagem_paginada(hiper, mock):
    app, _ = mock
    app.state.estado.config.tamanho_pagina = 7
    total = sum(len(l.produtos) for l in hiper.iterar_listagem_de_produtos(0))
    assert total == len(hiper.produtos_desde(0).produtos)


def test_renova_token_em_401(hiper):
    hiper.produtos_desde(0)
    hiper._token = "token-invalido"
    hiper._token_expira_em = 9e12
    assert hiper.produtos_desde(0).produtos


def test_chave_invalida():
    app = criar_app(Config(), despachar_webhooks=False)
    cliente = HiperClient("x" * 64, base_url="http://testserver", http=TestClient(app))
    with pytest.raises(HiperNaoAutorizado):
        cliente.produtos_desde(0)


def test_backoff_em_429_respeita_retry_after(hiper, mock):
    app, http = mock
    estado = app.state.estado
    chamadas = {"n": 0}
    original = http.request

    def instavel(*args, **kwargs):
        chamadas["n"] += 1
        estado.caos.taxa_429 = 1.0 if chamadas["n"] <= 2 else 0.0
        return original(*args, **kwargs)

    hiper.gerar_token()
    http.request = instavel
    assert hiper.produtos_desde(0).produtos
    assert hiper.esperas == [1.0, 1.0]  # Retry-After do mock


def test_get_desiste_apos_max_tentativas(hiper, mock):
    app, _ = mock
    hiper.gerar_token()
    app.state.estado.caos.taxa_500 = 1.0
    with pytest.raises(HiperErroAPI) as erro:
        hiper.produtos_desde(0)
    assert erro.value.status == 503 and len(hiper.esperas) == hiper.max_tentativas


def test_token_tambem_tem_retentativa(hiper, mock):
    app, http = mock
    estado = app.state.estado
    original = http.get
    chamadas = {"n": 0}

    def instavel(*args, **kwargs):
        chamadas["n"] += 1
        estado.caos.taxa_500 = 1.0 if chamadas["n"] == 1 else 0.0
        return original(*args, **kwargs)

    http.get = instavel
    assert hiper.gerar_token()
    assert len(hiper.esperas) == 1


def test_post_nao_repete_em_5xx(hiper, mock):
    app, _ = mock
    estado = app.state.estado
    filho = next(p for p in estado.produtos.values() if p["paiId"])
    hiper.gerar_token()
    estado.caos.taxa_500 = 1.0
    with pytest.raises(HiperEnvioIncerto):
        hiper.criar_pedido_de_venda(montar_pedido(filho["id"], filho["preco"]))
    assert hiper.esperas == []


def test_fluxo_completo_de_pedido(hiper, mock):
    app, http = mock
    estado = app.state.estado
    filho = next(p for p in estado.produtos.values() if p["paiId"] and p["estoque"] > 0)
    recebido = hiper.criar_pedido_de_venda(montar_pedido(filho["id"], filho["preco"], frete=12.5))
    assert recebido.id and recebido.message

    assert hiper.estoque_do_produto(filho["id"]).quantidadeEmEstoqueReservado == 1
    eventos = hiper.eventos_do_pedido(recebido.id)
    assert eventos.codigoDaSituacaoDeProcessamento == 2 and eventos.documento_fiscal is None

    http.post(f"/simulador/pedidos/{recebido.id}/faturar")
    eventos = hiper.eventos_do_pedido(recebido.id)
    assert eventos.documento_fiscal and len(eventos.documento_fiscal.chaveDocumentoFiscal) == 44


def test_erro_de_validacao_vira_excecao_com_detalhes(hiper):
    with pytest.raises(HiperErroAPI) as erro:
        hiper.criar_pedido_de_venda(montar_pedido("e41acf38-3f4b-4309-8988-8b33a4e5f359", 10))
    assert erro.value.status == 400 and erro.value.erros
