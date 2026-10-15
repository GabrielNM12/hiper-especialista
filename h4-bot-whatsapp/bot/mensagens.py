"""Mensagens de saída, independentes do provedor, e conversão para o formato da WhatsApp Cloud API.

Limites da Cloud API respeitados aqui (mensagens fora do limite são rejeitadas pela Meta):
- texto: até 4096 caracteres; corpo de interativo: até 1024;
- botões de resposta: até 3, título até 20 caracteres;
- lista: até 10 linhas no total, título da linha até 24, descrição até 72, rótulo do botão até 20.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


def cortar(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


@dataclass
class Texto:
    texto: str


@dataclass
class Imagem:
    url: str
    legenda: str = ""


@dataclass
class Botoes:
    corpo: str
    botoes: list[tuple[str, str]]  # (id, título)
    rodape: Optional[str] = None


@dataclass
class LinhaLista:
    id: str
    titulo: str
    descricao: str = ""


@dataclass
class Lista:
    corpo: str
    botao: str
    secoes: list[tuple[str, list[LinhaLista]]] = field(default_factory=list)
    rodape: Optional[str] = None


Mensagem = Union[Texto, Imagem, Botoes, Lista]


def para_cloud_api(destino: str, msg: Mensagem) -> dict:
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": destino}
    if isinstance(msg, Texto):
        return {**base, "type": "text", "text": {"body": cortar(msg.texto, 4096), "preview_url": False}}
    if isinstance(msg, Imagem):
        return {**base, "type": "image", "image": {"link": msg.url, "caption": cortar(msg.legenda, 1024)}}
    if isinstance(msg, Botoes):
        interativo = {
            "type": "button",
            "body": {"text": cortar(msg.corpo, 1024)},
            "action": {"buttons": [{"type": "reply", "reply": {"id": i[:256], "title": cortar(t, 20)}}
                                   for i, t in msg.botoes[:3]]},
        }
        if msg.rodape:
            interativo["footer"] = {"text": cortar(msg.rodape, 60)}
        return {**base, "type": "interactive", "interactive": interativo}
    if isinstance(msg, Lista):
        restantes = 10
        secoes = []
        for titulo, linhas in msg.secoes:
            linhas = linhas[:restantes]
            restantes -= len(linhas)
            if linhas:
                secoes.append({"title": cortar(titulo, 24), "rows": [
                    {"id": l.id[:200], "title": cortar(l.titulo, 24), "description": cortar(l.descricao, 72)} for l in linhas]})
        interativo = {"type": "list", "body": {"text": cortar(msg.corpo, 1024)},
                      "action": {"button": cortar(msg.botao, 20), "sections": secoes}}
        if msg.rodape:
            interativo["footer"] = {"text": cortar(msg.rodape, 60)}
        return {**base, "type": "interactive", "interactive": interativo}
    raise TypeError(f"mensagem não suportada: {msg!r}")
