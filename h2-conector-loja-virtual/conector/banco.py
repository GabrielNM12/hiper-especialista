"""Persistência do conector em SQLite: pontos de sincronização, mapa de IDs, pedidos, pendências e auditoria."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

ESQUEMA = """
CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mapa_produto (
    hiper_id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,              -- simples | variavel | variacao
    woo_id INTEGER NOT NULL,
    woo_pai_id INTEGER,
    hiper_pai_id TEXT,
    sku TEXT,
    hash TEXT,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mapa_woo ON mapa_produto (woo_id);
CREATE TABLE IF NOT EXISTS pendencia_produto (
    hiper_id TEXT PRIMARY KEY,
    dto TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0,
    erro TEXT,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pedido (
    woo_id INTEGER PRIMARY KEY,
    numero TEXT,
    status TEXT NOT NULL,            -- enviando | enviado | incerto | erro | faturado | cancelado | cancelar_manual
    hiper_id TEXT,
    hiper_codigo TEXT,
    chave_nfe TEXT,
    url_xml TEXT,
    tentativas INTEGER NOT NULL DEFAULT 0,
    erro TEXT,
    conciliacao TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL,
    nivel TEXT NOT NULL,             -- info | aviso | erro
    entidade TEXT NOT NULL,          -- catalogo | estoque | pedido | ciclo | webhook
    referencia TEXT,
    acao TEXT NOT NULL,
    detalhe TEXT
);
CREATE INDEX IF NOT EXISTS ix_auditoria_data ON auditoria (id DESC);
CREATE TABLE IF NOT EXISTS cache_ibge (
    cep TEXT PRIMARY KEY,
    codigo_ibge INTEGER NOT NULL,
    cidade TEXT,
    uf TEXT
);
"""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, caminho: str = "conector.db"):
        self.caminho = caminho
        self._lock = threading.RLock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL") if caminho != ":memory:" else None
        self.con.executescript(ESQUEMA)

    def executar(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.con.execute(sql, params)

    def todos(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def um(self, sql: str, params: tuple | dict = ()) -> Optional[dict]:
        with self._lock:
            r = self.con.execute(sql, params).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------- estado
    def ler(self, chave: str, padrao: Any = None) -> Any:
        r = self.um("SELECT valor FROM estado WHERE chave = ?", (chave,))
        return json.loads(r["valor"]) if r else padrao

    def gravar(self, chave: str, valor: Any) -> None:
        self.executar("INSERT INTO estado (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                      (chave, json.dumps(valor, ensure_ascii=False)))

    # ---------------------------------------------------------- auditoria
    def auditar(self, nivel: str, entidade: str, acao: str, referencia: str | None = None, detalhe: Any = None) -> None:
        if detalhe is not None and not isinstance(detalhe, str):
            detalhe = json.dumps(detalhe, ensure_ascii=False, default=str)
        self.executar("INSERT INTO auditoria (data, nivel, entidade, referencia, acao, detalhe) VALUES (?, ?, ?, ?, ?, ?)",
                      (agora(), nivel, entidade, referencia, acao, detalhe))

    # ------------------------------------------------------------- mapa
    def mapa(self, hiper_id: str) -> Optional[dict]:
        return self.um("SELECT * FROM mapa_produto WHERE hiper_id = ?", (hiper_id,))

    def salvar_mapa(self, hiper_id: str, tipo: str, woo_id: int, *, woo_pai_id: int | None = None,
                    hiper_pai_id: str | None = None, sku: str | None = None, hash_: str | None = None) -> None:
        self.executar(
            """INSERT INTO mapa_produto (hiper_id, tipo, woo_id, woo_pai_id, hiper_pai_id, sku, hash, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(hiper_id) DO UPDATE SET tipo=excluded.tipo, woo_id=excluded.woo_id, woo_pai_id=excluded.woo_pai_id,
                 hiper_pai_id=excluded.hiper_pai_id, sku=excluded.sku, hash=excluded.hash, atualizado_em=excluded.atualizado_em""",
            (hiper_id, tipo, woo_id, woo_pai_id, hiper_pai_id, sku, hash_, agora()))

    def hiper_id_por_woo(self, woo_id: int) -> Optional[str]:
        r = self.um("SELECT hiper_id FROM mapa_produto WHERE woo_id = ?", (woo_id,))
        return r["hiper_id"] if r else None

    # ------------------------------------------------------- pendências
    def registrar_pendencia(self, hiper_id: str, dto: dict, erro: str) -> None:
        self.executar(
            """INSERT INTO pendencia_produto (hiper_id, dto, tentativas, erro, atualizado_em) VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(hiper_id) DO UPDATE SET dto=excluded.dto, tentativas=pendencia_produto.tentativas + 1,
                 erro=excluded.erro, atualizado_em=excluded.atualizado_em""",
            (hiper_id, json.dumps(dto, ensure_ascii=False), erro, agora()))

    def resolver_pendencia(self, hiper_id: str) -> None:
        self.executar("DELETE FROM pendencia_produto WHERE hiper_id = ?", (hiper_id,))

    def pendencias(self) -> list[dict]:
        return self.todos("SELECT * FROM pendencia_produto ORDER BY atualizado_em")

    # ---------------------------------------------------------- pedidos
    def pedido(self, woo_id: int) -> Optional[dict]:
        return self.um("SELECT * FROM pedido WHERE woo_id = ?", (woo_id,))

    def salvar_pedido(self, woo_id: int, **campos) -> None:
        atual = self.pedido(woo_id)
        if "conciliacao" in campos and not isinstance(campos["conciliacao"], (str, type(None))):
            campos["conciliacao"] = json.dumps(campos["conciliacao"], ensure_ascii=False)
        campos["atualizado_em"] = agora()
        if atual is None:
            campos.setdefault("status", "enviando")
            campos["criado_em"] = campos["atualizado_em"]
            cols = ["woo_id", *campos]
            self.executar(f"INSERT INTO pedido ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          (woo_id, *campos.values()))
        else:
            sets = ", ".join(f"{c} = ?" for c in campos)
            self.executar(f"UPDATE pedido SET {sets} WHERE woo_id = ?", (*campos.values(), woo_id))

    def pedidos(self, status: tuple[str, ...] | None = None) -> list[dict]:
        if status:
            marcas = ",".join("?" * len(status))
            return self.todos(f"SELECT * FROM pedido WHERE status IN ({marcas}) ORDER BY woo_id DESC", status)
        return self.todos("SELECT * FROM pedido ORDER BY woo_id DESC")

    # ------------------------------------------------------------- ibge
    def ibge(self, cep: str) -> Optional[dict]:
        return self.um("SELECT * FROM cache_ibge WHERE cep = ?", (cep,))

    def salvar_ibge(self, cep: str, codigo: int, cidade: str | None, uf: str | None) -> None:
        self.executar("INSERT OR REPLACE INTO cache_ibge (cep, codigo_ibge, cidade, uf) VALUES (?, ?, ?, ?)",
                      (cep, codigo, cidade, uf))
