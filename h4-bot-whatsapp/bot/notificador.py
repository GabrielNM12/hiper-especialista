"""Acompanha os pedidos no Hiper e avisa o cliente: pedido registrado (código LV…) e nota fiscal emitida.

Observação: a Cloud API só permite mensagem livre até 24 h depois da última mensagem do cliente.
Fora dessa janela é preciso um template aprovado pela Meta (ex.: "pedido_faturado").
"""

from __future__ import annotations

import logging
import threading

from hiper_sdk import HiperClient, HiperErro

from .banco import Banco
from .mensagens import Texto

log = logging.getLogger("bot.notificador")


class Notificador:
    def __init__(self, banco: Banco, hiper: HiperClient, enviador, intervalo: int = 20):
        self.banco = banco
        self.hiper = hiper
        self.enviador = enviador
        self.intervalo = intervalo
        self._parar = threading.Event()

    def verificar(self) -> int:
        avisos = 0
        for p in self.banco.pedidos_em_aberto():
            try:
                ev = self.hiper.eventos_do_pedido(p["hiper_id"])
            except HiperErro as exc:
                log.warning("eventos do pedido %s: %s", p["numero"], exc)
                continue
            if ev.codigoDoPedidoDeVenda and not p["codigo"]:
                self.banco.atualizar_pedido(p["id"], codigo=ev.codigoDoPedidoDeVenda)
                self.enviador.enviar(p["telefone"], [Texto(
                    f"✅ Seu pedido *{p['numero']}* foi registrado na loja com o código *{ev.codigoDoPedidoDeVenda}*.")])
                avisos += 1
            if ev.cancelado:
                self.banco.atualizar_pedido(p["id"], status="cancelado")
                self.enviador.enviar(p["telefone"], [Texto(f"Seu pedido *{p['numero']}* foi cancelado. "
                                                           "Se tiver dúvidas, escreva *atendente*.")])
                avisos += 1
                continue
            doc = ev.documento_fiscal
            if doc:
                self.banco.atualizar_pedido(p["id"], status="faturado", chave_nfe=doc.chaveDocumentoFiscal)
                self.enviador.enviar(p["telefone"], [Texto(
                    f"🧾 A nota fiscal do pedido *{p['numero']}* foi emitida ({doc.tipoDocumentoFiscal or 'NF-e'}).\n"
                    f"Chave de acesso: {doc.chaveDocumentoFiscal}\nSeu pedido já está sendo preparado para envio! 🚚")])
                avisos += 1
        return avisos

    def iniciar(self) -> None:
        def loop():
            while not self._parar.wait(self.intervalo):
                try:
                    self.verificar()
                except Exception:  # noqa: BLE001
                    log.exception("falha no notificador")
        threading.Thread(target=loop, daemon=True, name="notificador").start()

    def parar(self) -> None:
        self._parar.set()
