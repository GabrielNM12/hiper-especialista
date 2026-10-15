"""O mock precisa responder com exatamente os campos dos DTOs do Swagger oficial."""

import json
from pathlib import Path

from conftest import pedido_exemplo

CONTRATO = json.loads((Path(__file__).resolve().parents[1] / "contrato" / "contrato_oficial.json").read_text())
DTOS = CONTRATO["dtos"]


def campos(dto):
    return set(DTOS[dto])


def test_rotas_do_contrato_existem_no_mock(app):
    rotas_mock = {f"{m} {r.path}" for r in app.routes for m in getattr(r, "methods", []) or []}
    faltando = [r for r in CONTRATO["rotas"] if r not in rotas_mock]
    assert not faltando, f"rotas oficiais sem implementação no mock: {faltando}"


def test_token_dto(client):
    from mock_hiper.seed import CHAVE_PADRAO
    corpo = client.get(f"/api/v1/auth/gerar-token/{CHAVE_PADRAO}").json()
    assert set(corpo) == campos("TokenDto")
    assert len(CHAVE_PADRAO) == 64


def test_produto_ponto_dto(client, auth):
    corpo = client.get("/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao=0", headers=auth).json()
    assert set(corpo) == campos("ListaDeProdutosDto")
    for p in corpo["produtos"]:
        # rota sem paginação não traz precoAtacado (só a listagem paginada traz)
        assert set(p) == campos("ProdutoPontoDeSincronizacaoDto") - {"precoAtacado"}
        for v in p["variacao"]:
            assert set(v) == campos("VariacaoDoProdutoPontoDeSincronizacaoDto")


def test_listagem_traz_preco_atacado(client, auth):
    corpo = client.get("/api/v1/produtos/listagemDeProdutos?pontoDeSincronizacao=0&pagina=1", headers=auth).json()
    p = corpo["produtos"][0]
    assert set(p) == campos("ProdutoPontoDeSincronizacaoDto")
    assert set(p["precoAtacado"]) == campos("PrecoAtacadoEcommerceDto")


def test_estoque_dtos(client, auth):
    lista = client.get("/api/v1/estoques/estoquesDeProdutos?pontoDeSincronizacao=0&pagina=1", headers=auth).json()
    assert set(lista) == campos("ListaDeEstoquesDosProdutosDto")
    item = lista["estoquesDeProdutos"][0]
    assert set(item) == campos("EstoqueDoProdutoPontoDeSincronizacaoDto")
    unico = client.get(f"/api/v1/estoques/pontoDeSincronizacao?produtoId={item['produtoId']}", headers=auth).json()
    assert set(unico) == campos("EstoqueDoProdutoDto")


def test_pedido_dtos(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    r = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth)
    assert r.status_code == 200, r.text
    assert set(r.json()) == campos("RecebimentoDoPedidoDeVendaDto")
    pedido_id = r.json()["id"]
    ev = client.get(f"/api/v1/pedido-de-venda/eventos/{pedido_id}", headers=auth).json()
    assert set(ev) == campos("EventosDoPedidoDeVendaDto")
    assert set(ev["eventos"][0]) == campos("EventoDto")
    cancel = client.put(f"/api/v1/pedido-de-venda/cancelar/{pedido_id}", headers=auth).json()
    assert set(cancel) == campos("RecebimentoDeCancelamentoDePedidoDto")


def test_webhook_dto(client, auth):
    r = client.post("/api/v1/configuracao/webhook-config", headers=auth,
                    json={"ativo": True, "url": "http://exemplo.test/hook", "tipo": [1], "limiteDeTentativas": 2})
    assert set(r.json()) == campos("ConfiguracaoDeWebhookResponseContract")
