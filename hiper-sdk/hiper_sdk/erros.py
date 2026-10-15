from __future__ import annotations

from typing import Optional


class HiperErro(Exception):
    """Erro base do SDK."""


class HiperErroAPI(HiperErro):
    """A API respondeu com status de erro."""

    def __init__(self, status: int, mensagem: Optional[str] = None, erros: Optional[list[str]] = None, corpo=None):
        self.status = status
        self.mensagem = mensagem
        self.erros = erros or []
        self.corpo = corpo
        detalhe = mensagem or "; ".join(self.erros) or "sem detalhe"
        super().__init__(f"HTTP {status}: {detalhe}")

    @property
    def recuperavel(self) -> bool:
        return self.status == 429 or self.status >= 500


class HiperNaoAutorizado(HiperErroAPI):
    """401 mesmo após gerar um token novo: chave de segurança inválida ou revogada."""


class HiperEnvioIncerto(HiperErro):
    """O POST pode ter sido processado, mas não recebemos confirmação (timeout ou 5xx).

    Reenviar às cegas pode duplicar o pedido no Hiper, porque a API não oferece
    chave de idempotência nem busca por numeroPedidoDeVenda. Quem chama deve marcar
    o pedido para conferência manual.
    """
