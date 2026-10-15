import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[2]
for pasta in ("hiper-sdk", "h1-mock-api-hiper", "h4-bot-whatsapp"):
    sys.path.insert(0, str(RAIZ / pasta))

from bot.app import assinar, criar_app  # noqa: E402
from bot.banco import Banco  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.enderecos import Endereco  # noqa: E402
from bot.mensagens import Botoes, LinhaLista, Lista, para_cloud_api  # noqa: E402
from hiper_sdk import HiperClient  # noqa: E402
from mock_hiper.app import criar_app as criar_mock  # noqa: E402
from mock_hiper.estado import Config as ConfigMock  # noqa: E402
from mock_hiper.seed import CHAVE_PADRAO  # noqa: E402

CPF = "529.982.247-25"


class Cliente:
    """Simula um cliente conversando pelo simulador (mesmo caminho do webhook real)."""

    def __init__(self, bot: TestClient, telefone="5548999990001"):
        self.bot, self.tel, self.seq = bot, telefone, 0

    def diz(self, texto=None, resposta=None, titulo="") -> list[dict]:
        corpo = {"telefone": self.tel, "nome": "Ana Souza"}
        corpo.update({"resposta_id": resposta, "resposta_titulo": titulo} if resposta else {"texto": texto})
        assert self.bot.post("/simulador/enviar", json=corpo).status_code == 200
        novas = self.bot.get(f"/simulador/mensagens?telefone={self.tel}&desde={self.seq}").json()
        if novas:
            self.seq = novas[-1]["seq"]
        return [m["payload"] for m in novas]

    @staticmethod
    def texto(payloads) -> str:
        partes = []
        for p in payloads:
            if p["type"] == "text":
                partes.append(p["text"]["body"])
            elif p["type"] == "interactive":
                partes.append(p["interactive"]["body"]["text"])
        return "\n".join(partes)

    @staticmethod
    def ids(payloads) -> list[str]:
        saida = []
        for p in payloads:
            i = p.get("interactive", {})
            if i.get("type") == "button":
                saida += [b["reply"]["id"] for b in i["action"]["buttons"]]
            if i.get("type") == "list":
                saida += [r["id"] for s in i["action"]["sections"] for r in s["rows"]]
        return saida


@pytest.fixture
def amb(tmp_path):
    mock = criar_mock(ConfigMock(atraso_processamento_s=0), despachar_webhooks=False)
    http_mock = TestClient(mock)
    hiper = HiperClient(CHAVE_PADRAO, base_url="http://testserver", http=http_mock, dormir=lambda s: None)
    config = Config(hiper_url="http://testserver", hiper_chave=CHAVE_PADRAO, marketplace_cnpj="12605982000124",
                    usar_viacep=False, whatsapp_app_secret="segredo", whatsapp_verify_token="verifica", cache_catalogo_segundos=0)
    app = criar_app(config, hiper=hiper, banco=Banco(str(tmp_path / "bot.db")), notificar=False, processar_em_segundo_plano=False)
    app.state.conversa.buscar_endereco = lambda cep: (
        Endereco("88802000", 4204608, "Criciúma", "SC", "Rua Coronel Pedro Benedet", "Centro") if "88802" in cep.replace("-", "") else None)
    bot = TestClient(app)
    return {"bot": bot, "app": app, "mock": mock.state.estado, "http_mock": http_mock, "cliente": Cliente(bot)}


def comprar_tenis_38(c: Cliente, qtd=1):
    r = c.diz("tem tênis 38?")
    assert "Tênis Casual Couro" in c.texto(r) and "38" in c.texto(r)
    qtd_id = f"qtd:{qtd}"
    assert qtd_id in c.ids(r)
    return c.diz(resposta=qtd_id)


def fechar_pedido(c: Cliente, pagamento="pix"):
    r = c.diz(resposta="cart:finalizar")
    assert "nome completo" in c.texto(r)
    c.diz("Ana Souza")
    r = c.diz("123.456.789-00")
    assert "não parece válido" in c.texto(r)
    r = c.diz(CPF)
    assert "CEP" in c.texto(r)
    r = c.diz("88802-000")
    assert "Rua Coronel Pedro Benedet" in c.texto(r)
    r = c.diz("120 apto 3")
    assert f"pag:{pagamento}" in c.ids(r)
    r = c.diz(resposta=f"pag:{pagamento}")
    assert "Confira seu pedido" in c.texto(r) and "•••.982.247-••" in c.texto(r)
    return c.diz(resposta="conf:sim")


