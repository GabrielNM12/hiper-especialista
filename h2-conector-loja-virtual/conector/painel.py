"""Painel web do conector: status do ciclo, pedidos, conciliação, pendências, auditoria e ações manuais."""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .banco import Banco
from .sincronizador import Sincronizador

PASTA = Path(__file__).parent


class Agendador:
    """Roda o ciclo a cada N segundos e permite antecipar (webhook do Hiper ou botão do painel)."""

    def __init__(self, sinc: Sincronizador, intervalo: int):
        self.sinc = sinc
        self.intervalo = intervalo
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="agendador")

    def iniciar(self) -> None:
        self.thread.start()

    def antecipar(self) -> None:
        self._acordar.set()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()

    def _loop(self) -> None:
        while not self._parar.is_set():
            try:
                self.sinc.ciclo()
            except Exception as exc:  # noqa: BLE001 - o agendador nunca pode morrer
                self.sinc.banco.auditar("erro", "ciclo", "erro inesperado", detalhe=repr(exc))
            self._acordar.wait(self.intervalo)
            self._acordar.clear()
            time.sleep(0.5)  # agrupa rajadas de webhooks


def criar_painel(sinc: Sincronizador, agendador: Agendador | None = None) -> FastAPI:
    banco: Banco = sinc.banco
    app = FastAPI(title="Conector Hiper ↔ WooCommerce", version="1.0.0")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def pagina():
        return (PASTA / "painel.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def status():
        por_status = {r["status"]: r["n"] for r in banco.todos("SELECT status, COUNT(*) n FROM pedido GROUP BY status")}
        mapa = {r["tipo"]: r["n"] for r in banco.todos("SELECT tipo, COUNT(*) n FROM mapa_produto GROUP BY tipo")}
        return {
            "ultimo_ciclo": banco.ler("ultimo_ciclo"),
            "ponto_produtos": banco.ler("ponto_produtos", 0),
            "ponto_estoque": banco.ler("ponto_estoque", 0),
            "produtos_vinculados": mapa,
            "pedidos_por_status": por_status,
            "pendencias": len(banco.pendencias()),
            "intervalo_segundos": sinc.config.intervalo_segundos,
            "hiper_url": sinc.config.hiper_url,
            "loja_url": sinc.config.woo_url,
        }

    @app.get("/api/pedidos")
    def pedidos():
        saida = []
        for p in banco.pedidos():
            p["conciliacao"] = json.loads(p["conciliacao"]) if p["conciliacao"] else None
            saida.append(p)
        return saida

    @app.get("/api/pendencias")
    def pendencias():
        return [{k: v for k, v in p.items() if k != "dto"} | {"nome": json.loads(p["dto"]).get("nome")}
                for p in banco.pendencias()]

    @app.get("/api/auditoria")
    def auditoria(nivel: str | None = None, limite: int = 200):
        if nivel:
            return banco.todos("SELECT * FROM auditoria WHERE nivel = ? ORDER BY id DESC LIMIT ?", (nivel, limite))
        return banco.todos("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))

    @app.post("/api/sincronizar")
    def sincronizar():
        return sinc.ciclo()

    @app.post("/api/ressincronizar-catalogo")
    def ressincronizar():
        sinc.ressincronizar_catalogo()
        return sinc.ciclo()

    @app.post("/api/pedidos/{woo_id}/reenviar")
    def reenviar(woo_id: int):
        if not banco.pedido(woo_id):
            raise HTTPException(404, "Pedido não encontrado.")
        sinc.marcar_para_reenvio(woo_id)
        return {"ok": True}

    @app.post("/api/pedidos/{woo_id}/vincular")
    def vincular(woo_id: int, hiper_id: str):
        if not banco.pedido(woo_id):
            raise HTTPException(404, "Pedido não encontrado.")
        sinc.confirmar_existente(woo_id, hiper_id)
        return {"ok": True}

    @app.get("/conciliacao.csv", response_class=PlainTextResponse)
    def conciliacao_csv():
        campos = ["numero", "status", "hiper_codigo", "total_loja", "total_hiper", "divergencia", "frete", "desconto_cupons",
                  "desconto_nos_itens", "acrescimos_fora", "cupons", "gateway", "meio_hiper", "parcelas", "linhas_divididas",
                  "chave_nfe"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=campos, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for p in banco.pedidos():
            c = json.loads(p["conciliacao"]) if p["conciliacao"] else {}
            c["cupons"] = ",".join(c.get("cupons", []))
            w.writerow({**c, "numero": p["numero"], "status": p["status"], "hiper_codigo": p["hiper_codigo"],
                        "chave_nfe": p["chave_nfe"]})
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=conciliacao.csv"})

    @app.post("/webhooks/hiper")
    async def webhook_hiper(request: Request):
        try:
            evento = await request.json()
        except ValueError:
            evento = {}
        banco.auditar("info", "webhook", "notificação recebida do Hiper", str(evento.get("tipo")),
                      evento.get("descricao"))
        if agendador:
            agendador.antecipar()
        return {"ok": True}

    return app
