"""Configuração do conector, lida de variáveis de ambiente (veja .env.exemplo)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _carregar_env(caminho: Path) -> None:
    """Carrega um .env simples (CHAVE=valor) sem sobrescrever o ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


# gateway do WooCommerce → idMeioDePagamento do Hiper (tabela oficial: 1,2,3,4,5,6,11,12)
MAPA_PAGAMENTO_PADRAO = {
    "pix": 12,
    "woo-mercado-pago-pix": 12,
    "pagarme-pix": 12,
    "credit_card": 4,
    "stripe": 4,
    "pagseguro": 4,
    "woo-mercado-pago-custom": 4,
    "pagarme-credit-card": 4,
    "debit_card": 5,
    "cod": 1,
    "cheque": 2,
}


@dataclass
class Config:
    hiper_url: str = "https://ms-ecommerce.hiper.com.br"
    hiper_chave: str = ""
    woo_url: str = "http://localhost:8100"
    woo_consumer_key: str = ""
    woo_consumer_secret: str = ""
    marketplace_cnpj: str = ""
    marketplace_nome: str = "Loja Virtual"
    banco: str = "conector.db"
    intervalo_segundos: int = 60
    estoque_seguranca: int = 0
    mapa_pagamento: dict[str, int] = field(default_factory=lambda: dict(MAPA_PAGAMENTO_PADRAO))
    meio_pagamento_padrao: int = 4
    status_pedido_pago: tuple[str, ...] = ("processing",)
    status_pedido_cancelado: tuple[str, ...] = ("cancelled", "refunded")
    concluir_pedido_ao_faturar: bool = True
    usar_viacep: bool = True
    porta_painel: int = 8200
    pedidos_a_partir_de: str = ""  # ISO (ex.: 2026-10-01): ignora pedidos antigos na primeira execução
    max_tentativas_pedido: int = 10

    @classmethod
    def do_ambiente(cls, arquivo_env: str | None = ".env") -> "Config":
        if arquivo_env:
            _carregar_env(Path(arquivo_env))
        e = os.environ.get
        mapa = dict(MAPA_PAGAMENTO_PADRAO)
        if e("MAPA_PAGAMENTO"):
            mapa.update({k: int(v) for k, v in json.loads(e("MAPA_PAGAMENTO")).items()})
        return cls(
            hiper_url=e("HIPER_URL", cls.hiper_url),
            hiper_chave=e("HIPER_CHAVE_DE_SEGURANCA", ""),
            woo_url=e("WOO_URL", cls.woo_url),
            woo_consumer_key=e("WOO_CONSUMER_KEY", ""),
            woo_consumer_secret=e("WOO_CONSUMER_SECRET", ""),
            marketplace_cnpj=e("MARKETPLACE_CNPJ", ""),
            marketplace_nome=e("MARKETPLACE_NOME", cls.marketplace_nome),
            banco=e("CONECTOR_BANCO", cls.banco),
            intervalo_segundos=int(e("CONECTOR_INTERVALO", str(cls.intervalo_segundos))),
            estoque_seguranca=int(e("ESTOQUE_SEGURANCA", "0")),
            mapa_pagamento=mapa,
            meio_pagamento_padrao=int(e("MEIO_PAGAMENTO_PADRAO", str(cls.meio_pagamento_padrao))),
            concluir_pedido_ao_faturar=e("CONCLUIR_AO_FATURAR", "1") == "1",
            usar_viacep=e("USAR_VIACEP", "1") == "1",
            porta_painel=int(e("PORTA_PAINEL", str(cls.porta_painel))),
            pedidos_a_partir_de=e("PEDIDOS_A_PARTIR_DE", ""),
        )