# ------------------------------------------------------------------ conversa
def test_saudacao_mostra_menu(amb):
    r = amb["cliente"].diz("oi")
    assert "Moda Sul" in Cliente.texto(r) and "menu:buscar" in Cliente.ids(r)


def test_busca_tolera_erro_de_digitacao(amb):
    c = amb["cliente"]
    c.diz("oi")
    r = c.diz("camizeta")
    assert "Camiseta Básica Algodão" in json.dumps(r, ensure_ascii=False)


def test_tamanho_na_busca_ja_seleciona_variacao(amb):
    r = comprar_tenis_38(amb["cliente"])
    assert "Adicionei 1× *Tênis Casual Couro 38*" in Cliente.texto(r)


def test_grade_com_muitas_opcoes_escolhe_cor_depois_tamanho(amb):
    c = amb["cliente"]
    c.diz("oi")
    camiseta = next(p for p in amb["mock"].produtos.values() if p["nome"] == "Camiseta Básica Algodão")
    r = c.diz(resposta=f"prod:{camiseta['id']}")
    ids = Cliente.ids(r)
    assert ids and all(i.startswith("opa:") for i in ids)  # primeiro a cor
    r = c.diz(resposta=ids[0])
    assert all(i.startswith("opc:") for i in Cliente.ids(r))  # depois o tamanho


def test_pedido_completo_chega_no_hiper(amb):
    c = amb["cliente"]
    comprar_tenis_38(c, qtd=2)
    r = fechar_pedido(c)
    assert "Pedido *WA00001* recebido" in Cliente.texto(r)
    pedido = next(iter(amb["mock"].pedidos.values()))
    corpo = pedido.corpo
    assert corpo["cliente"]["documento"] == "52998224725"
    assert corpo["enderecoDeEntrega"]["codigoIbge"] == 4204608 and corpo["enderecoDeEntrega"]["numero"] == "120"
    assert corpo["meiosDePagamento"][0]["idMeioDePagamento"] == 12
    assert corpo["numeroPedidoDeVenda"] == "WA00001"
    total = sum(i["quantidade"] * i["precoUnitarioLiquido"] for i in corpo["itens"]) + corpo["valorDoFrete"]
    assert corpo["valorDoFrete"] == 0.0  # 2 × 299,90 passa do frete grátis
    assert corpo["meiosDePagamento"][0]["valor"] == round(total, 2)
    filho = amb["mock"].produtos[corpo["itens"][0]["produtoId"]]
    assert filho["nomeVariacaoA"] == "38" and filho["reservado"] == 2


def test_dados_pessoais_saem_da_sessao_apos_pedido(amb):
    c = amb["cliente"]
    comprar_tenis_38(c)
    fechar_pedido(c)
    _, dados = amb["app"].state.banco.sessao(c.tel, 30)
    assert "checkout" not in dados and dados["carrinho"] == []


def test_estoque_conferido_em_tempo_real_antes_de_confirmar(amb):
    c = amb["cliente"]
    comprar_tenis_38(c, qtd=1)
    filho = next(p for p in amb["mock"].produtos.values() if p["nomeVariacaoA"] == "38" and "Tênis" in p["nome"])
    c.diz(resposta="cart:finalizar")
    c.diz("Ana Souza")
    c.diz(CPF)
    c.diz("88802000")
    c.diz("120")
    c.diz(resposta="pag:pix")
    amb["http_mock"].post(f"/simulador/produtos/{filho['id']}/estoque", json={"quantidade": 0})  # vendeu na loja física
    r = c.diz(resposta="conf:sim")
    assert "o estoque mudou" in Cliente.texto(r)
    assert not amb["mock"].pedidos


def test_notifica_codigo_e_nota_fiscal(amb):
    c = amb["cliente"]
    comprar_tenis_38(c)
    fechar_pedido(c)
    notificador = amb["app"].state.notificador
    notificador.verificar()
    pedido = next(iter(amb["mock"].pedidos.values()))
    amb["http_mock"].post(f"/simulador/pedidos/{pedido.id}/faturar")
    notificador.verificar()
    r = c.diz("meu pedido")
    texto = Cliente.texto(r)
    novas = amb["app"].state.simulador.caixa[c.tel]
    tudo = " ".join(json.dumps(m["payload"], ensure_ascii=False) for m in novas)
    assert "LV00000000001" in tudo and "Chave de acesso" in tudo
    assert "nota fiscal emitida" in texto


