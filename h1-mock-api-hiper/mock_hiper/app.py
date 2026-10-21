"""Aplicação FastAPI do mock da API personalizada do Hiper.

Rotas em /api/v1/* reproduzem caminhos, parâmetros e DTOs do Swagger oficial
(https://ms-ecommerce.hiper.com.br/swagger/index.html). Rotas em /simulador/* e a página
/gestao fazem o papel do lojista no Hiper Gestão (alterar preço/estoque, faturar, cancelar).
"""

from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from pathlib import Path
from typing import Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Gera o botão "Authorize" no Swagger: cole só o token (sem a palavra Bearer).
BEARER = HTTPBearer(auto_error=False, description="Cole o token gerado em /api/v1/auth/gerar-token/{chaveDeSeguranca}")
from pydantic import BaseModel

from .estado import (
    CODIGOS_EVENTO,
    CODIGOS_SITUACAO,
    TIPOS_WEBHOOK,
    Config,
    ErroDeValidacao,
    Estado,
    fmt,
    agora,
    serializar_pedido_admin,
)
from .nfe import gerar_nfe
from .webhooks import Despachante

SEGREDO_JWT = os.environ.get("MOCK_HIPER_SEGREDO_JWT", "segredo-do-mock-hiper-nao-use-em-producao")
VALIDADE_TOKEN = 6 * 60 * 60  # igual ao exemplo oficial (exp - nbf = 21.600 s)
CLAIM_SID = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/sid"
CLAIM_HASH = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/hash"
PASTA = Path(__file__).parent

DESCRICAO = """
Mock da **API personalizada (e-commerce) do Hiper Gestão**, fiel ao contrato publicado no Swagger oficial.

1. Gere o token em `GET /api/v1/auth/gerar-token/{chaveDeSeguranca}` com a chave de exemplo do README.
2. Use `Authorization: Bearer <token>` nas demais rotas. No Swagger: botão **Authorize** (canto superior direito),
   cole **só o token** (sem a palavra `Bearer`) e **clique em Authorize dentro da janela** antes de fechar — fechar
   sem clicar deixa as chamadas sem o cabeçalho e elas respondem 401.
3. Use `/gestao` (ou as rotas `/simulador`) para agir como o lojista: mudar preço, estoque, faturar pedido.

Códigos de evento, de situação e de webhook não são publicados pela Hiper; aqui são **simulados**.
"""


class WebhookEntrada(BaseModel):
    ativo: bool = True
    contaHiperId: Optional[str] = None
    id: Optional[str] = None
    tipo: Optional[list[int]] = None
    url: str
    limiteDeTentativas: int = 3
    intervaloEmSegundosEntreTentativas: int = 5


class AlterarProduto(BaseModel):
    preco: Optional[float] = None
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativo: Optional[bool] = None
    estoqueMinimo: Optional[float] = None
    variacaoAtiva: Optional[bool] = None


class AjusteEstoque(BaseModel):
    quantidade: float
    modo: str = "definir"  # ou "somar"


class Caos(BaseModel):
    latencia_ms: int = 0
    taxa_429: float = 0.0
    taxa_500: float = 0.0


