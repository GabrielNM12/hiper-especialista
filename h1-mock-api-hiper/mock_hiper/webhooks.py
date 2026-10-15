"""Entrega de webhooks com retentativas (limiteDeTentativas / intervaloEmSegundosEntreTentativas).

O Swagger oficial expõe a configuração de webhooks (/api/v1/configuracao/webhook-config). O formato
do payload não é público, então o mock usa um envelope simples e documentado no README.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

import httpx

from .estado import Estado, fmt, agora

log = logging.getLogger("mock_hiper.webhooks")


class Despachante:
    def __init__(self, estado: Estado, cliente: httpx.Client | None = None):
        self.estado = estado
        self.fila: "queue.Queue[tuple[dict, dict]]" = queue.Queue()
        self.http = cliente or httpx.Client(timeout=5)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="webhooks")
        self._thread.start()
        estado.ouvintes.append(self.enfileirar)

    def enfileirar(self, tipo: int, evento: dict) -> None:
        for config in list(self.estado.webhooks.values()):
            if config["ativo"] and (not config["tipo"] or tipo in config["tipo"]):
                self.fila.put((config, evento))

    def _loop(self) -> None:
        while True:
            config, evento = self.fila.get()
            self.entregar(config, evento)

    def entregar(self, config: dict, evento: dict) -> bool:
        tentativas = max(1, int(config.get("limiteDeTentativas") or 1))
        intervalo = max(0, int(config.get("intervaloEmSegundosEntreTentativas") or 0))
        for n in range(1, tentativas + 1):
            registro = {"url": config["url"], "tipo": evento["tipo"], "tentativa": n, "data": fmt(agora())}
            try:
                r = self.http.post(config["url"], json=evento)
                registro["status"] = r.status_code
                ok = 200 <= r.status_code < 300
            except httpx.HTTPError as exc:
                registro["status"] = f"erro: {exc.__class__.__name__}"
                ok = False
            self.estado.entregas_webhook.append(registro)
            del self.estado.entregas_webhook[:-200]
            if ok:
                return True
            time.sleep(intervalo)
        return False
