"""Cliente mínimo da REST API v3 do WooCommerce (autenticação Basic com consumer key/secret)."""

from __future__ import annotations

import time
from typing import Any, Iterator, Optional

import httpx

LOTE_MAXIMO = 100  # limite do endpoint /batch do WooCommerce


class WooErro(Exception):
    def __init__(self, status: int, corpo: Any):
        self.status = status
        self.corpo = corpo
        msg = corpo.get("message") if isinstance(corpo, dict) else str(corpo)[:200]
        super().__init__(f"WooCommerce HTTP {status}: {msg}")


class WooClient:
    def __init__(self, url: str, consumer_key: str, consumer_secret: str, *, http: Optional[httpx.Client] = None,
                 tentativas: int = 4, dormir=time.sleep):
        self.base = url.rstrip("/") + "/wp-json/wc/v3"
        self.auth = (consumer_key, consumer_secret)  # Basic auth
        self.http = http or httpx.Client(timeout=30)
        self.tentativas = tentativas
        self.dormir = dormir

    def _req(self, metodo: str, caminho: str, **kw) -> httpx.Response:
        ultima = None
        for n in range(self.tentativas):
            try:
                r = self.http.request(metodo, self.base + caminho, auth=self.auth, **kw)
            except httpx.TransportError as exc:
                ultima = exc
                self.dormir(min(2 ** n, 10))
                continue
            if r.status_code in (429, 502, 503, 504) and n < self.tentativas - 1:
                self.dormir(min(2 ** n, 10))
                continue
            if r.status_code >= 400:
                try:
                    corpo = r.json()
                except ValueError:
                    corpo = r.text
                raise WooErro(r.status_code, corpo)
            return r
        raise WooErro(0, f"sem resposta: {ultima!r}")

    def _paginar(self, caminho: str, params: dict | None = None) -> Iterator[dict]:
        pagina = 1
        while True:
            r = self._req("GET", caminho, params={**(params or {}), "page": pagina, "per_page": 100})
            itens = r.json()
            yield from itens
            total_paginas = int(r.headers.get("X-WP-TotalPages", "1"))
            if pagina >= total_paginas or not itens:
                return
            pagina += 1

    # ----------------------------------------------------------- categorias
    def garantir_categoria(self, nome: str, cache: dict[str, int]) -> int:
        if nome in cache:
            return cache[nome]
        for c in self._paginar("/products/categories", {"search": nome}):
            if c["name"].lower() == nome.lower():
                cache[nome] = c["id"]
                return c["id"]
        try:
            criada = self._req("POST", "/products/categories", json={"name": nome}).json()
            cache[nome] = criada["id"]
        except WooErro as exc:
            # "term_exists" devolve o id existente em data.resource_id no WooCommerce real
            dados = exc.corpo.get("data", {}) if isinstance(exc.corpo, dict) else {}
            if dados.get("resource_id"):
                cache[nome] = dados["resource_id"]
            else:
                raise
        return cache[nome]

    # ------------------------------------------------------------- produtos
    def criar_produto(self, dados: dict) -> dict:
        return self._req("POST", "/products", json=dados).json()

    def atualizar_produto(self, woo_id: int, dados: dict) -> dict:
        return self._req("PUT", f"/products/{woo_id}", json=dados).json()

    def produto_por_sku(self, sku: str) -> Optional[dict]:
        itens = self._req("GET", "/products", params={"sku": sku, "status": "any"}).json()
        return itens[0] if itens else None

    def criar_variacao(self, pai_id: int, dados: dict) -> dict:
        return self._req("POST", f"/products/{pai_id}/variations", json=dados).json()

    def atualizar_variacao(self, pai_id: int, var_id: int, dados: dict) -> dict:
        return self._req("PUT", f"/products/{pai_id}/variations/{var_id}", json=dados).json()

    def variacoes(self, pai_id: int) -> list[dict]:
        return list(self._paginar(f"/products/{pai_id}/variations"))

    def lote_produtos(self, atualizar: list[dict]) -> list[dict]:
        resultado = []
        for i in range(0, len(atualizar), LOTE_MAXIMO):
            resultado += self._req("POST", "/products/batch", json={"update": atualizar[i:i + LOTE_MAXIMO]}).json().get("update", [])
        return resultado

    def lote_variacoes(self, pai_id: int, atualizar: list[dict]) -> list[dict]:
        resultado = []
        for i in range(0, len(atualizar), LOTE_MAXIMO):
            resultado += self._req("POST", f"/products/{pai_id}/variations/batch",
                                   json={"update": atualizar[i:i + LOTE_MAXIMO]}).json().get("update", [])
        return resultado

    # -------------------------------------------------------------- pedidos
    def pedidos(self, status: tuple[str, ...]) -> Iterator[dict]:
        return self._paginar("/orders", {"status": ",".join(status)})

    def pedido(self, woo_id: int) -> dict:
        return self._req("GET", f"/orders/{woo_id}").json()

    def atualizar_pedido(self, woo_id: int, dados: dict) -> dict:
        return self._req("PUT", f"/orders/{woo_id}", json=dados).json()

    def anotar(self, woo_id: int, nota: str, para_cliente: bool = False) -> None:
        self._req("POST", f"/orders/{woo_id}/notes", json={"note": nota, "customer_note": para_cliente})
