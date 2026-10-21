"""Linha de comando do conector.

    python -m conector demo      # sobe mock do Hiper (8000), loja WooCommerce simulada (8100) e o conector (8200)
    python -m conector painel    # conector + painel, usando a configuração do .env
    python -m conector ciclo     # executa um ciclo e imprime o resultado
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

import uvicorn

RAIZ = Path(__file__).resolve().parents[2]
for pasta in ("hiper-sdk", "h1-mock-api-hiper"):
    caminho = str(RAIZ / pasta)
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

from .config import Config  # noqa: E402
from .fabrica import criar_sincronizador  # noqa: E402
from .painel import Agendador, criar_painel  # noqa: E402


def _servir(app, porta: int) -> threading.Thread:
    servidor = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=porta, log_level="warning"))
    t = threading.Thread(target=servidor.run, daemon=True)
    t.start()
    return t


def painel(config: Config) -> None:
    sinc = criar_sincronizador(config)
    agendador = Agendador(sinc, config.intervalo_segundos)
    agendador.iniciar()
    print(f"Painel do conector em http://localhost:{config.porta_painel}")
    uvicorn.run(criar_painel(sinc, agendador), host="0.0.0.0", port=config.porta_painel, log_level="warning")


def demo() -> None:
    from mock_hiper.compartilhado import garantir_mock, registrar_webhook
    from mock_hiper.seed import CHAVE_PADRAO
    from mock_woocommerce.app import CK, CS, criar_app as criar_mock_woo

    novo = garantir_mock(8000)
    if not novo:
        print("Mock do Hiper já estava rodando na porta 8000: vou usar o mesmo.")
    _servir(criar_mock_woo(), 8100)
    time.sleep(1.5)

    config = Config(hiper_url="http://localhost:8000", hiper_chave=CHAVE_PADRAO, woo_url="http://localhost:8100",
                    woo_consumer_key=CK, woo_consumer_secret=CS, marketplace_cnpj="12605982000124",
                    marketplace_nome="Loja Virtual Moda Sul", banco="demo-conector.db", intervalo_segundos=15,
                    usar_viacep=False)
    Path(config.banco).unlink(missing_ok=True)
    sinc = criar_sincronizador(config)
    agendador = Agendador(sinc, config.intervalo_segundos)

    # O Hiper avisa o conector por webhook quando algo muda: o ciclo roda na hora, sem esperar o intervalo.
    registrar_webhook("http://localhost:8000", CHAVE_PADRAO, "http://localhost:8200/webhooks/hiper")
    agendador.iniciar()
    print("""
Demonstração no ar:
  Retaguarda simulada do Hiper ... http://localhost:8000/gestao
  Vitrine WooCommerce (teste) .... http://localhost:8100/loja
  Painel do conector ............. http://localhost:8200

Roteiro: compre na vitrine com o cupom BEMVINDO10 → veja o pedido chegar na retaguarda →
clique em Faturar → o pedido na loja fica "completed" com a chave da NF-e.
Ctrl+C para sair.""")
    uvicorn.run(criar_painel(sinc, agendador), host="0.0.0.0", port=8200, log_level="warning")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="conector", description="Conector Hiper ↔ WooCommerce")
    parser.add_argument("comando", choices=["demo", "painel", "ciclo"], nargs="?", default="painel")
    parser.add_argument("--env", default=".env", help="arquivo de configuração")
    args = parser.parse_args()
    if args.comando == "demo":
        demo()
        return
    config = Config.do_ambiente(args.env)
    if args.comando == "ciclo":
        print(json.dumps(criar_sincronizador(config).ciclo(), indent=2, ensure_ascii=False))
    else:
        painel(config)


if __name__ == "__main__":
    main()
