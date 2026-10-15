"""Servidor do bot: webhook da WhatsApp Cloud API + simulador de chat.

O simulador monta um webhook no MESMO formato que a Meta envia e o entrega à mesma rota /webhook,
assinado com o app secret. Assim a demonstração exercita exatamente o código de produção.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from hiper_sdk import HiperClient
from pydantic import BaseModel

from .banco import Banco
from .catalogo import Catalogo
from .config import Config
from .conversa import Conversa, Entrada, mascarar
from .enviador import EnviadorCloudApi, EnviadorSimulador
from .mensagens import Texto
from .notificador import Notificador

log = logging.getLogger("bot")
PASTA = Path(__file__).parent


def assinar(segredo: str, corpo: bytes) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


class Simulacao(BaseModel):
    telefone: str = "5548999990001"
    nome: str = "Ana Souza"
    texto: str = ""
    resposta_id: Optional[str] = None
    resposta_titulo: Optional[str] = None
    tipo_resposta: str = "button_reply"  # button_reply | list_reply


def extrair_mensagens(payload: dict) -> list[dict]:
    """Normaliza o webhook da Cloud API em [{id, de, nome, texto, resposta_id}]."""
    saida = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            nomes = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in valor.get("contacts", [])}
            for m in valor.get("messages", []):
                item = {"id": m.get("id"), "de": m.get("from"), "nome": nomes.get(m.get("from"), ""), "texto": "",
                        "resposta_id": None, "tipo": m.get("type")}
                if m.get("type") == "text":
                    item["texto"] = m.get("text", {}).get("body", "")
                elif m.get("type") == "interactive":
                    i = m.get("interactive", {})
                    r = i.get("button_reply") or i.get("list_reply") or {}
                    item["resposta_id"], item["texto"] = r.get("id"), r.get("title", "")
                elif m.get("type") == "button":  # resposta a botão de template
                    item["texto"] = m.get("button", {}).get("text", "")
                saida.append(item)
    return saida


def criar_app(config: Config, *, hiper: Optional[HiperClient] = None, banco: Optional[Banco] = None,
              notificar: bool = True, processar_em_segundo_plano: bool = True) -> FastAPI:
    hiper = hiper or HiperClient(config.hiper_chave, base_url=config.hiper_url)
    banco = banco or Banco(config.banco)
    catalogo = Catalogo(hiper, ttl=config.cache_catalogo_segundos)
    conversa = Conversa(config, banco, catalogo, hiper)
    simulador = EnviadorSimulador()
    enviador = simulador if config.modo == "simulador" else EnviadorCloudApi(
        config.whatsapp_token, config.whatsapp_phone_number_id, config.graph_versao)
    notificador = Notificador(banco, hiper, enviador, config.intervalo_notificacoes)
    if notificar:
        notificador.iniciar()

    app = FastAPI(title="Bot de WhatsApp · estoque e pedidos no Hiper", version="1.0.0")
    app.state.conversa, app.state.simulador, app.state.notificador, app.state.banco = conversa, simulador, notificador, banco
    app.state.catalogo = catalogo
    locks: dict[str, threading.Lock] = {}

    def tratar(msg: dict) -> None:
        # Um cliente por vez: mensagens do mesmo número são processadas em ordem.
        with locks.setdefault(msg["de"], threading.Lock()):
            if msg["tipo"] not in ("text", "interactive", "button"):
                enviador.enviar(msg["de"], [Texto("Por enquanto eu entendo só mensagens de texto. 🙂")])
                return
            inicio = time.perf_counter()
            saida = conversa.processar(msg["de"], Entrada(msg["texto"], msg["resposta_id"]), msg["nome"])
            enviador.enviar(msg["de"], saida)
            log.info("mensagem de %s respondida em %.0f ms", mascarar(msg["de"]), (time.perf_counter() - inicio) * 1000)

    # ------------------------------------------------------- webhook Meta
    @app.get("/webhook", response_class=PlainTextResponse)
    def verificar(modo: str = Query(alias="hub.mode", default=""), token: str = Query(alias="hub.verify_token", default=""),
                  desafio: str = Query(alias="hub.challenge", default="")):
        if modo == "subscribe" and hmac.compare_digest(token, config.whatsapp_verify_token):
            return desafio
        raise HTTPException(403, "token de verificação inválido")

    @app.post("/webhook")
    async def receber(request: Request, tarefas: BackgroundTasks):
        corpo = await request.body()
        if config.whatsapp_app_secret:
            recebida = request.headers.get("X-Hub-Signature-256", "")
            if not hmac.compare_digest(recebida, assinar(config.whatsapp_app_secret, corpo)):
                raise HTTPException(401, "assinatura inválida")
        try:
            payload = json.loads(corpo)
        except ValueError:
            raise HTTPException(400, "JSON inválido")
        for msg in extrair_mensagens(payload):
            if not msg["id"] or not banco.marcar_processada(msg["id"]):
                continue  # a Meta reenvia o mesmo webhook se não receber 200 rápido
            if processar_em_segundo_plano:
                tarefas.add_task(tratar, msg)  # responde 200 na hora; processa depois
            else:
                tratar(msg)
        return {"ok": True}

    # ----------------------------------------------------------- simulador
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/simulador", response_class=HTMLResponse, include_in_schema=False)
    def pagina():
        return (PASTA / "simulador.html").read_text(encoding="utf-8").replace("{{LOJA}}", config.loja_nome).replace("{{INICIAL}}", config.loja_nome[:1].upper())

    @app.post("/simulador/enviar")
    async def simular(s: Simulacao, request: Request, tarefas: BackgroundTasks):
        mid = f"wamid.SIM{time.time_ns()}"
        if s.resposta_id:
            mensagem = {"from": s.telefone, "id": mid, "timestamp": str(int(time.time())), "type": "interactive",
                        "interactive": {"type": s.tipo_resposta, s.tipo_resposta: {"id": s.resposta_id, "title": s.resposta_titulo or ""}}}
        else:
            mensagem = {"from": s.telefone, "id": mid, "timestamp": str(int(time.time())), "type": "text", "text": {"body": s.texto}}
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "WABA_SIMULADA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "554800000000", "phone_number_id": "SIMULADO"},
            "contacts": [{"profile": {"name": s.nome}, "wa_id": s.telefone}], "messages": [mensagem]}}]}]}
        for msg in extrair_mensagens(payload):
            if banco.marcar_processada(msg["id"]):
                tratar(msg)
        return {"ok": True, "webhook": payload}

    @app.get("/simulador/mensagens")
    def mensagens(telefone: str, desde: int = 0):
        return simulador.desde(telefone, desde)

    @app.post("/simulador/verificar-pedidos")
    def verificar_agora():
        return {"avisos": notificador.verificar()}

    @app.get("/api/pedidos")
    def pedidos():
        return banco.todos_pedidos()

    return app
