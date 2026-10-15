"""Regras de negócio documentadas pela Hiper que o mock reproduz."""

from conftest import pedido_exemplo
from mock_hiper.nfe import gerar_nfe
from mock_hiper.util_fiscal import dv_chave


def test_sem_token_401_sem_corpo(client):
    r = client.get("/api/v1/produtos/pontoDeSincronizacao")
    assert r.status_code == 401
    assert r.content == b""


def test_chave_invalida(client):
    r = client.get("/api/v1/auth/gerar-token/" + "0" * 64)
    assert r.status_code == 401
    assert r.json()["errors"]


def test_token_vale_6_horas(client):
    import jwt
    from mock_hiper.seed import CHAVE_PADRAO
    token = client.get(f"/api/v1/auth/gerar-token/{CHAVE_PADRAO}").json()["token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["nbf"] == 21600


def test_sincronizacao_incremental(client, auth, estado):
    inicial = client.get("/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao=0", headers=auth).json()
    ponto = inicial["pontoDeSincronizacao"]
    nada = client.get(f"/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao={ponto}", headers=auth).json()
    assert nada["produtos"] == []

    simples = next(p for p in estado.produtos.values() if not p["grade"] and p["enviarParaLojaVirtual"])
    client.patch(f"/simulador/produtos/{simples['id']}", json={"preco": 77.7})
    mudou = client.get(f"/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao={ponto}", headers=auth).json()
    assert [p["id"] for p in mudou["produtos"]] == [simples["id"]]
    assert mudou["produtos"][0]["preco"] == 77.7
    assert mudou["pontoDeSincronizacao"] > ponto


def test_grade_pai_e_filhos(client, auth):
    produtos = client.get("/api/v1/produtos/pontoDeSincronizacao", headers=auth).json()["produtos"]
    pai = next(p for p in produtos if p["grade"] and p["produtoPrimarioId"].startswith("00000000"))
    assert pai["variacao"], "pai de grade deve listar os filhos em variacao"
    assert pai["quantidadeEmEstoque"] == 0
    filhos = [p for p in produtos if p["produtoPrimarioId"] == pai["id"]]
    assert {f["id"] for f in filhos} == {v["id"] for v in pai["variacao"]}
    assert all(f["variacao"] == [] for f in filhos)
    assert all(v["codigo"] == pai["codigo"] for v in pai["variacao"])


def test_produto_fora_da_loja_virtual_nao_aparece_e_removido_volta(client, auth, estado):
    ids = {p["id"] for p in client.get("/api/v1/produtos/pontoDeSincronizacao", headers=auth).json()["produtos"]}
    fora = next(p for p in estado.produtos.values() if not p["enviarParaLojaVirtual"])
    assert fora["id"] not in ids

    alvo = next(p for p in estado.produtos.values() if not p["paiId"] and p["enviarParaLojaVirtual"] and not p["grade"])
    ponto = estado.ponto
    client.post(f"/simulador/produtos/{alvo['id']}/loja-virtual?enviar=false")
    mudou = client.get(f"/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao={ponto}", headers=auth).json()
    assert mudou["produtos"][0]["id"] == alvo["id"]
    assert mudou["produtos"][0]["removido"] is True


def test_pedido_reserva_e_fatura_baixa_estoque(client, auth, estado):
    filho, corpo = pedido_exemplo(estado, qtd=2, desconto=5.0)
    estoque_antes = filho["estoque"]
    pedido_id = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth).json()["id"]
    assert filho["reservado"] == 2

    est = client.get(f"/api/v1/estoques/pontoDeSincronizacao?produtoId={filho['id']}", headers=auth).json()
    assert est["quantidadeEmEstoqueReservado"] == 2

    r = client.post(f"/simulador/pedidos/{pedido_id}/faturar")
    assert r.status_code == 200, r.text
    assert filho["estoque"] == estoque_antes - 2 and filho["reservado"] == 0

    ev = client.get(f"/api/v1/pedido-de-venda/eventos/{pedido_id}", headers=auth).json()
    doc = ev["eventos"][-1]
    assert doc["tipoDocumentoFiscal"] == "NF-e" and len(doc["chaveDocumentoFiscal"]) == 44
    assert int(doc["chaveDocumentoFiscal"][-1]) == dv_chave(doc["chaveDocumentoFiscal"][:43])
    xml = client.get(f"/arquivos/xml/{doc['chaveDocumentoFiscal']}.xml")
    assert xml.status_code == 200 and b"<vDesc>10.00</vDesc>" in xml.content


