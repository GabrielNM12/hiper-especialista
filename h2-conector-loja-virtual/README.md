# H2 · Conector Hiper ↔ WooCommerce

Serviço que mantém uma loja WooCommerce sincronizada com o Hiper Gestão pela API personalizada:

- **Catálogo e estoque** do Hiper para a loja (produtos com grade viram produtos variáveis);
- **Pedidos pagos** da loja para o Hiper como pedido de venda, com desconto rateado e conciliação;
- **NF-e emitida** no Hiper volta para a loja: pedido concluído e chave de acesso enviada ao cliente;
- **Cancelamentos** da loja propagados ao Hiper.

![Painel do conector](../docs/img/h2-painel-conector.png)

## Demonstração em um comando

```bash
pip install -r requirements.txt
python -m conector demo
```

Sobe três servidores: o mock do Hiper (H1), uma loja WooCommerce simulada e o conector.

| Endereço | O que é |
|---|---|
| http://localhost:8100/loja | Vitrine de teste (cupons `BEMVINDO10` e `DESC15`) |
| http://localhost:8000/gestao | Retaguarda simulada do Hiper: veja o pedido chegar e clique em **Faturar** |
| http://localhost:8200 | Painel do conector: pedidos, conciliação, pendências e auditoria |

Roteiro de 2 minutos: compre 3 camisetas com o cupom `BEMVINDO10` e pague em 3x no cartão → o pedido aparece
na retaguarda com o desconto embutido no preço de cada item → **Faturar** → o Hiper avisa o conector por webhook e,
cerca de 1 segundo depois, o pedido na loja está `completed` com a chave da NF-e numa nota para o cliente.

## Usando com o Hiper e uma loja de verdade

1. No Hiper Gestão, contrate o app **Pacote do Varejo Digital/Loja virtual** e copie a **chave de segurança**
   (Vendas → Loja virtual). Marque "Enviar produto para a loja virtual" nos produtos que devem ir para o site.
2. No WooCommerce, gere uma chave de REST API com permissão de leitura e escrita e instale o plugin
   **Brazilian Market on WooCommerce** (CPF/CNPJ, número e bairro no checkout).
3. `cp .env.exemplo .env`, preencha e rode `python -m conector painel` (ou use o `Dockerfile`).

Se a loja for de SC, preencha `MARKETPLACE_CNPJ`/`MARKETPLACE_NOME`: o Hiper exige esses campos no pedido.

## Como resolve as peculiaridades do Hiper

| Peculiaridade | Tratamento |
|---|---|
| Pedido não tem campo de desconto (FAQ oficial) | O desconto de cupons, já rateado por linha pelo WooCommerce, vai em `precoUnitarioLiquido`; taxas negativas (ex.: desconto no Pix) também são rateadas. A diferença aparece na conciliação. |
| O Hiper multiplica preço unitário × quantidade | Quando o total da linha não divide em centavos (R$ 100,00 / 3), a linha é dividida em 2 × 33,33 + 1 × 33,34, e o total no Hiper fica idêntico ao cobrado. |
| Grade: o pedido precisa do id do filho | Pai vira produto variável (atributos de `tipoVariacaoA/B`), cada filho vira variação; o pedido usa sempre o id do filho. |
| Sem webhook garantido; sincronização por ponto | Usa o ponto de sincronização e a listagem paginada (a rota sem paginação fica inviável acima de ~1.000 produtos, segundo o FAQ). Se o webhook do Hiper estiver configurado, o ciclo é antecipado. |
| Não existe rota para buscar um produto por id | Produto que falha na loja vai para "pendências" com o JSON guardado; o ponto avança e o item é reprocessado no ciclo seguinte. |
| Estoque reservado e estoque negativo | Publica `estoque − reservado − estoque de segurança`, nunca abaixo de zero. Pedidos são enviados **antes** de publicar estoque, para a loja não "devolver" unidades recém-vendidas. |
| POST de pedido sem idempotência | Grava "enviando" antes do POST; timeout ou 5xx vira **incerto** e nunca é reenviado sozinho. O painel tem "Reenviar" e "Já existe no Hiper". |
| Cancelamento no Gestão não aparece na API | O conector cancela via API (`PUT /cancelar`) quando a loja cancela. Se o pedido já foi faturado, marca **ação manual**: cancelar NF-e ou fazer devolução é decisão fiscal. |
| Código IBGE obrigatório no endereço | Obtido pelo CEP no ViaCEP, com cache e tabela de reserva offline. |
| Meios de pagamento com IDs fixos | Mapa configurável gateway → id (Pix 12, crédito 4 com parcelas, etc.); gateway desconhecido gera aviso. |
| Banco local perdido | Produtos são revinculados pelo SKU, sem duplicar nada na loja. |

## Arquitetura

```
conector/
  sincronizador.py  ciclo: pedidos → cancelamentos → catálogo → estoque → eventos (NF-e)
  mapeamento.py     regras de tradução Hiper ↔ WooCommerce (funções puras)
  woocommerce.py    cliente REST v3 (lotes de 100, paginação, retentativa)
  banco.py          SQLite: pontos, mapa de IDs, pedidos, pendências, auditoria, cache de IBGE
  ibge.py           CEP → código IBGE
  painel.py         painel web, webhook do Hiper, CSV de conciliação, agendador
mock_woocommerce/   loja WooCommerce simulada (mesmas rotas e JSON da REST API v3) + vitrine
```

O cliente da API do Hiper fica em [`../hiper-sdk`](../hiper-sdk): renovação de token, backoff em 429/5xx e
proteção contra reenvio de pedido.

## Testes

```bash
pytest -q    # 26 testes: regras de mapeamento + fluxo completo com os dois mocks em processo
```

Os testes de integração cobrem: catálogo com grade, idempotência (segundo ciclo não altera nada), preço e estoque,
produto despublicado, pedido com cupom até a NF-e, cancelamento, cancelamento após faturar, falha no POST sem
duplicar, pedido sem CPF, resistência a 429, pendência que se resolve sozinha e recuperação após perder o banco.
