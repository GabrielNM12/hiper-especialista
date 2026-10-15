"""Cliente HTTP para a API personalizada (e-commerce) do Hiper.

Comportamentos que a documentação pede ou que a prática exige:
- autenticação: GET /api/v1/auth/gerar-token/{chave} devolve um JWT usado como Bearer;
- 401 → gera um token novo uma única vez e repete a chamada;
- 429 e 5xx → espera exponencial com jitter (recomendação da própria documentação),
  respeitando o cabeçalho Retry-After quando presente;
- POST de pedido não é repetido em falhas ambíguas, para não duplicar pedidos.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Iterator, Optional

import httpx
import jwt

from .constantes import URL_PRODUCAO
from .erros import HiperEnvioIncerto, HiperErroAPI, HiperNaoAutorizado
from .modelos import (
    ConfiguracaoDeWebhook,
    EstoqueDoProduto,
    EventosDoPedido,
    ListaDeEstoques,
    ListaDeProdutos,
    Mensagem,
    PedidoDeVenda,
    RecebimentoDoPedido,
    Token,
)

log = logging.getLogger("hiper_sdk")

MARGEM_RENOVACAO_TOKEN = 300  # renova 5 min antes de expirar


class HiperClient:
    def __init__(
        self,
        chave_de_seguranca: str,
        base_url: str = URL_PRODUCAO,
        *,
        http: Optional[httpx.Client] = None,
        timeout: float = 30.0,
        max_tentativas: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.chave = chave_de_seguranca
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=timeout)
        self._http_proprio = http is None
        self.max_tentativas = max_tentativas
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._dormir = dormir
        self._token: Optional[str] = None
        self._token_expira_em: float = 0

    # ------------------------------------------------------------------ infra
    def close(self) -> None:
        if self._http_proprio:
            self._http.close()

    def __enter__(self) -> "HiperClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _url(self, caminho: str) -> str:
        return f"{self.base_url}{caminho}"

    def _espera(self, tentativa: int, resposta: Optional[httpx.Response]) -> float:
        if resposta is not None:
            retry_after = resposta.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), self.backoff_max)
                except ValueError:
                    pass
        base = min(self.backoff_max, self.backoff_base * (2 ** tentativa))
        return base / 2 + random.uniform(0, base / 2)

    @staticmethod
    def _erro(resposta: httpx.Response) -> HiperErroAPI:
        corpo: Any = None
        mensagem = None
        erros: list[str] = []
        try:
            corpo = resposta.json()
            if isinstance(corpo, dict):
                mensagem = corpo.get("message") or corpo.get("title")
                erros = list(corpo.get("errors") or [])
        except ValueError:
            corpo = resposta.text
        classe = HiperNaoAutorizado if resposta.status_code == 401 else HiperErroAPI
        return classe(resposta.status_code, mensagem, erros, corpo)

    # ------------------------------------------------------------------- token
    def gerar_token(self) -> str:
        resposta: Optional[httpx.Response] = None
        for tentativa in range(self.max_tentativas):
            try:
                resposta = self._http.get(self._url(f"/api/v1/auth/gerar-token/{self.chave}"))
            except httpx.TransportError:
                resposta = None
                self._dormir(self._espera(tentativa, None))
                continue
            if resposta.status_code == 429 or resposta.status_code >= 500:
                self._dormir(self._espera(tentativa, resposta))
                continue
            break
        if resposta is None:
            raise HiperErroAPI(0, "não foi possível gerar o token (falha de conexão)")
        if resposta.status_code != 200:
            raise self._erro(resposta)
        dados = Token.model_validate(resposta.json())
        if not dados.token:
            raise HiperNaoAutorizado(401, dados.message or "token não retornado", dados.errors)
        self._token = dados.token
        try:
            exp = jwt.decode(dados.token, options={"verify_signature": False}).get("exp")
        except jwt.PyJWTError:
            exp = None
        self._token_expira_em = float(exp) if exp else time.time() + 3600
        log.debug("token Hiper gerado, expira em %s", self._token_expira_em)
        return self._token

    def _token_valido(self) -> str:
        if not self._token or time.time() > self._token_expira_em - MARGEM_RENOVACAO_TOKEN:
            return self.gerar_token()
        return self._token

    # ----------------------------------------------------------------- request
    def _requisitar(self, metodo: str, caminho: str, *, idempotente: bool = True, **kwargs) -> httpx.Response:
        renovou_token = False
        ultima: Optional[httpx.Response] = None
        for tentativa in range(self.max_tentativas):
            headers = {"Authorization": f"Bearer {self._token_valido()}"}
            try:
                resposta = self._http.request(metodo, self._url(caminho), headers=headers, **kwargs)
            except httpx.ConnectError:
                # Conexão nem foi aberta: seguro repetir mesmo um POST.
                self._dormir(self._espera(tentativa, None))
                continue
            except httpx.TransportError as exc:
                if not idempotente:
                    raise HiperEnvioIncerto(f"{metodo} {caminho}: {exc!r}") from exc
                self._dormir(self._espera(tentativa, None))
                continue

            ultima = resposta
            if resposta.status_code == 401 and not renovou_token:
                renovou_token = True
                self._token = None
                continue
            if resposta.status_code == 429:
                self._dormir(self._espera(tentativa, resposta))
                continue
            if resposta.status_code >= 500:
                if not idempotente:
                    raise HiperEnvioIncerto(f"{metodo} {caminho}: HTTP {resposta.status_code}")
                self._dormir(self._espera(tentativa, resposta))
                continue
            if resposta.status_code >= 400:
                raise self._erro(resposta)
            return resposta

        if ultima is not None:
            raise self._erro(ultima)
        raise HiperErroAPI(0, f"sem resposta após {self.max_tentativas} tentativas")

    def _get(self, caminho: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        return self._requisitar("GET", caminho, params=params).json()

    # ---------------------------------------------------------------- produtos
    def produtos_desde(self, ponto_de_sincronizacao: int = 0) -> ListaDeProdutos:
        """Produtos alterados após o ponto informado (rota sem paginação)."""
        dados = self._get("/api/v1/produtos/pontoDeSincronizacao", pontoDeSincronizacao=ponto_de_sincronizacao)
        return ListaDeProdutos.model_validate(dados)

    def listagem_de_produtos(self, ponto_de_sincronizacao: int = 0, pagina: int = 1) -> ListaDeProdutos:
        """Rota paginada; é a única que traz preços de atacado."""
        dados = self._get(
            "/api/v1/produtos/listagemDeProdutos", pontoDeSincronizacao=ponto_de_sincronizacao, pagina=pagina
        )
        return ListaDeProdutos.model_validate(dados)

    def iterar_listagem_de_produtos(self, ponto_de_sincronizacao: int = 0) -> Iterator[ListaDeProdutos]:
        pagina = 1
        while True:
            lote = self.listagem_de_produtos(ponto_de_sincronizacao, pagina)
            if not lote.produtos:
                return
            yield lote
            pagina += 1

    def categorias(self, categoria_id: Optional[str] = None) -> Any:
        return self._get("/api/v1/categorias/categorias-de-produto", categoriaProdutoId=categoria_id)

    # ----------------------------------------------------------------- estoque
    def estoque_do_produto(self, produto_id: str) -> EstoqueDoProduto:
        dados = self._get("/api/v1/estoques/pontoDeSincronizacao", produtoId=produto_id)
        return EstoqueDoProduto.model_validate(dados)

    def estoques_de_produtos(self, ponto_de_sincronizacao: int = 0, pagina: int = 1) -> ListaDeEstoques:
        dados = self._get(
            "/api/v1/estoques/estoquesDeProdutos", pontoDeSincronizacao=ponto_de_sincronizacao, pagina=pagina
        )
        return ListaDeEstoques.model_validate(dados)

    def iterar_estoques(self, ponto_de_sincronizacao: int = 0) -> Iterator[ListaDeEstoques]:
        pagina = 1
        while True:
            lote = self.estoques_de_produtos(ponto_de_sincronizacao, pagina)
            if not lote.estoquesDeProdutos:
                return
            yield lote
            pagina += 1

    # --------------------------------------------------------- pedido de venda
    def criar_pedido_de_venda(self, pedido: PedidoDeVenda) -> RecebimentoDoPedido:
        corpo = pedido.model_dump(mode="json", exclude_none=True)
        # O artigo de ajuda chama o campo de "observacaoDoPedidoDeVenda"; o Swagger, de "observacao".
        # Enviamos os dois para funcionar com qualquer versão do backend.
        obs = corpo.get("observacaoDoPedidoDeVenda") or corpo.get("observacao")
        if obs:
            corpo["observacaoDoPedidoDeVenda"] = corpo["observacao"] = obs
        resposta = self._requisitar("POST", "/api/v1/pedido-de-venda", json=corpo, idempotente=False)
        return RecebimentoDoPedido.model_validate(resposta.json())

    def eventos_do_pedido(self, pedido_id: str) -> EventosDoPedido:
        return EventosDoPedido.model_validate(self._get(f"/api/v1/pedido-de-venda/eventos/{pedido_id}"))

    def cancelar_pedido(self, pedido_id: str) -> Mensagem:
        resposta = self._requisitar("PUT", f"/api/v1/pedido-de-venda/cancelar/{pedido_id}")
        return Mensagem.model_validate(resposta.json())

    def atualizar_status_do_pedido(self, pedido_id: str, codigo_situacao: int) -> Mensagem:
        resposta = self._requisitar(
            "POST",
            "/api/v1/pedido-de-venda/atualizar-status",
            params={"pedidodeVendaId": pedido_id, "codigoSituacao": codigo_situacao},
        )
        return Mensagem.model_validate(resposta.json())

    # ----------------------------------------------------------------- webhook
    def configurar_webhook(self, config: ConfiguracaoDeWebhook) -> ConfiguracaoDeWebhook:
        corpo = config.model_dump(mode="json", exclude_none=True, exclude={"errors", "message"})
        resposta = self._requisitar("POST", "/api/v1/configuracao/webhook-config", json=corpo)
        return ConfiguracaoDeWebhook.model_validate(resposta.json())

    def tipos_de_webhook(self) -> Any:
        return self._get("/api/v1/configuracao/webhook-tipos")