def test_pedido_rejeita_produto_inexistente_e_pai_de_grade(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    corpo["itens"][0]["produtoId"] = "e41acf38-3f4b-4309-8988-8b33a4e5f359"
    r = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth)
    assert r.status_code == 400 and "Somente produtos cadastrados no Hiper" in " ".join(r.json()["errors"])

    pai = next(p for p in estado.produtos.values() if p["grade"] and not p["paiId"])
    corpo["itens"][0].update(produtoId=pai["id"])
    r = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth)
    assert "variação" in " ".join(r.json()["errors"])


def test_pedido_exige_marketplace_em_sc_e_meio_de_pagamento_valido(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    corpo.pop("Marketplace")
    corpo["meiosDePagamento"][0]["idMeioDePagamento"] = 7
    erros = " ".join(client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth).json()["errors"])
    assert "Marketplace" in erros and "idMeioDePagamento" in erros


def test_pedido_valida_soma_dos_pagamentos(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    corpo["meiosDePagamento"][0]["valor"] += 5
    r = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth)
    assert r.status_code == 400 and "difere do total" in " ".join(r.json()["errors"])


def test_cliente_novo_e_cadastrado(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth)
    assert "19561920000" in estado.clientes


def test_cancelamento_no_gestao_nao_reflete_na_api(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    pedido_id = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth).json()["id"]
    client.post(f"/simulador/pedidos/{pedido_id}/cancelar-no-gestao")
    ev = client.get(f"/api/v1/pedido-de-venda/eventos/{pedido_id}", headers=auth).json()
    assert ev["cancelado"] is False


def test_cancelamento_via_api_libera_reserva(client, auth, estado):
    filho, corpo = pedido_exemplo(estado, qtd=1)
    pedido_id = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth).json()["id"]
    client.put(f"/api/v1/pedido-de-venda/cancelar/{pedido_id}", headers=auth)
    assert filho["reservado"] == 0
    ev = client.get(f"/api/v1/pedido-de-venda/eventos/{pedido_id}", headers=auth).json()
    assert ev["cancelado"] is True


def test_caos_429_com_retry_after(client, auth):
    client.put("/simulador/caos", json={"taxa_429": 1.0})
    r = client.get("/api/v1/produtos/pontoDeSincronizacao", headers=auth)
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"
    client.put("/simulador/caos", json={})


def test_paginacao(client, auth, estado):
    estado.config.tamanho_pagina = 10
    paginas, pagina = [], 1
    while True:
        lote = client.get(f"/api/v1/produtos/listagemDeProdutos?pagina={pagina}", headers=auth).json()["produtos"]
        if not lote:
            break
        paginas.append(lote)
        pagina += 1
    total = sum(len(p) for p in paginas)
    assert total == len(client.get("/api/v1/produtos/pontoDeSincronizacao", headers=auth).json()["produtos"])
    assert all(len(p) <= 10 for p in paginas)


def test_nfe_interestadual_usa_cfop_6108(client, auth, estado):
    _, corpo = pedido_exemplo(estado)
    corpo["enderecoDeEntrega"].update(codigoIbge=3550308, cep="01310100")  # São Paulo
    pedido_id = client.post("/api/v1/pedido-de-venda", json=corpo, headers=auth).json()["id"]
    estado.processar_pendentes()
    chave, tipo, xml = gerar_nfe(estado, estado.pedido(pedido_id), 999)
    assert "<CFOP>6108</CFOP>" in xml and "<idDest>2</idDest>" in xml
