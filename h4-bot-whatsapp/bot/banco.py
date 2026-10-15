"""SQLite do bot: sessões de conversa, mensagens já processadas (idempotência do webhook) e pedidos."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sessao (
    telefone TEXT PRIMARY KEY,
    estado TEXT NOT NULL,
    dados TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processada (
    mensagem_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pedido (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telefone TEXT NOT NULL,
    hiper_id TEXT,
    numero TEXT NOT NULL,
    status TEXT NOT NULL,          -- enviado | incerto | faturado | cancelado
    codigo TEXT,
    chave_nfe TEXT,
    total REAL NOT NULL,
    resumo TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pedido_tel ON pedido (telefone);
"""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, caminho: str = "bot.db"):
        self._lock = threading.RLock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)

    def _um(self, sql: str, p: tuple = ()) -> Optional[dict]:
        with self._lock:
            r = self.con.execute(sql, p).fetchone()
            return dict(r) if r else None

    def _todos(self, sql: str, p: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, p).fetchall()]

    def _exec(self, sql: str, p: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.con.execute(sql, p)

    # ---------------------------------------------------------- sessão
    def sessao(self, telefone: str, expira_minutos: int) -> tuple[str, dict]:
        r = self._um("SELECT * FROM sessao WHERE telefone = ?", (telefone,))
        if not r:
            return "inicio", {}
        if datetime.fromisoformat(r["atualizado_em"]) < datetime.now() - timedelta(minutes=expira_minutos):
            # Sessão expirada: descarta carrinho e dados pessoais (minimização de dados, LGPD).
            return "inicio", {"expirou": True}
        return r["estado"], json.loads(r["dados"])

    def salvar_sessao(self, telefone: str, estado: str, dados: dict) -> None:
        self._exec("INSERT INTO sessao (telefone, estado, dados, atualizado_em) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(telefone) DO UPDATE SET estado=excluded.estado, dados=excluded.dados, atualizado_em=excluded.atualizado_em",
                   (telefone, estado, json.dumps(dados, ensure_ascii=False), agora()))

    # ------------------------------------------------- idempotência webhook
    def marcar_processada(self, mensagem_id: str) -> bool:
        """True se é a primeira vez que vemos a mensagem. A Meta reenvia webhooks que não receberam 200 a tempo."""
        try:
            self._exec("INSERT INTO processada (mensagem_id, data) VALUES (?, ?)", (mensagem_id, agora()))
            return True
        except sqlite3.IntegrityError:
            return False

    # ---------------------------------------------------------- pedidos
    def novo_pedido(self, telefone: str, numero: str, total: float, resumo: str, hiper_id: Optional[str], status: str) -> int:
        cur = self._exec("INSERT INTO pedido (telefone, hiper_id, numero, status, total, resumo, criado_em, atualizado_em) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (telefone, hiper_id, numero, status, total, resumo, agora(), agora()))
        return cur.lastrowid

    def atualizar_pedido(self, pid: int, **campos: Any) -> None:
        campos["atualizado_em"] = agora()
        sets = ", ".join(f"{k} = ?" for k in campos)
        self._exec(f"UPDATE pedido SET {sets} WHERE id = ?", (*campos.values(), pid))

    def pedidos_do_telefone(self, telefone: str, limite: int = 3) -> list[dict]:
        return self._todos("SELECT * FROM pedido WHERE telefone = ? ORDER BY id DESC LIMIT ?", (telefone, limite))

    def pedidos_em_aberto(self) -> list[dict]:
        return self._todos("SELECT * FROM pedido WHERE status = 'enviado' AND hiper_id IS NOT NULL")

    def todos_pedidos(self) -> list[dict]:
        return self._todos("SELECT * FROM pedido ORDER BY id DESC")

    def proximo_numero(self) -> int:
        r = self._um("SELECT COUNT(*) n FROM pedido")
        return (r["n"] if r else 0) + 1
