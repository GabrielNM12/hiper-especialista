"""Fluxo completo: mock do Hiper + mock do WooCommerce + conector, tudo em processo."""

from conector.banco import Banco


def produtos_visiveis(hiper):
    return [p for p in hiper.produtos.values() if not p["paiId"] and p["enviarParaLojaVirtual"]]


def test_catalogo_publicado_com_grade_e_status(amb):
    r = amb.sinc.ciclo()
    assert "erro" not in r["catalogo"], r
    visiveis = produtos_visiveis(amb.hiper)
    assert len(amb.loja.produtos) == len(visiveis)

    camiseta = next(p for p in amb.loja.produtos.values() if p["name"] == "Camiseta Básica Algodão")
    assert camiseta["type"] == "variable"
    assert [a["name"] for a in camiseta["attributes"]] == ["Cores", "Tamanhos"]
    assert len(amb.loja.variacoes[camiseta["id"]]) == 12
    assert any(m["key"] == "_hiper_preco_atacado" for m in camiseta["meta_data"])

    inativo = next(p for p in amb.loja.produtos.values() if p["name"] == "Pulseira Banhada")
    assert inativo["status"] == "draft"
    negativo = next(p for p in amb.loja.produtos.values() if p["name"] == "Carteira Slim")
    assert negativo["stock_quantity"] == 0
    assert not any(p["name"] == "Bermuda Sarja" for p in amb.loja.produtos.values())


def test_segundo_ciclo_nao_altera_nada(amb):
    amb.sinc.ciclo()
    antes = {pid: p["date_modified"] for pid, p in amb.loja.produtos.items()}
    r = amb.sinc.ciclo()
    assert r["catalogo"].get("recebidos", 0) == 0
    assert {pid: p["date_modified"] for pid, p in amb.loja.produtos.items()} == antes


def test_alteracao_de_preco_no_hiper_chega_na_loja(amb):
    amb.sinc.ciclo()
    bone = next(p for p in amb.hiper.produtos.values() if p["nome"] == "Boné Aba Curva")
    amb.http_hiper.patch(f"/simulador/produtos/{bone['id']}", json={"preco": 69.9})
    r = amb.sinc.ciclo()
    assert r["catalogo"]["atualizados"] == 1
    woo = next(p for p in amb.loja.produtos.values() if p["name"] == "Boné Aba Curva")
    assert woo["regular_price"] == "69.90"


def test_preco_do_pai_de_grade_propaga_para_variacoes(amb):
    amb.sinc.ciclo()
    jaqueta = next(p for p in amb.hiper.produtos.values() if p["nome"] == "Jaqueta Corta-Vento")
    amb.http_hiper.patch(f"/simulador/produtos/{jaqueta['id']}", json={"preco": 199.9})
    amb.sinc.ciclo()
    woo = next(p for p in amb.loja.produtos.values() if p["name"] == "Jaqueta Corta-Vento")
    assert {v["regular_price"] for v in amb.loja.variacoes[woo["id"]].values()} == {"199.90"}


def test_estoque_e_desmarcar_loja_virtual(amb):
    amb.sinc.ciclo()
    cinto = next(p for p in amb.hiper.produtos.values() if p["nome"] == "Cinto de Couro Legítimo")
    amb.http_hiper.post(f"/simulador/produtos/{cinto['id']}/estoque", json={"quantidade": 7})
    amb.sinc.ciclo()
    woo = next(p for p in amb.loja.produtos.values() if p["name"] == "Cinto de Couro Legítimo")
    assert woo["stock_quantity"] == 7
    amb.http_hiper.post(f"/simulador/produtos/{cinto['id']}/loja-virtual?enviar=false")
    amb.sinc.ciclo()
    assert woo["status"] == "draft"


def test_pedido_com_cupom_vai_ao_hiper_fatura_e_conclui(amb):
    amb.sinc.ciclo()
    item = amb.woo_item_da_variacao("Camiseta Básica Algodão", qtd=3)
    pedido = amb.comprar([item, amb.woo_item_simples("Kit Meias Cano Médio 3 Pares")], cupom="BEMVINDO10")
    r = amb.sinc.ciclo()
    assert r["pedidos"] == {"enviados": 1}

    reg = amb.banco.pedido(pedido["id"])
    assert reg["status"] == "enviado"
    hiper_pedido = amb.hiper.pedido(reg["hiper_id"])
    total_hiper = sum(i["quantidade"] * i["precoUnitarioLiquido"] for i in hiper_pedido.corpo["itens"]) + hiper_pedido.corpo["valorDoFrete"]
    assert round(total_hiper, 2) == float(pedido["total"])
    assert "BEMVINDO10" in hiper_pedido.corpo["observacaoDoPedidoDeVenda"]

    variacao_hiper = amb.hiper.produtos[amb.banco.hiper_id_por_woo(item["variation_id"])]
    assert variacao_hiper["reservado"] == 3
    var_woo = amb.loja.variacoes[item["product_id"]][item["variation_id"]]
    assert var_woo["stock_quantity"] == variacao_hiper["estoque"] - 3  # loja não "devolve" o que foi vendido

    amb.sinc.ciclo()
    assert amb.banco.pedido(pedido["id"])["hiper_codigo"].startswith("LV")
    amb.http_hiper.post(f"/simulador/pedidos/{reg['hiper_id']}/faturar")
    r = amb.sinc.ciclo()
    assert r["eventos"]["faturados"] == 1
    woo_pedido = amb.loja.pedidos[pedido["id"]]
    meta = {m["key"]: m["value"] for m in woo_pedido["meta_data"]}
    assert woo_pedido["status"] == "completed" and len(meta["_nfe_chave"]) == 44
    assert any(n["customer_note"] and meta["_nfe_chave"] in n["note"] for n in amb.loja.notas[pedido["id"]])