def test_atendente_silencia_bot(amb):
    c = amb["cliente"]
    c.diz("oi")
    r = c.diz("atendente")
    assert "chamar alguém" in Cliente.texto(r)
    assert c.diz("quanto custa a bolsa?") == []
    assert "menu:buscar" in Cliente.ids(c.diz("menu"))


def test_quantidade_maior_que_estoque(amb):
    c = amb["cliente"]
    c.diz("oi")
    oculos = next(p for p in amb["mock"].produtos.values() if p["nome"] == "Óculos de Sol Retrô")
    c.diz(resposta=f"prod:{oculos['id']}")
    r = c.diz(str(int(oculos["estoque"]) + 5))
    assert "Só tenho" in Cliente.texto(r)


def test_produto_inexistente(amb):
    c = amb["cliente"]
    c.diz("oi")
    assert "Não encontrei" in Cliente.texto(c.diz("geladeira frost free"))


# ------------------------------------------------------------------- webhook
def test_verificacao_do_webhook(amb):
    bot = amb["bot"]
    ok = bot.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verifica", "hub.challenge": "123"})
    assert ok.status_code == 200 and ok.text == "123"
    assert bot.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"}).status_code == 403


def webhook(texto, mid="wamid.1", tel="5548988887777"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"profile": {"name": "Bia"}, "wa_id": tel}],
        "messages": [{"from": tel, "id": mid, "timestamp": "1", "type": "text", "text": {"body": texto}}]}}]}]}


def test_assinatura_obrigatoria_e_mensagem_duplicada_ignorada(amb):
    bot, sim = amb["bot"], amb["app"].state.simulador
    corpo = json.dumps(webhook("oi")).encode()
    assert bot.post("/webhook", content=corpo, headers={"X-Hub-Signature-256": "sha256=errada"}).status_code == 401
    cab = {"X-Hub-Signature-256": assinar("segredo", corpo), "Content-Type": "application/json"}
    assert bot.post("/webhook", content=corpo, headers=cab).status_code == 200
    assert bot.post("/webhook", content=corpo, headers=cab).status_code == 200  # reenvio da Meta
    assert len(sim.caixa["5548988887777"]) == 1


def test_mensagem_de_audio_tem_resposta_educada(amb):
    p = webhook("")
    p["entry"][0]["changes"][0]["value"]["messages"][0].update(type="audio", audio={"id": "x"})
    corpo = json.dumps(p).encode()
    amb["bot"].post("/webhook", content=corpo, headers={"X-Hub-Signature-256": assinar("segredo", corpo)})
    assert "só mensagens de texto" in json.dumps(amb["app"].state.simulador.caixa["5548988887777"], ensure_ascii=False)


# ------------------------------------------------------- limites Cloud API
def test_payloads_respeitam_limites_da_cloud_api():
    botoes = para_cloud_api("55", Botoes("x" * 2000, [(f"id{i}", "Título muito comprido demais") for i in range(5)]))
    acao = botoes["interactive"]["action"]["buttons"]
    assert len(acao) == 3 and all(len(b["reply"]["title"]) <= 20 for b in acao)
    assert len(botoes["interactive"]["body"]["text"]) <= 1024
    lista = para_cloud_api("55", Lista("corpo", "Um rótulo de botão grande", [
        ("Seção com título bem comprido", [LinhaLista(f"r{i}", "Linha com título comprido demais", "d" * 100) for i in range(8)]),
        ("Outra", [LinhaLista(f"s{i}", "x") for i in range(8)])]))
    secoes = lista["interactive"]["action"]["sections"]
    assert sum(len(s["rows"]) for s in secoes) == 10
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 for s in secoes for r in s["rows"])
    assert len(lista["interactive"]["action"]["button"]) <= 20


def test_todas_as_respostas_do_fluxo_sao_validas(amb):
    c = amb["cliente"]
    comprar_tenis_38(c)
    fechar_pedido(c)
    for m in amb["app"].state.simulador.caixa[c.tel]:
        p = m["payload"]
        assert p["messaging_product"] == "whatsapp" and p["to"] == c.tel
        if p["type"] == "interactive" and p["interactive"]["type"] == "button":
            assert 1 <= len(p["interactive"]["action"]["buttons"]) <= 3
