"""
    python -m bot demo     # mock do Hiper (8000) + bot com simulador de chat (8300)
    python -m bot          # usa o .env (Cloud API real ou simulador)
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import uvicorn

RAIZ = Path(__file__).resolve().parents[2]
for pasta in ("hiper-sdk", "h1-mock-api-hiper"):
    if str(RAIZ / pasta) not in sys.path:
        sys.path.insert(0, str(RAIZ / pasta))

from .app import criar_app  # noqa: E402
from .config import Config  # noqa: E402


def demo() -> None:
    from mock_hiper.compartilhado import garantir_mock
    from mock_hiper.seed import CHAVE_PADRAO
    if not garantir_mock(8000):
        print("Mock do Hiper já estava rodando na porta 8000: vou usar o mesmo.")
    banco = Path("demo-bot.db")
    banco.unlink(missing_ok=True)
    config = Config(hiper_url="http://localhost:8000", hiper_chave=CHAVE_PADRAO, marketplace_cnpj="12605982000124",
                    marketplace_nome="WhatsApp Moda Sul", banco=str(banco), usar_viacep=True, intervalo_notificacoes=5,
                    cache_catalogo_segundos=10)
    print("""
Demonstração no ar:
  Simulador do bot ............... http://localhost:8300
  Retaguarda simulada do Hiper ... http://localhost:8000/gestao

Roteiro: escreva "tem tênis 38?" → escolha a quantidade → finalize (CPF de teste 529.982.247-25, CEP 88802-000)
→ na retaguarda, clique em Faturar → o cliente recebe a chave da NF-e no chat.""")
    uvicorn.run(criar_app(config), host="0.0.0.0", port=8300, log_level="warning")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("comando", nargs="?", default="servir", choices=["servir", "demo"])
    p.add_argument("--env", default=".env")
    a = p.parse_args()
    if a.comando == "demo":
        demo()
        return
    config = Config.do_ambiente(a.env)
    if not config.hiper_chave:
        sys.exit("Defina HIPER_CHAVE_DE_SEGURANCA (veja .env.exemplo).")
    uvicorn.run(criar_app(config), host="0.0.0.0", port=config.porta, log_level="info")


if __name__ == "__main__":
    main()
