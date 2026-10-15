# API personalizada do Hiper: guia prático

Resumo do que a Hiper publica sobre a API de e-commerce e do que aprendi construindo os projetos deste
repositório. Consultado em outubro de 2026; confira sempre as fontes oficiais.

Fontes oficiais:
[documentação técnica](https://ajuda.hiper.com.br/ajudahiper/s/article/integracao-api-personalizada) ·
[FAQ da API](https://ajuda.hiper.com.br/ajudahiper/s/article/FAQ-Integra%C3%A7%C3%A3o-via-API-personalizada-de-sua-loja-virtual) ·
[Swagger](https://ms-ecommerce.hiper.com.br/swagger/index.html)

## Visão geral

- Base: `https://ms-ecommerce.hiper.com.br`, REST + JSON.
- Feita para **lojas virtuais**; o FAQ diz que não serve para CRM.
- Habilitada no Hiper Gestão pelo app de loja virtual; a **chave de segurança** (64 caracteres) fica em
  Vendas → Loja virtual.

## Autenticação

`GET /api/v1/auth/gerar-token/{chaveDeSeguranca}` devolve um JWT. Nas demais rotas: `Authorization: Bearer <token>`.
No exemplo oficial o token vale 6 horas (exp − nbf = 21.600 s). Em 401, gere outro token.

## Rotas

| Rota | Uso |
|---|---|
| `GET /api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao=N` | Produtos alterados depois do ponto N (sem paginação) |
| `GET /api/v1/produtos/listagemDeProdutos?pontoDeSincronizacao=N&pagina=P` | Mesma coisa, paginada; é a única com `precoAtacado` |
| `GET /api/v1/estoques/pontoDeSincronizacao?produtoId=ID` | Estoque e reserva de um produto |
| `GET /api/v1/estoques/estoquesDeProdutos?pontoDeSincronizacao=N&pagina=P` | Estoques alterados, paginado (o campo de ponto vem como `pontoDeSituacao`) |
| `GET /api/v1/categorias/categorias-de-produto` | Categorias |
| `POST /api/v1/pedido-de-venda` | Cria pedido; responde com `id` e "em processamento" |
| `GET /api/v1/pedido-de-venda/eventos/{id}` | Situação, código `LV…`, eventos, chave e URL do XML da nota |
| `PUT /api/v1/pedido-de-venda/cancelar/{id}` | Cancela |
| `POST /api/v1/pedido-de-venda/atualizar-status` | Atualiza status |
| `GET/POST /api/v1/configuracao/webhook-*` | Configuração de webhooks (só no Swagger, não no artigo) |

## Regras que pegam o integrador

1. **Ponto de sincronização**: cada alteração no Hiper gera um ponto novo. Guarde o último ponto e consulte só o que
   mudou. A rota sem paginação fica inviável acima de ~1.000 produtos (FAQ): use a listagem paginada.
2. **Só vêm produtos marcados para a loja virtual.** Ao desmarcar, o produto volta com `removido: true`.
3. **Grade**: o pai traz os filhos em `variacao` (`tipoVariacaoA/B`, `nomeVariacaoA/B`); os filhos vêm com
   `produtoPrimarioId`. Os campos `cor` e `tamanho` estão descontinuados.
4. **Desconto não tem campo**: vai embutido em `precoUnitarioLiquido` por item. Desconto no total do pedido só
   pode ser dado no Gestão.
5. **Somente produtos do Hiper** são aceitos no pedido; cliente inexistente é **cadastrado automaticamente**.
6. **SC exige `Marketplace.Cnpj` e `Marketplace.Nome`** no pedido.
7. **Meios de pagamento** por id fixo: 1 dinheiro, 2 cheque, 3 devolução, 4 crédito, 5 débito, 6 crediário,
   11 voucher, 12 Pix.
8. **Cancelamento no Gestão não aparece na API.** Pedidos que vieram pela API devem ser cancelados pela API.
9. **Sem idempotência no POST** e sem busca por número do pedido: em timeout, não reenvie às cegas.
10. **429** existe e a documentação recomenda espera exponencial.
11. Nome do campo de observação: o artigo usa `observacaoDoPedidoDeVenda`, o Swagger usa `observacao`.

## O que não é público

Tabelas de `codigoDaSituacaoDeProcessamento`, `codigoDoTipoDeEvento`, tipos e formato do payload de webhook.
No mock (H1) esses valores são simulados e estão marcados como tal. Confirme com o suporte Hiper ou pelo
programa de parceiros (Hiperador) antes de ir para produção.
