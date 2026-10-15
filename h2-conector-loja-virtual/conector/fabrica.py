"""Monta o sincronizador a partir da configuração."""

from __future__ import annotations

from typing import Optional

import httpx
from hiper_sdk import HiperClient

from .banco import Banco
from .config import Config
from .ibge import ResolvedorIbge
from .sincronizador import Sincronizador
from .woocommerce import WooClient


def criar_sincronizador(config: Config, *, http_hiper: Optional[httpx.Client] = None,
                        http_woo: Optional[httpx.Client] = None, banco: Optional[Banco] = None) -> Sincronizador:
    if not config.hiper_chave:
        raise SystemExit("Defina HIPER_CHAVE_DE_SEGURANCA (veja .env.exemplo).")
    banco = banco or Banco(config.banco)
    hiper = HiperClient(config.hiper_chave, base_url=config.hiper_url, http=http_hiper)
    woo = WooClient(config.woo_url, config.woo_consumer_key, config.woo_consumer_secret, http=http_woo)
    return Sincronizador(config, banco, hiper, woo, ResolvedorIbge(banco, usar_viacep=config.usar_viacep))
