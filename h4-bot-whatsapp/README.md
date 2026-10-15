# H4 · Bot de WhatsApp: estoque e pedidos no Hiper

O cliente pergunta "tem tênis 38?", o bot responde com preço e estoque reais do Hiper, monta o carrinho, coleta
os dados de entrega e cria o **pedido de venda no Hiper**. Quando a loja fatura, o cliente recebe a chave da NF-e
no próprio chat.

Funciona com a **WhatsApp Cloud API** (Meta) e traz um **simulador de chat** que envia webhooks no mesmo formato da
Meta para o mesmo código de produção, para demonstrar sem número de WhatsApp.

![Simulador: pedido faturado e chave da NF-e no chat](../docs/img/h4-simulador-nfe.png)

## Demonstração

```bash
pip install -r requirements.txt
python -m bot demo
```

| Endereço | O que é |
|---|---|
| http://localhost:8300 | Simulador do chat (à direita, o webhook recebido e o payload que iria para a Meta) |
| http://localhost:8000/gestao | Retaguarda simulada do Hiper (H1): veja o pedido chegar e clique em **Faturar** |

Roteiro: `oi` → `camizeta M` (com erro de digitação) → escolha a cor → `tem tênis 38?` → finalize com o CPF de teste
`529.982.247-25` e CEP `88802-000` → Pix → Confirmar → na retaguarda, **Faturar** → o cliente recebe a nota no chat.

## O que ele faz

- **Busca tolerante** (rapidfuzz): "camizeta", "tenis", "bolsa preta" encontram o produto; tamanhos e numerações
  na frase ("tênis 38", "camiseta M") já filtram as variações com estoque.
- **Grade inteligente**: até 10 combinações vão numa lista; acima disso pergunta primeiro a cor, depois o tamanho
  (limites da lista do WhatsApp: 10 linhas, títulos de 24 caracteres).
- **Estoque em duas velocidades**: catálogo em cache atualizado pelo ponto de sincronização (barato) e estoque
  conferido **em tempo real** no Hiper no momento de confirmar, porque a loja física pode ter vendido a última peça
  durante a conversa. Se faltar, o carrinho é ajustado e o cliente é avisado.
- **Checkout**: nome (sugere o nome do perfil), CPF com dígito verificador, CEP → endereço e código IBGE (ViaCEP,
  com reserva offline), número e forma de pagamento; frete fixo com frete grátis acima de um valor.
- **Pedido no Hiper** com cliente, endereço, itens (id da variação), meio de pagamento da tabela oficial
  (Pix 12, cartão 4, dinheiro 1), número `WA00001` e marketplace (obrigatório em SC).
- **Acompanhamento**: avisa quando o pedido ganha o código `LV…` e quando a NF-e é emitida.
- **Atendente humano**: "atendente" silencia o bot até o cliente escrever "menu".

## Detalhes de produção

| Tema | Tratamento |
|---|---|
| Verificação do webhook | `GET /webhook` responde `hub.challenge` só com o `WHATSAPP_VERIFY_TOKEN` correto |
| Autenticidade | Valida `X-Hub-Signature-256` (HMAC-SHA256 do corpo com o app secret) e recusa com 401 |
| Reenvio da Meta | Cada `message.id` é processado uma única vez (a Meta reenvia se não receber 200 a tempo) |
| Resposta rápida | O webhook devolve 200 na hora e processa em segundo plano; mensagens do mesmo número em ordem |
| Limites da Cloud API | Botões (3, 20 caracteres), listas (10 linhas, 24/72 caracteres), corpo (1024) aplicados na conversão |
| Áudio, imagem, figurinha | Resposta educada pedindo texto |
| LGPD | CPF mascarado no resumo e nos logs; CPF e endereço apagados da sessão após o pedido; sessão expira em 30 min |
| Janela de 24 h | Avisos de NF-e são mensagens livres, válidas até 24 h após a última mensagem do cliente. Fora disso, use um template aprovado |

## Usando com a Cloud API de verdade

1. No [Meta for Developers](https://developers.facebook.com/), crie um app do tipo Business com o produto WhatsApp
   e anote o **token**, o **Phone number ID** e o **App secret**.
2. Exponha o bot com HTTPS (ex.: `cloudflared tunnel --url http://localhost:8300`) e cadastre
   `https://SEU-ENDERECO/webhook` com o **verify token** que você escolheu, assinando o campo `messages`.
3. `cp .env.exemplo .env`, preencha com `BOT_MODO=cloud` e a chave do Hiper, e rode `python -m bot`.

## Estrutura

```
bot/
  app.py          webhook (verificação, assinatura, idempotência), simulador, rotas auxiliares
  conversa.py     máquina de estados: busca → variação → quantidade → carrinho → checkout → pedido
  catalogo.py     cache incremental do Hiper e busca tolerante
  mensagens.py    mensagens independentes do provedor + conversão para a Cloud API (com limites)
  enviador.py     Cloud API (graph.facebook.com/v25.0) ou caixa do simulador
  notificador.py  acompanha eventos do pedido e avisa o cliente
  banco.py        sessões, mensagens processadas e pedidos (SQLite)
  enderecos.py    CEP → endereço + código IBGE
  simulador.html  chat de teste com os payloads à vista
```

## Testes

```bash
pytest -q   # 16 testes: conversas completas contra o mock do Hiper, webhook, segurança e limites da Cloud API
```

Próximos passos naturais: cobrança Pix dinâmica pelo PSP da loja (QR code no chat), templates aprovados para
avisos fora da janela de 24 h e encaminhamento para atendente em uma central (ex.: Chatwoot).
