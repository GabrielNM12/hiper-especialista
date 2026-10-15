# H1 · Mock server da API Hiper

Servidor que imita a **API personalizada (e-commerce) do Hiper Gestão** com o mesmo contrato da API real:
mesmas rotas, parâmetros, nomes de campos e códigos HTTP do
[Swagger oficial](https://ms-ecommerce.hiper.com.br/swagger/index.html) e da
[documentação técnica](https://ajuda.hiper.com.br/ajudahiper/s/article/integracao-api-personalizada).

Serve para desenvolver e testar integrações (H2, H4) sem conta no Hiper, e para demonstrar as regras que pegam
integradores de surpresa.

## Rodando

```bash
pip install -r requirements.txt
python -m mock_hiper            # http://localhost:8000
```

| Endereço | O que é |
|---|---|
| `http://localhost:8000/gestao` | Retaguarda simulada: alterar preço/estoque, faturar e cancelar pedidos, ver as chamadas da integração, ligar falhas |
| `http://localhost:8000/docs` | Swagger do mock |
| `postman/hiper-api-mock.postman_collection.json` | Coleção com o fluxo completo (importe no Postman ou rode `newman run`) |

**Chave de segurança de exemplo** (64 caracteres, como a real):
`4d6f636b48697065724c6f6a614d6f646153756c4372696369756d6132303236`

```bash
TOKEN=$(curl -s localhost:8000/api/v1/auth/gerar-token/4d6f636b48697065724c6f6a614d6f646153756c4372696369756d6132303236 | jq -r .token)
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao=0" | jq '.produtos[0]'
```

## Rotas (iguais às da API real)

| Método | Rota | Observação |
|---|---|---|
| GET | `/api/v1/auth/gerar-token/{chaveDeSeguranca}` | JWT válido por 6 h (mesma janela do exemplo oficial) |
| GET | `/api/v1/produtos/pontoDeSincronizacao?pontoDeSincronizacao=N` | Tudo que mudou depois do ponto N, sem paginação |
| GET | `/api/v1/produtos/listagemDeProdutos?pontoDeSincronizacao=N&pagina=P` | Paginada; única que traz `precoAtacado` |
| GET | `/api/v1/estoques/pontoDeSincronizacao?produtoId=ID` | Estoque e reserva de um produto |
| GET | `/api/v1/estoques/estoquesDeProdutos?pontoDeSincronizacao=N&pagina=P` | Estoques alterados, paginado |
| GET | `/api/v1/categorias/categorias-de-produto` | |
| POST | `/api/v1/pedido-de-venda` | Cria pedido; resposta "Pedido recebido e em processamento." |
| GET | `/api/v1/pedido-de-venda/eventos/{id}` | Situação, código `LV…`, eventos e chave da NF-e |
| PUT | `/api/v1/pedido-de-venda/cancelar/{id}` | |
| POST | `/api/v1/pedido-de-venda/atualizar-status` | |
| GET/POST | `/api/v1/configuracao/webhook-*` | Configuração e disparo de webhooks com retentativa |

## Regras reais que o mock reproduz

Cada uma tem teste automatizado em `tests/test_regras.py`.

- **Ponto de sincronização**: toda alteração gera um ponto novo; consultar com o último ponto devolve só o que mudou.
- **Flag "Enviar produto para a loja virtual"**: produtos sem a flag não aparecem; ao desmarcar, o produto volta com `removido: true` para o e-commerce despublicar.
- **Grade**: o pai traz os filhos em `variacao` (com `tipoVariacaoA/B` e `nomeVariacaoA/B`) e estoque zero; cada filho vem com `produtoPrimarioId` apontando para o pai e é o id que vai no pedido.
- **Estoque reservado**: o pedido reserva (`quantidadeEmEstoqueReservado`); faturar baixa o estoque e libera a reserva.
- **Pedido só aceita produtos do Hiper** e rejeita o pai da grade.
- **Desconto só por item**: não há campo de desconto no pedido; vai embutido em `precoUnitarioLiquido` (vira `vDesc` na NF-e).
- **SC exige `Marketplace.Cnpj` e `Marketplace.Nome`** no pedido.
- **Meios de pagamento**: só os IDs da tabela oficial (1, 2, 3, 4, 5, 6, 11, 12).
- **Cliente inexistente é cadastrado automaticamente.**
- **Cancelamento feito no Gestão não aparece na API** (só cancelamentos via API são refletidos). O botão "Cancelar no Gestão" demonstra isso.
- **Processamento assíncrono**: o pedido fica "aguardando" e só depois ganha o código `LV…`.
- **401 sem corpo** quando falta token; **429 com `Retry-After`** e **503** sob demanda para testar resiliência.

Ao faturar, o mock gera o XML de uma **NF-e modelo 55** (homologação, sem valor fiscal): CFOP 5102 dentro de SC,
6108 para consumidor final em outra UF, CSOSN 102 (Simples Nacional), frete rateado por item, `infIntermed` com o
marketplace. O link fica em `urlArquivoXml` do evento, como na API real. Esses XMLs alimentam o H3.

## O que é simulado (não publicado pela Hiper)

| Item | Valor no mock |
|---|---|
| `codigoDaSituacaoDeProcessamento` | 1 aguardando · 2 processado · 3 erro |
| `codigoDoTipoDeEvento` | 1 pedido gerado · 2 documento fiscal emitido · 3 cancelado · 4 status atualizado |
| Tipos de webhook e payload | 1 produto · 2 estoque · 3 pedido; envelope `{tipo, descricao, contaHiperId, data, dados}` |
| Resposta a chave inválida | 401 com `errors` |

Ao conectar na API real, confirme esses valores com o suporte Hiper.

## Mantendo o contrato em dia

`contrato/contrato_oficial.json` guarda rotas e campos de cada DTO do Swagger oficial. Os testes de
`tests/test_contrato.py` falham se o mock responder diferente. Para descobrir se a Hiper mudou a API:

```bash
python scripts/comparar_com_swagger_oficial.py
```

## Testes

```bash
pytest -q          # 23 testes de contrato e regras
newman run postman/hiper-api-mock.postman_collection.json --delay-request 300   # com o servidor rodando
```

## Estrutura

```
mock_hiper/
  app.py          rotas oficiais, simulador, middleware de falhas
  estado.py       regras de negócio e estado em memória
  seed.py         loja de moda de Criciúma: 14 produtos, 52 SKUs, grade, atacado, casos de borda
  nfe.py          XML da NF-e emitida no faturamento
  webhooks.py     disparo com retentativas
  gestao.html     retaguarda simulada
contrato/         campos oficiais por DTO
postman/          coleção
scripts/          gerar coleção, exportar OpenAPI, comparar com o Swagger oficial
```
