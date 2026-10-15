"""Mock da API personalizada (e-commerce) do Hiper Gestão."""

from .app import criar_app
from .seed import CHAVE_PADRAO

__all__ = ["criar_app", "CHAVE_PADRAO"]