def criar_app(config: Optional[Config] = None, *, despachar_webhooks: bool = True) -> FastAPI:
    config = config or Config(
        atraso_processamento_s=float(os.environ.get("MOCK_HIPER_ATRASO_PROCESSAMENTO", "1")),
        base_url_publica=os.environ.get("MOCK_HIPER_URL_PUBLICA", "http://localhost:8000"),
        validacao_estrita=os.environ.get("MOCK_HIPER_VALIDACAO_ESTRITA", "1") == "1",
    )
    estado = Estado(config)
    app = FastAPI(title="Mock API Hiper (e-commerce)", version="1.0.0", description=DESCRICAO,
                  # persistAuthorization: o token continua valendo se a página do Swagger for recarregada.
                  swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True})
    app.state.estado = estado
    if despachar_webhooks:
        app.state.despachante = Despachante(estado)

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def caos_e_log(request: Request, call_next):
        inicio = time.perf_counter()
        caminho = request.url.path
        resposta: Response
        if caminho.startswith("/api/"):
            caos = estado.caos
            if caos.latencia_ms:
                await asyncio.sleep(caos.latencia_ms / 1000)
            sorteio = random.random()
            if sorteio < caos.taxa_429:
                resposta = JSONResponse({"errors": ["Too Many Requests"], "message": "Limite de requisições excedido."},
                                        status_code=429, headers={"Retry-After": "1"})
            elif sorteio < caos.taxa_429 + caos.taxa_500:
                resposta = JSONResponse({"errors": ["Erro interno simulado"], "message": None}, status_code=503)
            else:
                resposta = await call_next(request)
            estado.log_requisicoes.append({
                "data": fmt(agora()), "metodo": request.method, "caminho": caminho,
                "query": str(request.url.query)[:120], "status": resposta.status_code,
                "ms": round((time.perf_counter() - inicio) * 1000, 1),
            })
            del estado.log_requisicoes[:-300]
            return resposta
        return await call_next(request)

    @app.exception_handler(ErroDeValidacao)
    async def _validacao(_: Request, exc: ErroDeValidacao):
        return JSONResponse({"id": None, "errors": exc.erros, "message": "A requisição possui erros de validação."},
                            status_code=400)

    # -------------------------------------------------------------------- auth
    def autenticar(credencial: Optional[HTTPAuthorizationCredentials] = Depends(BEARER)) -> str:
        if credencial is None:
            raise HTTPException(status_code=401)
        try:
            claims = jwt.decode(credencial.credentials, SEGREDO_JWT, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status_code=401)
        if claims.get(CLAIM_HASH) not in estado.chaves:
            raise HTTPException(status_code=403)
        return claims[CLAIM_SID]

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException):
        # A API real responde 401/403 sem corpo (padrão ASP.NET); 404 de recurso traz errors/message.
        if exc.status_code in (401, 403):
            return Response(status_code=exc.status_code)
        return JSONResponse({"errors": [str(exc.detail)], "message": None}, status_code=exc.status_code)

    # ------------------------------------------------------------- Autenticacao
    @app.get("/api/v1/auth/gerar-token/{chaveDeSeguranca}", tags=["Autenticacao"])
    def gerar_token(chaveDeSeguranca: str):
        conta_id = estado.chaves.get(chaveDeSeguranca)
        if not conta_id:
            return JSONResponse({"chaveDeSeguranca": chaveDeSeguranca, "token": None,
                                 "errors": ["Chave de segurança inválida."], "message": "Chave de segurança inválida."},
                                status_code=401)
        agora_s = int(time.time())
        token = jwt.encode({CLAIM_SID: conta_id, CLAIM_HASH: chaveDeSeguranca, "nbf": agora_s,
                            "exp": agora_s + VALIDADE_TOKEN, "iat": agora_s}, SEGREDO_JWT, algorithm="HS256")
        return {"chaveDeSeguranca": chaveDeSeguranca, "token": token, "errors": [], "message": None}

    # ----------------------------------------------------------------- Produtos
    @app.get("/api/v1/produtos/pontoDeSincronizacao", tags=["Produtos"])
    def produtos_ponto(pontoDeSincronizacao: int = 0, _: str = Depends(autenticar)):
        itens = estado.produtos_alterados(pontoDeSincronizacao)
        return {"pontoDeSincronizacao": estado.ponto, "produtos": [estado.dto_produto(p, False) for p in itens],
                "errors": [], "message": None}

    @app.get("/api/v1/produtos/listagemDeProdutos", tags=["Produtos"])
    def produtos_listagem(pontoDeSincronizacao: int = 0, pagina: int = Query(1, ge=1), _: str = Depends(autenticar)):
        itens = estado.produtos_alterados(pontoDeSincronizacao)
        tam = estado.config.tamanho_pagina
        fatia = itens[(pagina - 1) * tam: pagina * tam]
        return {"pontoDeSincronizacao": estado.ponto, "produtos": [estado.dto_produto(p, True) for p in fatia],
                "errors": [], "message": None}

    @app.get("/api/v1/categorias/categorias-de-produto", tags=["Categorias"])
    def categorias(categoriaProdutoId: Optional[str] = None, _: str = Depends(autenticar)):
        from .seed import CATEGORIAS
        cats = [{"id": cid, "nome": nome} for nome, cid in CATEGORIAS.items()
                if not categoriaProdutoId or cid == categoriaProdutoId]
        return {"categorias": cats, "errors": [], "message": None}

    # ------------------------------------------------------------------ Estoque
    @app.get("/api/v1/estoques/pontoDeSincronizacao", tags=["Estoque"])
    def estoque_produto(produtoId: str, pontoDeSincronizacao: int = 0, _: str = Depends(autenticar)):
        p = estado.produtos.get(produtoId)
        if not p:
            return JSONResponse({"errors": ["Produto não encontrado."], "message": None}, status_code=404)
        return estado.dto_estoque(p)

    @app.get("/api/v1/estoques/estoquesDeProdutos", tags=["Estoque"])
    def estoques(pontoDeSincronizacao: int = 0, pagina: int = Query(1, ge=1), conta: str = Depends(autenticar)):
        itens = estado.estoques_alterados(pontoDeSincronizacao)
        tam = estado.config.tamanho_pagina
        fatia = itens[(pagina - 1) * tam: pagina * tam]
        return {"contaHiperId": conta, "localDeEstoqueId": None, "pontoDeSituacao": estado.ponto, "pagina": pagina,
                "estoquesDeProdutos": [estado.dto_estoque(p) for p in fatia], "errors": [], "message": None}

    # ------------------------------------------------------------ PedidoDeVenda
    @app.post("/api/v1/pedido-de-venda", tags=["PedidoDeVenda"])
    @app.post("/api/v1/pedido-de-venda/", include_in_schema=False)
    async def criar_pedido(request: Request, _: str = Depends(autenticar)):
        try:
            corpo = await request.json()
        except ValueError:
            return JSONResponse({"id": None, "errors": ["Body não é um JSON válido."], "message": None}, status_code=400)
        if not isinstance(corpo, dict):
            return JSONResponse({"id": None, "errors": ["Body deve ser um objeto."], "message": None}, status_code=400)
        pedido = estado.criar_pedido(corpo)
        return {"id": pedido.id, "errors": [], "message": "Pedido recebido e em processamento."}

    def _pedido_ou_404(pedido_id: str):
        estado.processar_pendentes()
        try:
            return estado.pedido(pedido_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Pedido de venda não encontrado.")

    @app.get("/api/v1/pedido-de-venda/eventos/{id}", tags=["PedidoDeVenda"])
    def eventos(id: str, _: str = Depends(autenticar)):
        return estado.dto_eventos(_pedido_ou_404(id))

    @app.put("/api/v1/pedido-de-venda/cancelar/{id}", tags=["PedidoDeVenda"])
    def cancelar(id: str, _: str = Depends(autenticar)):
        _pedido_ou_404(id)
        estado.cancelar_via_api(id)
        return {"errors": [], "message": "Cancelamento recebido e em processamento."}

    @app.post("/api/v1/pedido-de-venda/atualizar-status", tags=["PedidoDeVenda"])
    def atualizar_status(pedidodeVendaId: str, codigoSituacao: int, _: str = Depends(autenticar)):
        _pedido_ou_404(pedidodeVendaId)
        estado.atualizar_status(pedidodeVendaId, codigoSituacao)
        return {"errors": [], "message": "Status atualizado."}

    # ------------------------------------------------------------- Configuracao
    @app.get("/api/v1/configuracao/webhook-tipos", tags=["Configuracao"])
    def webhook_tipos(_: str = Depends(autenticar)):
        return {"tipos": [{"codigo": k, "descricao": v} for k, v in TIPOS_WEBHOOK.items()], "errors": [], "message": None}


    def _dto_webhook(cfg: dict) -> dict:
        return {**cfg, "errors": [], "errosConsolidados": [], "failures": [], "successMessage": "Configuração salva."}

    @app.post("/api/v1/configuracao/webhook-config", tags=["Configuracao"])
    def salvar_webhook(entrada: WebhookEntrada, conta: str = Depends(autenticar)):
        cfg = entrada.model_dump()
        cfg["id"] = cfg.get("id") or str(uuid.uuid4())
        cfg["contaHiperId"] = conta
        cfg["tipo"] = cfg.get("tipo") or list(TIPOS_WEBHOOK)
        estado.webhooks[cfg["id"]] = cfg
        return _dto_webhook(cfg)

    @app.get("/api/v1/configuracao/webhook-config/{contaHiperId}", tags=["Configuracao"])
    def obter_webhook(contaHiperId: str, conta: str = Depends(autenticar)):
        for cfg in estado.webhooks.values():
            if cfg["contaHiperId"] == contaHiperId == conta:
                return _dto_webhook(cfg)
        return JSONResponse({"errors": ["Nenhum webhook configurado."], "message": None}, status_code=404)

    # ---------------------------------------------------------------- arquivos
    @app.get("/arquivos/xml/{chave}.xml", tags=["Arquivos"])
    def xml(chave: str):
        conteudo = estado.xmls.get(chave)
        if not conteudo:
            raise HTTPException(status_code=404, detail="XML não encontrado.")
        return Response(conteudo, media_type="application/xml")

    # --------------------------------------------------------------- simulador
    @app.get("/simulador/produtos", tags=["Simulador (Hiper Gestão)"])
    def sim_produtos():
        return [
            {**{k: v for k, v in p.items() if k not in ("imagensAdicionais",)},
             "disponivel": p["estoque"] - p["reservado"]}
            for p in sorted(estado.produtos.values(), key=lambda p: (p["codigo"], p["paiId"] is not None))
        ]


    @app.patch("/simulador/produtos/{produto_id}", tags=["Simulador (Hiper Gestão)"])
    def sim_alterar(produto_id: str, mudancas: AlterarProduto):
        try:
            return estado.alterar_produto(produto_id, mudancas.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, "Produto não encontrado.")


    @app.post("/simulador/produtos/{produto_id}/estoque", tags=["Simulador (Hiper Gestão)"])
    def sim_estoque(produto_id: str, ajuste: AjusteEstoque):
        try:
            return estado.ajustar_estoque(produto_id, ajuste.quantidade, ajuste.modo)
        except KeyError:
            raise HTTPException(404, "Produto não encontrado.")

    @app.post("/simulador/produtos/{produto_id}/loja-virtual", tags=["Simulador (Hiper Gestão)"])
    def sim_loja_virtual(produto_id: str, enviar: bool = True):
        try:
            return estado.definir_loja_virtual(produto_id, enviar)
        except KeyError:
            raise HTTPException(404, "Produto não encontrado.")

    @app.get("/simulador/pedidos", tags=["Simulador (Hiper Gestão)"])
    def sim_pedidos():
        estado.processar_pendentes()
        return [serializar_pedido_admin(p) for p in sorted(estado.pedidos.values(), key=lambda p: -p.numero)]

    @app.post("/simulador/pedidos/{pedido_id}/faturar", tags=["Simulador (Hiper Gestão)"])
    def sim_faturar(pedido_id: str):
        try:
            p = estado.faturar(pedido_id, gerar_nfe)
        except KeyError:
            raise HTTPException(404, "Pedido não encontrado.")
        return serializar_pedido_admin(p)

    @app.post("/simulador/pedidos/{pedido_id}/cancelar-no-gestao", tags=["Simulador (Hiper Gestão)"])
    def sim_cancelar_gestao(pedido_id: str):
        """Cancela pelo Gestão. A API continua mostrando o pedido como ativo (comportamento real, ver FAQ)."""
        try:
            return serializar_pedido_admin(estado.cancelar_no_gestao(pedido_id))
        except KeyError:
            raise HTTPException(404, "Pedido não encontrado.")


    @app.put("/simulador/caos", tags=["Simulador (Hiper Gestão)"])
    def sim_caos(caos: Caos):
        estado.caos.latencia_ms, estado.caos.taxa_429, estado.caos.taxa_500 = caos.latencia_ms, caos.taxa_429, caos.taxa_500
        return caos

    @app.post("/simulador/resetar", tags=["Simulador (Hiper Gestão)"])
    def sim_resetar():
        estado.resetar()
        return {"ok": True, "pontoDeSincronizacao": estado.ponto}

    @app.get("/simulador/info", tags=["Simulador (Hiper Gestão)"])
    def sim_info():
        return {
            "conta": estado.conta,
            "pontoDeSincronizacao": estado.ponto,
            "produtos": len(estado.produtos),
            "pedidos": len(estado.pedidos),
            "caos": vars(estado.caos),
            "webhooks": list(estado.webhooks.values()),
            "codigosSimulados": {"situacao": CODIGOS_SITUACAO, "evento": CODIGOS_EVENTO, "webhook": TIPOS_WEBHOOK},
        }

    @app.get("/simulador/log", tags=["Simulador (Hiper Gestão)"])
    def sim_log():
        return {"requisicoes": estado.log_requisicoes[-100:][::-1], "webhooks": estado.entregas_webhook[-50:][::-1]}

    @app.get("/gestao", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def gestao():
        return (PASTA / "gestao.html").read_text(encoding="utf-8")

    return app


app = criar_app()
