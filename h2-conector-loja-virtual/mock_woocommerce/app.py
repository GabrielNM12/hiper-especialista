"""Mock de uma loja WooCommerce (subconjunto da REST API v3).

Implementa as rotas que o conector usa, com o mesmo formato de JSON do WooCommerce real:
produtos simples e variáveis, variações, categorias, lotes (batch), pedidos e notas de pedido,
paginação com X-WP-Total / X-WP-TotalPages e autenticação Basic (consumer key/secret).

Inclui uma vitrine mínima em /loja para fazer compras com cupom e gerar pedidos pagos,
fechando o ciclo da demonstração: compra na loja → conector → Hiper → NF-e → pedido concluído.
Campos do plugin "Brazilian Market on WooCommerce" (cpf, number, neighborhood) aparecem em billing.
"""

from __future__ import annotations

import base64
import itertools
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

CK = os.environ.get("MOCK_WOO_CONSUMER_KEY", "ck_mock_0000000000000000000000000000000000")
CS = os.environ.get("MOCK_WOO_CONSUMER_SECRET", "cs_mock_0000000000000000000000000000000000")
PASTA = Path(__file__).parent

CUPONS = {
    "BEMVINDO10": {"discount_type": "percent", "amount": "10"},
    "DESC15": {"discount_type": "fixed_cart", "amount": "15"},
}
GATEWAYS = {
    "pix": "Pix",
    "credit_card": "Cartão de crédito",
    "boleto": "Boleto bancário",
}
FRETE_FIXO = 19.90


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _cents(v: float) -> str:
    return f"{v:.2f}"


class Loja:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.resetar()

    def resetar(self) -> None:
        self.seq = itertools.count(101)
        self.produtos: dict[int, dict] = {}
        self.variacoes: dict[int, dict[int, dict]] = {}
        self.categorias: dict[int, dict] = {}
        self.pedidos: dict[int, dict] = {}
        self.notas: dict[int, list[dict]] = {}
        self.numero_pedido = itertools.count(1001)

    def novo_id(self) -> int:
        return next(self.seq)


