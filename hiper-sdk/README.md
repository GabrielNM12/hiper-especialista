# hiper-sdk

Cliente Python para a API personalizada (e-commerce) do Hiper Gestão, usado pelo conector (H2) e pelo bot (H4).

```python
from hiper_sdk import HiperClient

with HiperClient("SUA_CHAVE_DE_SEGURANCA") as hiper:          # produção: ms-ecommerce.hiper.com.br
    lote = hiper.produtos_desde(0)
    for p in lote.produtos:
        print(p.codigo, p.nome, p.preco, "grade" if p.eh_pai_de_grade else "")
    ultimo_ponto = lote.pontoDeSincronizacao                  # guarde para a próxima consulta
```

Para usar o mock: `HiperClient(chave, base_url="http://localhost:8000")`.

## O que ele resolve

- **Token**: gera, guarda e renova 5 minutos antes de expirar; em 401 gera outro e repete a chamada uma vez.
- **429 / 5xx**: espera exponencial com jitter, respeitando `Retry-After` (recomendação da documentação oficial). Vale também para a geração do token.
- **Pedido não duplica**: o POST do pedido não é repetido em timeout ou 5xx, porque a API não tem chave de idempotência nem busca por número do pedido. Nesses casos levanta `HiperEnvioIncerto` para quem chama conferir antes de reenviar.
- **Modelos tipados** (pydantic) com os nomes de campos do Swagger e `extra="allow"` para não quebrar quando a Hiper adicionar campos.
- **Paginação**: `iterar_listagem_de_produtos()` e `iterar_estoques()`.
- **Diferença de nomes**: o artigo de ajuda chama a observação do pedido de `observacaoDoPedidoDeVenda`, o Swagger de `observacao`; o SDK envia os dois.

```bash
pip install -e ".[dev]"
pytest -q     # roda contra o mock H1 em processo, sem rede
```