def test_cancelamento_na_loja_cancela_no_hiper_e_libera_reserva(amb):
    amb.sinc.ciclo()
    item = amb.woo_item_da_variacao("Moletom Canguru", qtd=1)
    pedido = amb.comprar([item])
    amb.sinc.ciclo()
    reg = amb.banco.pedido(pedido["id"])
    amb.loja.pedidos[pedido["id"]]["status"] = "cancelled"
    r = amb.sinc.ciclo()
    assert r["cancelamentos"] == {"cancelados": 1}
    assert amb.hiper.pedido(reg["hiper_id"]).cancelado
    assert amb.hiper.produtos[amb.banco.hiper_id_por_woo(item["variation_id"])]["reservado"] == 0


def test_cancelar_pedido_faturado_exige_acao_manual(amb):
    amb.sinc.ciclo()
    pedido = amb.comprar([amb.woo_item_simples("Boné Aba Curva")])
    amb.sinc.ciclo()
    reg = amb.banco.pedido(pedido["id"])
    amb.http_hiper.post(f"/simulador/pedidos/{reg['hiper_id']}/faturar")
    amb.sinc.ciclo()
    amb.loja.pedidos[pedido["id"]]["status"] = "refunded"
    amb.sinc.ciclo()
    assert amb.banco.pedido(pedido["id"])["status"] == "cancelar_manual"
    assert not amb.hiper.pedido(reg["hiper_id"]).cancelado


def test_falha_no_post_vira_incerto_e_nao_duplica(amb):
    amb.sinc.ciclo()
    pedido = amb.comprar([amb.woo_item_simples("Cinto de Couro Legítimo")])
    amb.sinc.hiper.gerar_token()
    amb.hiper.caos.taxa_500 = 1.0
    amb.sinc.enviar_pedidos()
    amb.hiper.caos.taxa_500 = 0.0
    assert amb.banco.pedido(pedido["id"])["status"] == "incerto"
    amb.sinc.ciclo()
    assert amb.banco.pedido(pedido["id"])["status"] == "incerto"
    assert len(amb.hiper.pedidos) == 0  # nada reenviado às cegas

    amb.sinc.marcar_para_reenvio(pedido["id"])
    amb.sinc.ciclo()
    assert amb.banco.pedido(pedido["id"])["status"] == "enviado" and len(amb.hiper.pedidos) == 1


def test_pedido_sem_cpf_fica_em_erro_com_motivo(amb):
    amb.sinc.ciclo()
    pedido = amb.comprar([amb.woo_item_simples("Boné Aba Curva")], cpf="")
    amb.sinc.ciclo()
    reg = amb.banco.pedido(pedido["id"])
    assert reg["status"] == "erro" and "CPF" in reg["erro"]


def test_resiste_a_429_e_latencia(amb):
    amb.hiper.caos.taxa_429 = 0.3
    r = amb.sinc.ciclo()
    amb.hiper.caos.taxa_429 = 0
    assert "erro" not in r["catalogo"]
    assert len(amb.loja.produtos) == len(produtos_visiveis(amb.hiper))


def test_produto_que_falha_vira_pendencia_e_se_resolve(amb):
    # ocupa o SKU de um produto do Hiper com outro produto na loja → o WooCommerce recusa
    bone = next(p for p in amb.hiper.produtos.values() if p["nome"] == "Boné Aba Curva")
    conflito = amb.http_woo.post("/wp-json/wc/v3/products", auth=(amb.config.woo_consumer_key, amb.config.woo_consumer_secret),
                                 json={"name": "Outro", "type": "variable", "sku": "X"}).json()
    amb.loja.produtos[conflito["id"]]["sku"] = bone["codigoDeBarras"]
    amb.sinc.woo.produto_por_sku = lambda sku: None  # força o caminho de criação

    amb.sinc.ciclo()
    pend = amb.banco.pendencias()
    assert [p["hiper_id"] for p in pend] == [bone["id"]]
    assert amb.banco.ler("ponto_produtos") == amb.hiper.ponto  # o ponto avança; o item fica guardado

    amb.loja.produtos[conflito["id"]]["sku"] = "LIVRE"
    amb.sinc.ciclo()
    assert amb.banco.pendencias() == []
    assert any(p["name"] == "Boné Aba Curva" for p in amb.loja.produtos.values())


def test_banco_local_perdido_nao_duplica_produtos(amb):
    amb.sinc.ciclo()
    total = len(amb.loja.produtos)
    variacoes = sum(len(v) for v in amb.loja.variacoes.values())
    novo = amb.novo_sincronizador(Banco(":memory:"))
    r = novo.ciclo()
    assert r["catalogo"]["revinculados"] == total
    assert len(amb.loja.produtos) == total
    assert sum(len(v) for v in amb.loja.variacoes.values()) == variacoes


def test_painel_e_webhook(amb):
    from fastapi.testclient import TestClient
    from conector.painel import criar_painel
    painel = TestClient(criar_painel(amb.sinc))
    assert painel.post("/api/sincronizar").status_code == 200
    amb.comprar([amb.woo_item_simples("Boné Aba Curva")], cupom="DESC15")
    painel.post("/api/sincronizar")
    status = painel.get("/api/status").json()
    assert status["pedidos_por_status"]["enviado"] == 1
    csv = painel.get("/conciliacao.csv").text
    assert "DESC15" in csv and csv.splitlines()[0].startswith("numero;status")
    assert painel.post("/webhooks/hiper", json={"tipo": 1, "descricao": "Produto alterado"}).json() == {"ok": True}
    assert painel.get("/").status_code == 200