def criar_app() -> FastAPI:
    loja = Loja()
    app = FastAPI(title="Mock WooCommerce REST API v3", version="3.0")
    app.state.loja = loja
    base = "/wp-json/wc/v3"

    # ------------------------------------------------------------------- auth
    def autenticar(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
        ck = request.query_params.get("consumer_key")
        cs = request.query_params.get("consumer_secret")
        if authorization and authorization.startswith("Basic "):
            try:
                ck, cs = base64.b64decode(authorization[6:]).decode().split(":", 1)
            except Exception:
                ck = cs = None
        if (ck, cs) != (CK, CS):
            raise HTTPException(401, detail={"code": "woocommerce_rest_cannot_view",
                                             "message": "Desculpe, você não pode listar os recursos.", "data": {"status": 401}})

    @app.exception_handler(HTTPException)
    async def _erro(_: Request, exc: HTTPException):
        corpo = exc.detail if isinstance(exc.detail, dict) else {"code": "erro", "message": str(exc.detail), "data": {"status": exc.status_code}}
        return JSONResponse(corpo, status_code=exc.status_code)

    def paginar(itens: list[dict], request: Request) -> JSONResponse:
        por_pagina = min(int(request.query_params.get("per_page", 10)), 100)
        pagina = max(int(request.query_params.get("page", 1)), 1)
        total = len(itens)
        fatia = itens[(pagina - 1) * por_pagina: pagina * por_pagina]
        return JSONResponse(fatia, headers={"X-WP-Total": str(total),
                                            "X-WP-TotalPages": str(max(1, math.ceil(total / por_pagina)))})

    def erro_validacao(msg: str, code: str = "woocommerce_rest_invalid_param"):
        raise HTTPException(400, detail={"code": code, "message": msg, "data": {"status": 400}})

    # ------------------------------------------------------------- categorias
    @app.get(f"{base}/products/categories", dependencies=[Depends(autenticar)])
    def listar_categorias(request: Request, search: Optional[str] = None):
        itens = [c for c in loja.categorias.values() if not search or search.lower() in c["name"].lower()]
        return paginar(itens, request)

    @app.post(f"{base}/products/categories", status_code=201, dependencies=[Depends(autenticar)])
    async def criar_categoria(request: Request):
        dados = await request.json()
        with loja.lock:
            if any(c["name"] == dados["name"] for c in loja.categorias.values()):
                erro_validacao("Um termo com o nome informado já existe.", "term_exists")
            cid = loja.novo_id()
            loja.categorias[cid] = {"id": cid, "name": dados["name"], "slug": dados["name"].lower().replace(" ", "-")}
            return loja.categorias[cid]

    # --------------------------------------------------------------- produtos
    def _sku_em_uso(sku: str, ignorar: Optional[int] = None) -> bool:
        if not sku:
            return False
        for p in loja.produtos.values():
            if p["sku"] == sku and p["id"] != ignorar:
                return True
        for vs in loja.variacoes.values():
            for v in vs.values():
                if v["sku"] == sku and v["id"] != ignorar:
                    return True
        return False

    def _aplicar_produto(p: dict, dados: dict) -> None:
        if "sku" in dados and _sku_em_uso(dados["sku"], p.get("id")):
            erro_validacao("SKU inválido ou duplicado.", "product_invalid_sku")
        for campo in ("name", "type", "status", "description", "short_description", "sku", "regular_price", "sale_price",
                      "manage_stock", "stock_quantity", "weight", "dimensions", "categories", "images", "attributes",
                      "meta_data"):
            if campo in dados:
                p[campo] = dados[campo]
        if "categories" in dados:
            p["categories"] = [loja.categorias.get(c["id"], {"id": c["id"]}) for c in dados["categories"]]
        if "images" in dados:
            p["images"] = [{"id": i + 1, "src": img["src"]} for i, img in enumerate(dados["images"])]
        if "meta_data" in dados:
            p["meta_data"] = [{"id": i + 1, **m} for i, m in enumerate(dados["meta_data"])]
        p["price"] = p.get("sale_price") or p.get("regular_price") or ""
        if p.get("manage_stock"):
            p["stock_status"] = "instock" if (p.get("stock_quantity") or 0) > 0 else "outofstock"
        p["date_modified"] = _agora()

    def _novo_produto(dados: dict) -> dict:
        p = {"id": loja.novo_id(), "name": "", "type": "simple", "status": "publish", "description": "",
             "short_description": "", "sku": "", "price": "", "regular_price": "", "sale_price": "",
             "manage_stock": False, "stock_quantity": None, "stock_status": "instock", "weight": "",
             "dimensions": {"length": "", "width": "", "height": ""}, "categories": [], "images": [],
             "attributes": [], "variations": [], "meta_data": [], "date_created": _agora()}
        _aplicar_produto(p, dados)
        loja.produtos[p["id"]] = p
        loja.variacoes[p["id"]] = {}
        return p

    @app.get(f"{base}/products", dependencies=[Depends(autenticar)])
    def listar_produtos(request: Request, sku: Optional[str] = None, status: Optional[str] = None):
        itens = [p for p in loja.produtos.values()
                 if (not sku or p["sku"] == sku) and (not status or status == "any" or p["status"] == status)]
        return paginar(sorted(itens, key=lambda p: p["id"]), request)

    @app.post(f"{base}/products", status_code=201, dependencies=[Depends(autenticar)])
    async def criar_produto(request: Request):
        with loja.lock:
            return _novo_produto(await request.json())

    def _produto(pid: int) -> dict:
        p = loja.produtos.get(pid)
        if not p:
            raise HTTPException(404, detail={"code": "woocommerce_rest_product_invalid_id", "message": "ID inválido.",
                                             "data": {"status": 404}})
        return p

    @app.get(f"{base}/products/{{pid}}", dependencies=[Depends(autenticar)])
    def obter_produto(pid: int):
        return _produto(pid)

    @app.put(f"{base}/products/{{pid}}", dependencies=[Depends(autenticar)])
    async def atualizar_produto(pid: int, request: Request):
        with loja.lock:
            p = _produto(pid)
            _aplicar_produto(p, await request.json())
            return p

    @app.post(f"{base}/products/batch", dependencies=[Depends(autenticar)])
    async def lote_produtos(request: Request):
        dados = await request.json()
        resultado: dict[str, list] = {"create": [], "update": []}
        with loja.lock:
            for item in dados.get("create", []):
                try:
                    resultado["create"].append(_novo_produto(item))
                except HTTPException as exc:
                    resultado["create"].append({"id": 0, "error": exc.detail})
            for item in dados.get("update", []):
                try:
                    p = _produto(int(item["id"]))
                    _aplicar_produto(p, item)
                    resultado["update"].append(p)
                except HTTPException as exc:
                    resultado["update"].append({"id": item.get("id"), "error": exc.detail})
        return resultado

    # -------------------------------------------------------------- variações
    def _aplicar_variacao(v: dict, dados: dict) -> None:
        if "sku" in dados and _sku_em_uso(dados["sku"], v.get("id")):
            erro_validacao("SKU inválido ou duplicado.", "product_invalid_sku")
        for campo in ("sku", "regular_price", "sale_price", "status", "manage_stock", "stock_quantity", "attributes",
                      "weight", "dimensions", "meta_data", "image", "description"):
            if campo in dados:
                v[campo] = dados[campo]
        if "meta_data" in dados:
            v["meta_data"] = [{"id": i + 1, **m} for i, m in enumerate(dados["meta_data"])]
        v["price"] = v.get("sale_price") or v.get("regular_price") or ""
        if v.get("manage_stock"):
            v["stock_status"] = "instock" if (v.get("stock_quantity") or 0) > 0 else "outofstock"
        v["date_modified"] = _agora()

    def _nova_variacao(pid: int, dados: dict) -> dict:
        v = {"id": loja.novo_id(), "sku": "", "price": "", "regular_price": "", "sale_price": "", "status": "publish",
             "manage_stock": False, "stock_quantity": None, "stock_status": "instock", "attributes": [], "meta_data": []}
        _aplicar_variacao(v, dados)
        loja.variacoes[pid][v["id"]] = v
        loja.produtos[pid]["variations"].append(v["id"])
        return v

    @app.get(f"{base}/products/{{pid}}/variations", dependencies=[Depends(autenticar)])
    def listar_variacoes(pid: int, request: Request):
        _produto(pid)
        return paginar(list(loja.variacoes[pid].values()), request)

    @app.post(f"{base}/products/{{pid}}/variations", status_code=201, dependencies=[Depends(autenticar)])
    async def criar_variacao(pid: int, request: Request):
        with loja.lock:
            _produto(pid)
            return _nova_variacao(pid, await request.json())

    @app.put(f"{base}/products/{{pid}}/variations/{{vid}}", dependencies=[Depends(autenticar)])
    async def atualizar_variacao(pid: int, vid: int, request: Request):
        with loja.lock:
            _produto(pid)
            v = loja.variacoes[pid].get(vid)
            if not v:
                raise HTTPException(404, detail={"code": "woocommerce_rest_product_variation_invalid_id",
                                                 "message": "ID inválido.", "data": {"status": 404}})
            _aplicar_variacao(v, await request.json())
            return v

    @app.post(f"{base}/products/{{pid}}/variations/batch", dependencies=[Depends(autenticar)])
    async def lote_variacoes(pid: int, request: Request):
        dados = await request.json()
        resultado: dict[str, list] = {"create": [], "update": []}
        with loja.lock:
            _produto(pid)
            for item in dados.get("create", []):
                try:
                    resultado["create"].append(_nova_variacao(pid, item))
                except HTTPException as exc:
                    resultado["create"].append({"id": 0, "error": exc.detail})
            for item in dados.get("update", []):
                v = loja.variacoes[pid].get(int(item["id"]))
                if not v:
                    resultado["update"].append({"id": item["id"], "error": {"code": "invalid_id", "message": "ID inválido."}})
                    continue
                _aplicar_variacao(v, item)
                resultado["update"].append(v)
        return resultado

    # ---------------------------------------------------------------- pedidos
    @app.get(f"{base}/orders", dependencies=[Depends(autenticar)])
    def listar_pedidos(request: Request, status: Optional[str] = None):
        filtros = set(status.split(",")) if status else None
        itens = [o for o in loja.pedidos.values() if not filtros or "any" in filtros or o["status"] in filtros]
        return paginar(sorted(itens, key=lambda o: o["id"]), request)

    def _pedido(oid: int) -> dict:
        o = loja.pedidos.get(oid)
        if not o:
            raise HTTPException(404, detail={"code": "woocommerce_rest_shop_order_invalid_id", "message": "ID inválido.",
                                             "data": {"status": 404}})
        return o

    @app.get(f"{base}/orders/{{oid}}", dependencies=[Depends(autenticar)])
    def obter_pedido(oid: int):
        return _pedido(oid)

    @app.put(f"{base}/orders/{{oid}}", dependencies=[Depends(autenticar)])
    async def atualizar_pedido(oid: int, request: Request):
        dados = await request.json()
        with loja.lock:
            o = _pedido(oid)
            if "status" in dados:
                o["status"] = dados["status"]
                if dados["status"] == "completed":
                    o["date_completed"] = _agora()
            if "meta_data" in dados:
                atuais = {m["key"]: m for m in o["meta_data"]}
                for m in dados["meta_data"]:
                    atuais[m["key"]] = {"id": len(atuais) + 1, **m}
                o["meta_data"] = list(atuais.values())
            o["date_modified"] = _agora()
            return o

    @app.post(f"{base}/orders/{{oid}}/notes", status_code=201, dependencies=[Depends(autenticar)])
    async def criar_nota(oid: int, request: Request):
        dados = await request.json()
        with loja.lock:
            _pedido(oid)
            nota = {"id": loja.novo_id(), "note": dados["note"], "customer_note": bool(dados.get("customer_note")),
                    "date_created": _agora()}
            loja.notas.setdefault(oid, []).append(nota)
            return nota

    @app.get(f"{base}/orders/{{oid}}/notes", dependencies=[Depends(autenticar)])
    def listar_notas(oid: int):
        _pedido(oid)
        return loja.notas.get(oid, [])

    # ------------------------------------------------------- vitrine / checkout
    def _resolver_item(item: dict) -> tuple[dict, Optional[dict], float]:
        p = _produto(int(item["product_id"]))
        v = None
        if item.get("variation_id"):
            v = loja.variacoes[p["id"]].get(int(item["variation_id"]))
            if not v:
                erro_validacao("Variação inválida.")
        alvo = v or p
        preco = float(alvo.get("price") or 0)
        return p, v, preco

    def checkout(dados: dict) -> dict:
        """Cria um pedido pago, com o mesmo rateio de cupom do WooCommerce (proporcional ao subtotal da linha)."""
        with loja.lock:
            linhas = []
            for item in dados["line_items"]:
                p, v, preco = _resolver_item(item)
                qtd = int(item["quantity"])
                alvo = v or p
                if alvo.get("manage_stock") and (alvo.get("stock_quantity") or 0) < qtd:
                    erro_validacao(f"Sem estoque suficiente de {p['name']}.", "woocommerce_rest_invalid_stock")
                nome = p["name"] + ("" if not v else " - " + ", ".join(a["option"] for a in v["attributes"]))
                linhas.append({"p": p, "v": v, "qtd": qtd, "subtotal": round(preco * qtd, 2), "nome": nome,
                               "sku": alvo["sku"]})
            subtotal = round(sum(l["subtotal"] for l in linhas), 2)
            desconto = 0.0
            cupons = []
            for codigo in dados.get("coupon_lines", []):
                cupom = CUPONS.get(codigo["code"].upper())
                if not cupom:
                    erro_validacao(f"Cupom {codigo['code']} não existe.", "woocommerce_rest_invalid_coupon")
                valor = subtotal * float(cupom["amount"]) / 100 if cupom["discount_type"] == "percent" else float(cupom["amount"])
                valor = round(min(valor, subtotal - desconto), 2)
                desconto += valor
                cupons.append({"id": loja.novo_id(), "code": codigo["code"].upper(), "discount": _cents(valor)})
            # rateio do desconto nas linhas (o resíduo de centavos vai para a maior linha)
            rateios = [round(desconto * l["subtotal"] / subtotal, 2) if subtotal else 0 for l in linhas]
            if linhas:
                maior = max(range(len(linhas)), key=lambda i: linhas[i]["subtotal"])
                rateios[maior] = round(rateios[maior] + desconto - sum(rateios), 2)
            frete = 0.0 if dados.get("frete_gratis") else FRETE_FIXO
            line_items = []
            for linha, rateio in zip(linhas, rateios):
                total = round(linha["subtotal"] - rateio, 2)
                line_items.append({
                    "id": loja.novo_id(), "name": linha["nome"], "product_id": linha["p"]["id"],
                    "variation_id": linha["v"]["id"] if linha["v"] else 0, "quantity": linha["qtd"],
                    "subtotal": _cents(linha["subtotal"]), "total": _cents(total), "sku": linha["sku"],
                    "price": round(linha["subtotal"] / linha["qtd"], 2),
                })
                alvo = linha["v"] or linha["p"]
                if alvo.get("manage_stock"):
                    alvo["stock_quantity"] = (alvo.get("stock_quantity") or 0) - linha["qtd"]
                    alvo["stock_status"] = "instock" if alvo["stock_quantity"] > 0 else "outofstock"
            total_pedido = round(subtotal - desconto + frete, 2)
            gateway = dados.get("payment_method", "pix")
            b = dados["billing"]
            oid = loja.novo_id()
            pedido = {
                "id": oid, "number": str(next(loja.numero_pedido)), "status": "processing", "currency": "BRL",
                "date_created": _agora(), "date_modified": _agora(), "date_paid": _agora(), "date_completed": None,
                "discount_total": _cents(desconto), "shipping_total": _cents(frete), "total": _cents(total_pedido),
                "payment_method": gateway, "payment_method_title": GATEWAYS.get(gateway, gateway),
                "billing": {**b, "country": "BR"}, "shipping": {**b, "country": "BR"},
                "line_items": line_items, "coupon_lines": cupons, "fee_lines": [],
                "shipping_lines": [{"id": loja.novo_id(), "method_title": "Entrega padrão", "total": _cents(frete)}],
                "meta_data": [{"id": 1, "key": "_billing_cpf", "value": b.get("cpf", "")},
                              {"id": 2, "key": "_installments", "value": str(dados.get("installments", 1))}],
            }
            loja.pedidos[oid] = pedido
            return pedido

    app.state.checkout = checkout

    @app.post(f"{base}/orders", status_code=201, dependencies=[Depends(autenticar)])
    async def criar_pedido(request: Request):
        return checkout(await request.json())

    @app.get("/loja/api/produtos")
    def vitrine_produtos():
        saida = []
        for p in loja.produtos.values():
            if p["status"] != "publish":
                continue
            vs = [v for v in loja.variacoes[p["id"]].values() if v["status"] == "publish"]
            saida.append({"id": p["id"], "name": p["name"], "type": p["type"], "price": p["price"],
                          "image": p["images"][0]["src"] if p["images"] else None,
                          "stock": p.get("stock_quantity"),
                          "variations": [{"id": v["id"], "label": " / ".join(a["option"] for a in v["attributes"]),
                                          "price": v["price"], "stock": v.get("stock_quantity")} for v in vs]})
        return saida

    @app.post("/loja/api/checkout")
    async def vitrine_checkout(request: Request):
        return checkout(await request.json())

    @app.get("/loja/api/pedidos")
    def vitrine_pedidos():
        return [{"id": o["id"], "number": o["number"], "status": o["status"], "total": o["total"],
                 "discount_total": o["discount_total"],
                 "meta": {m["key"]: m["value"] for m in o["meta_data"]},
                 "notes": [n["note"] for n in loja.notas.get(o["id"], [])]}
                for o in sorted(loja.pedidos.values(), key=lambda o: -o["id"])]

    @app.post("/loja/api/pedidos/{oid}/status")
    def vitrine_status(oid: int, status: str):
        with loja.lock:
            _pedido(oid)["status"] = status
            return {"ok": True}

    @app.post("/loja/api/resetar")
    def vitrine_resetar():
        loja.resetar()
        return {"ok": True}

    @app.get("/loja", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def vitrine():
        return (PASTA / "loja.html").read_text(encoding="utf-8")

    return app


app = criar_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")))
