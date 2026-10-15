from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _carregar_env(caminho: Path) -> None:
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    hiper_url: str = "https://ms-ecommerce.hiper.com.br"
    hiper_chave: str = ""
    loja_nome: str = "Moda Sul"
    marketplace_cnpj: str = ""
    marketplace_nome: str = "WhatsApp"
    frete_fixo: float = 15.0
    frete_gratis_acima: float = 299.0
    # WhatsApp Cloud API
    modo: str = "simulador"  # simulador | cloud
    graph_versao: str = "v25.0"
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = "troque-este-token"
    # operação
    banco: str = "bot.db"
    cache_catalogo_segundos: int = 60
    sessao_expira_minutos: int = 30
    intervalo_notificacoes: int = 20
    usar_viacep: bool = True
    porta: int = 8300

    @classmethod
    def do_ambiente(cls, arquivo: str | None = ".env") -> "Config":
        if arquivo:
            _carregar_env(Path(arquivo))
        e = os.environ.get
        return cls(
            hiper_url=e("HIPER_URL", cls.hiper_url),
            hiper_chave=e("HIPER_CHAVE_DE_SEGURANCA", ""),
            loja_nome=e("LOJA_NOME", cls.loja_nome),
            marketplace_cnpj=e("MARKETPLACE_CNPJ", ""),
            marketplace_nome=e("MARKETPLACE_NOME", cls.marketplace_nome),
            frete_fixo=float(e("FRETE_FIXO", str(cls.frete_fixo))),
            frete_gratis_acima=float(e("FRETE_GRATIS_ACIMA", str(cls.frete_gratis_acima))),
            modo=e("BOT_MODO", cls.modo),
            graph_versao=e("GRAPH_VERSAO", cls.graph_versao),
            whatsapp_token=e("WHATSAPP_TOKEN", ""),
            whatsapp_phone_number_id=e("WHATSAPP_PHONE_NUMBER_ID", ""),
            whatsapp_app_secret=e("WHATSAPP_APP_SECRET", ""),
            whatsapp_verify_token=e("WHATSAPP_VERIFY_TOKEN", cls.whatsapp_verify_token),
            banco=e("BOT_BANCO", cls.banco),
            usar_viacep=e("USAR_VIACEP", "1") == "1",
            porta=int(e("PORTA", str(cls.porta))),
        )
