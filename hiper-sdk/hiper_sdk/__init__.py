"""SDK Python para a API personalizada (e-commerce) do Hiper Gestão."""

from .cliente import HiperClient
from .constantes import MEIOS_DE_PAGAMENTO, URL_PRODUCAO
from .erros import HiperEnvioIncerto, HiperErro, HiperErroAPI, HiperNaoAutorizado
from .modelos import (
    ZERO_GUID,
    Cliente,
    ConfiguracaoDeWebhook,
    Endereco,
    EstoqueDoProduto,
    Evento,
    EventosDoPedido,
    ItemDoPedido,
    ListaDeEstoques,
    ListaDeProdutos,
    Marketplace,
    MeioDePagamento,
    PedidoDeVenda,
    Produto,
    RecebimentoDoPedido,
    Variacao,
)

__all__ = [name for name in dir() if not name.startswith("_")]
__version__ = "0.1.0"
