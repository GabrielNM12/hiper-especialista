"""Gera a coleção Postman (v2.1) do mock. Funciona também contra a API real: troque {{baseUrl}} e {{chaveDeSeguranca}}."""
import json
from pathlib import Path

AUTH = {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]}


def req(nome, metodo, caminho, *, corpo=None, testes=None, auth=True, descricao="", pre=None):
    partes = caminho.split("?")
    url = {"raw": "{{baseUrl}}" + caminho, "host": ["{{baseUrl}}"], "path": partes[0].strip("/").split("/")}
    if len(partes) > 1:
        url["query"] = [{"key": k, "value": v} for k, v in (q.split("=") for q in partes[1].split("&"))]
    item = {"name": nome, "request": {"method": metodo, "header": [], "url": url, "description": descricao}}
    if auth:
        item["request"]["auth"] = AUTH
    else:
        item["request"]["auth"] = {"type": "noauth"}
    if corpo is not None:
        item["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        item["request"]["body"] = {"mode": "raw", "raw": json.dumps(corpo, indent=2, ensure_ascii=False)}
    eventos = []
    if pre:
        eventos.append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": pre}})
    if testes:
        eventos.append({"listen": "test", "script": {"type": "text/javascript", "exec": testes}})
    if eventos:
        item["event"] = eventos
    return item


PEDIDO = {
    "cliente": {"documento": "19561920000", "email": "cliente@exemplo.com.br", "inscricaoEstadual": "",
                "nomeDoCliente": "Cliente do E-commerce", "nomeFantasia": ""},
    "enderecoDeCobranca": {"bairro": "Centro", "cep": "88801010", "codigoIbge": 4204608, "complemento": "",
                           "logradouro": "Rua Principal", "numero": "01"},
    "enderecoDeEntrega": {"bairro": "Centro", "cep": "88801010", "codigoIbge": 4204608, "complemento": "Sala 2",
                          "logradouro": "Rua Principal", "numero": "22"},
    "itens": [{"produtoId": "{{produtoId}}", "quantidade": 1, "precoUnitarioBruto": "{{preco}}", "precoUnitarioLiquido": "{{preco}}"}],
    "meiosDePagamento": [{"idMeioDePagamento": 12, "parcelas": 1, "valor": "{{totalPedido}}"}],
    "numeroPedidoDeVenda": "POSTMAN-1", "observacaoDoPedidoDeVenda": "", "valorDoFrete": 10,
    "Marketplace": {"Cnpj": "12605982000124", "Nome": "Loja Virtual"},
}
corpo_pedido = json.dumps(PEDIDO, indent=2, ensure_ascii=False)
for var in ("preco", "totalPedido"):
    corpo_pedido = corpo_pedido.replace(f'"{{{{{var}}}}}"', f"{{{{{var}}}}}")

colecao = {
    "info": {"name": "Hiper API personalizada (mock)", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
             "description": "Fluxo completo da API de e-commerce do Hiper. Rode na ordem. Variáveis são preenchidas pelos scripts de teste."},
    "variable": [
        {"key": "baseUrl", "value": "http://localhost:8000"},
        {"key": "chaveDeSeguranca", "value": "4d6f636b48697065724c6f6a614d6f646153756c4372696369756d6132303236"},
        {"key": "token", "value": ""}, {"key": "pontoDeSincronizacao", "value": "0"},
        {"key": "produtoId", "value": ""}, {"key": "preco", "value": "0"}, {"key": "totalPedido", "value": "0"},
        {"key": "pedidoId", "value": ""},
    ],
    "item": [
        {"name": "1. Autenticação", "item": [req("Gerar token", "GET", "/api/v1/auth/gerar-token/{{chaveDeSeguranca}}", auth=False, testes=[
            "pm.test('200', () => pm.response.to.have.status(200));",
            "pm.collectionVariables.set('token', pm.response.json().token);"])]},
        {"name": "2. Produtos", "item": [
            req("Produtos alterados desde o ponto", "GET", "/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao={{pontoDeSincronizacao}}", testes=[
                "const j = pm.response.json();",
                "pm.test('tem produtos', () => pm.expect(j.produtos.length).to.be.above(0));",
                "const filho = j.produtos.find(p => p.produtoPrimarioId !== '00000000-0000-0000-0000-000000000000' && p.quantidadeEmEstoque > 0);",
                "if (filho) { pm.collectionVariables.set('produtoId', filho.id); pm.collectionVariables.set('preco', filho.preco); pm.collectionVariables.set('totalPedido', (filho.preco + 10).toFixed(2)); }",
                "console.log('ponto atual', j.pontoDeSincronizacao);"]),
            req("Listagem paginada (com atacado)", "GET", "/api/v1/produtos/listagemDeProdutos?pontoDeSincronizacao=0&pagina=1"),
            req("Categorias", "GET", "/api/v1/categorias/categorias-de-produto"),
        ]},
        {"name": "3. Estoque", "item": [
            req("Estoque de um produto", "GET", "/api/v1/estoques/pontoDeSincronizacao?produtoId={{produtoId}}"),
            req("Estoques alterados (paginado)", "GET", "/api/v1/estoques/estoquesDeProdutos?pontoDeSincronizacao=0&pagina=1"),
        ]},
        {"name": "4. Pedido de venda", "item": [
            {**req("Criar pedido", "POST", "/api/v1/pedido-de-venda", corpo={}, testes=[
                "pm.test('200', () => pm.response.to.have.status(200));",
                "pm.collectionVariables.set('pedidoId', pm.response.json().id);"])},
            req("Consultar eventos", "GET", "/api/v1/pedido-de-venda/eventos/{{pedidoId}}"),
            req("[Simulador] Faturar pedido no Gestão (emite NF-e)", "POST", "/simulador/pedidos/{{pedidoId}}/faturar", auth=False,
                descricao="O Hiper processa o pedido de forma assíncrona; espere alguns segundos antes de faturar.",
                pre=["// pedido é processado de forma assíncrona (situação 1 → 2)", "setTimeout(() => {}, 1500);"]),
            req("Consultar eventos (com chave da NF-e)", "GET", "/api/v1/pedido-de-venda/eventos/{{pedidoId}}", testes=[
                "const ev = pm.response.json().eventos;",
                "pm.test('tem documento fiscal', () => pm.expect(ev.some(e => e.chaveDocumentoFiscal)).to.be.true);"]),
            req("Cancelar pedido", "PUT", "/api/v1/pedido-de-venda/cancelar/{{pedidoId}}"),
        ]},
        {"name": "5. Webhook", "item": [
            req("Configurar webhook", "POST", "/api/v1/configuracao/webhook-config",
                corpo={"ativo": True, "url": "https://webhook.site/seu-id", "tipo": [1, 2, 3], "limiteDeTentativas": 3}),
            req("Tipos de webhook", "GET", "/api/v1/configuracao/webhook-tipos"),
        ]},
        {"name": "Simulador (só no mock)", "item": [
            req("Ligar falhas (429 em 30%)", "PUT", "/simulador/caos", auth=False, corpo={"taxa_429": 0.3}),
            req("Resetar mock", "POST", "/simulador/resetar", auth=False),
        ]},
    ],
}
colecao["item"][3]["item"][0]["request"]["body"]["raw"] = corpo_pedido
saida = Path(__file__).resolve().parents[1] / "postman" / "hiper-api-mock.postman_collection.json"
saida.write_text(json.dumps(colecao, indent=2, ensure_ascii=False), encoding="utf-8")
print("coleção gerada em", saida)
