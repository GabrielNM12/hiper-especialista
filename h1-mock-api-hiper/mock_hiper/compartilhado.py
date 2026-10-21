"""Sobe o mock na porta indicada, ou reaproveita um que já esteja rodando.

Permite rodar as demonstrações do conector (H2) e do bot (H4) ao mesmo tempo, compartilhando o mesmo
"Hiper": os pedidos da loja virtual e do WhatsApp aparecem na mesma retaguarda, como numa loja real.
"""

from __future__ import annotations

import threading
import time

import httpx
import uvicorn


def mock_no_ar(url: str) -> bool:
    try:
        return httpx.get(f"{url}/simulador/info", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def garantir_mock(porta: int = 8000) -> bool:
    """Devolve True se iniciou um mock novo, False se reaproveitou um existente."""
    url = f"http://localhost:{porta}"
    if mock_no_ar(url):
        return False
    from .app import criar_app
    servidor = uvicorn.Server(uvicorn.Config(criar_app(), host="0.0.0.0", port=porta, log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(40):
        if mock_no_ar(url):
            return True
        time.sleep(0.25)
    raise SystemExit(f"Não consegui iniciar o mock do Hiper na porta {porta}. Ela está ocupada por outro programa?")


def registrar_webhook(url_mock: str, chave: str, url_destino: str) -> None:
    """Configura o webhook pela própria API (rota oficial /configuracao/webhook-config)."""
    token = httpx.get(f"{url_mock}/api/v1/auth/gerar-token/{chave}", timeout=5).json()["token"]
    httpx.post(f"{url_mock}/api/v1/configuracao/webhook-config", headers={"Authorization": f"Bearer {token}"},
               json={"ativo": True, "url": url_destino, "tipo": [1, 2, 3], "limiteDeTentativas": 3,
                     "intervaloEmSegundosEntreTentativas": 2}, timeout=5)
