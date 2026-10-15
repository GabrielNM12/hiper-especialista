"""Analisador de XMLs fiscais (NF-e / NFC-e) para o pequeno varejo."""

from .analises import Resultado, analisar
from .leitor import ler

__all__ = ["analisar", "ler", "Resultado"]
__version__ = "1.0.0"
