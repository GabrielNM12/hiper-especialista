"""Envio das mensagens: WhatsApp Cloud API (produção) ou memória (simulador)."""

from __future__ import annotations

import itertools
import logging
import threading
from collections import defaultdict

import httpx

from .mensagens import Mensagem, para_cloud_api

log = logging.getLogger("bot.enviador")


class EnviadorSimulador:
    """Guarda os payloads exatamente como iriam para a Cloud API, para o chat de teste exibir."""

    def __init__(self) -> None:
        self.caixa: dict[str, list[dict]] = defaultdict(list)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def enviar(self, telefone: str, mensagens: list[Mensagem]) -> None:
        with self._lock:
            for m in mensagens:
                self.caixa[telefone].append({"seq": next(self._seq), "payload": para_cloud_api(telefone, m)})

    def desde(self, telefone: str, seq: int) -> list[dict]:
        return [m for m in self.caixa.get(telefone, []) if m["seq"] > seq]


class EnviadorCloudApi:
    def __init__(self, token: str, phone_number_id: str, versao: str = "v25.0", http: httpx.Client | None = None):
        self.url = f"https://graph.facebook.com/{versao}/{phone_number_id}/messages"
        self.http = http or httpx.Client(timeout=15, headers={"Authorization": f"Bearer {token}"})

    def enviar(self, telefone: str, mensagens: list[Mensagem]) -> None:
        for m in mensagens:  # em ordem: a próxima só sai depois da anterior ser aceita
            r = self.http.post(self.url, json=para_cloud_api(telefone, m))
            if r.status_code >= 400:
                log.error("Cloud API recusou mensagem (HTTP %s): %s", r.status_code, r.text[:300])
