"""Estado em memória do Hiper Gestão simulado.

Regras reproduzidas da documentação oficial e do FAQ da API personalizada:
- cada alteração gera um novo ponto de sincronização; a consulta devolve o que mudou depois do ponto informado;
- só aparecem produtos com "Enviar produto para a loja virtual"; ao desmarcar, o produto volta com `removido=true`;
- produtos de grade: o pai traz `variacao` com os filhos; os filhos trazem `produtoPrimarioId` = id do pai;
- pedido de venda só aceita produtos do Hiper; cliente inexistente é cadastrado automaticamente;
- desconto só existe por item, via `precoUnitarioLiquido`;
- cancelamento feito no Gestão NÃO é refletido na API (só cancelamentos via API aparecem).

Tabelas que a documentação não publica (códigos de evento e de situação) estão em `CODIGOS_*`
e são marcadas como simuladas no README.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import seed
from .util_fiscal import documento_valido, so_digitos, uf_do_ibge

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
FUSO = timezone(timedelta(hours=-3))
FORMATO_DATA = "%d/%m/%Y %H:%M:%S"
MEIOS_DE_PAGAMENTO = {1, 2, 3, 4, 5, 6, 11, 12}

# Simulado: a documentação mostra apenas o código 1 (pedido gerado) e a situação 2 nos exemplos.
CODIGOS_SITUACAO = {1: "Aguardando processamento", 2: "Processado", 3: "Erro no processamento"}
CODIGOS_EVENTO = {1: "Pedido de venda gerado", 2: "Documento fiscal emitido", 3: "Pedido cancelado", 4: "Status atualizado"}

# Simulado: tipos de webhook (o Swagger expõe /webhook-tipos, mas só autenticado).
TIPOS_WEBHOOK = {1: "Produto alterado", 2: "Estoque alterado", 3: "Pedido de venda atualizado"}


def agora() -> datetime:
    return datetime.now(FUSO)


def fmt(dt: datetime) -> str:
    return dt.strftime(FORMATO_DATA)


class ErroDeValidacao(Exception):
    def __init__(self, erros: list[str]):
        self.erros = erros
        super().__init__("; ".join(erros))


@dataclass
class Pedido:
    id: str
    numero: int
    corpo: dict
    criado_em: datetime
    situacao: int = 1
    cancelado: bool = False
    cancelado_no_gestao: bool = False
    faturado: bool = False
    eventos: list[dict] = field(default_factory=list)
    chave_nf: Optional[str] = None
    status_externo: Optional[int] = None

    @property
    def codigo(self) -> str:
        return f"LV{self.numero:011d}"


@dataclass
class ConfigCaos:
    latencia_ms: int = 0
    taxa_429: float = 0.0
    taxa_500: float = 0.0


@dataclass
class Config:
    atraso_processamento_s: float = 1.0
    tamanho_pagina: int = 50
    validacao_estrita: bool = True
    incluir_filhos_na_listagem: bool = True
    base_url_publica: str = "http://localhost:8000"


class Estado:
    def __init__(self, config: Optional[Config] = None, semente: int = 2026):
        self.config = config or Config()
        self.caos = ConfigCaos()
        self._lock = threading.RLock()
        self.semente = semente
        self.ouvintes: list[Callable[[int, dict], None]] = []
        self.resetar()

    # ------------------------------------------------------------------ setup
    def resetar(self) -> None:
        with self._lock:
            self.conta = dict(seed.CONTA_PADRAO)
            self.chaves = {seed.CHAVE_PADRAO: self.conta["contaHiperId"]}
            self.ponto = 0
            self.produtos: dict[str, dict] = {}
            self.ja_enviados: set[str] = set()
            self.pedidos: dict[str, Pedido] = {}
            self.clientes: dict[str, dict] = {}
            self.webhooks: dict[str, dict] = {}
            self.xmls: dict[str, str] = {}
            self.log_requisicoes: list[dict] = []
            self.entregas_webhook: list[dict] = []
            self.seq_pedido = 0
            self.seq_nf = 0
            for p in seed.gerar_produtos(self.semente):
                self.ponto += 1
                p["pontoProduto"] = self.ponto
                p["pontoEstoque"] = self.ponto
                self.produtos[p["id"]] = p
            self.caos = ConfigCaos()

    def _novo_ponto(self) -> int:
        self.ponto += 1
        return self.ponto

    # ------------------------------------------------------------- consultas
    def filhos(self, pai_id: str) -> list[dict]:
        return [p for p in self.produtos.values() if p["paiId"] == pai_id]

    def _visivel(self, p: dict) -> bool:
        raiz = self.produtos[p["paiId"]] if p["paiId"] else p
        return raiz["enviarParaLojaVirtual"] or raiz["id"] in self.ja_enviados

    def _removido(self, p: dict) -> bool:
        raiz = self.produtos[p["paiId"]] if p["paiId"] else p
        return not raiz["enviarParaLojaVirtual"]

    def _dto_variacao(self, f: dict) -> dict:
        return {
            "variacaoAtiva": f["variacaoAtiva"] and f["ativo"],
            "codigo": f["codigo"],
            "codigoDeBarras": f["codigoDeBarras"],
            "id": f["id"],
            "nomeVariacaoA": f["nomeVariacaoA"],
            "nomeVariacaoB": f["nomeVariacaoB"],
            "quantidadeEmEstoque": f["estoque"],
            "quantidadeEmEstoqueReservado": f["reservado"],
            "quantidadeMinimaEmEstoque": f["estoqueMinimo"],
            "tipoVariacaoA": f["tipoVariacaoA"],
            "tipoVariacaoB": f["tipoVariacaoB"],
        }

    def dto_produto(self, p: dict, com_atacado: bool) -> dict:
        eh_pai = p["grade"] and not p["paiId"]
        dto = {
            "quantidadeEmEstoqueReservado": p["reservado"],
            "removido": self._removido(p),
            "altura": p["altura"],
            "ativo": p["ativo"],
            "categoria": p["categoria"],
            "categoriaDoProdutoId": p["categoriaDoProdutoId"],
            "codigo": p["codigo"],
            "codigoDeBarras": p["codigoDeBarras"],
            "comprimento": p["comprimento"],
            "cor": "",
            "descricao": p["descricao"],
            "grade": p["grade"],
            "id": p["id"],
            "imagem": p["imagem"],
            "imagensAdicionais": [{"imagem": i} for i in p["imagensAdicionais"]],
            "largura": p["largura"],
            "marca": p["marca"],
            "ncm": p["ncm"],
            "nome": p["nome"],
            "peso": p["peso"],
            "preco": p["preco"],
            "produtoPrimarioId": p["paiId"] or ZERO_GUID,
            "quantidadeEmEstoque": 0.0 if eh_pai else p["estoque"],
            "quantidadeMinimaEmEstoque": p["estoqueMinimo"],
            "tamanho": "",
            "unidade": p["unidade"],
            "variacao": [self._dto_variacao(f) for f in self.filhos(p["id"])] if eh_pai else [],
            "variacaoAtiva": False if eh_pai else (p["variacaoAtiva"] if p["paiId"] else False),
            "atacadoAtivo": bool(p["atacado"]),
        }
        if com_atacado:
            dto["precoAtacado"] = {"precos": [{"precoUnitario": v, "quantidade": q} for q, v in p["atacado"]]}
        return dto

    def produtos_alterados(self, ponto: int) -> list[dict]:
        with self._lock:
            itens = [
                p for p in self.produtos.values()
                if p["pontoProduto"] > ponto and self._visivel(p)
                and (self.config.incluir_filhos_na_listagem or not p["paiId"])
            ]
            return sorted(itens, key=lambda p: p["pontoProduto"])

    def estoques_alterados(self, ponto: int) -> list[dict]:
        with self._lock:
            itens = [
                p for p in self.produtos.values()
                if p["pontoEstoque"] > ponto and self._visivel(p) and not (p["grade"] and not p["paiId"])
            ]
            return sorted(itens, key=lambda p: p["pontoEstoque"])

    def dto_estoque(self, p: dict) -> dict:
        return {
            "quantidadeEmEstoqueReservado": p["reservado"],
            "pontoDeSincronizacao": p["pontoEstoque"],
            "produtoId": p["id"],
            "quantidadeEmEstoque": p["estoque"],
            "quantidadeMinimaEmEstoque": p["estoqueMinimo"],
            "errors": [],
            "message": None,
        }

    # ------------------------------------------------- alterações (Gestão)
    def _tocar_produto(self, p: dict, estoque: bool = False) -> None:
        ponto = self._novo_ponto()
        p["pontoProduto"] = ponto
        if estoque:
            p["pontoEstoque"] = ponto
        if p["paiId"]:
            # O pai embute o estoque dos filhos em `variacao`, então também muda.
            self.produtos[p["paiId"]]["pontoProduto"] = ponto
        self._emitir(2 if estoque else 1, {"produtoId": p["id"], "pontoDeSincronizacao": ponto})

    def alterar_produto(self, produto_id: str, mudancas: dict) -> dict:
        permitidos = {"preco", "nome", "descricao", "ativo", "marca", "categoria", "estoqueMinimo", "variacaoAtiva", "imagem"}
        with self._lock:
            p = self._produto(produto_id)
            invalidos = set(mudancas) - permitidos
            if invalidos:
                raise ErroDeValidacao([f"Campo não editável: {c}" for c in sorted(invalidos)])
            p.update(mudancas)
            # Preço do pai de grade vale para os filhos no Hiper.
            if "preco" in mudancas and p["grade"] and not p["paiId"]:
                for f in self.filhos(p["id"]):
                    f["preco"] = mudancas["preco"]
                    self._tocar_produto(f)
            self._tocar_produto(p)
            return p

    def ajustar_estoque(self, produto_id: str, quantidade: float, modo: str = "definir") -> dict:
        with self._lock:
            p = self._produto(produto_id)
            if p["grade"] and not p["paiId"]:
                raise ErroDeValidacao(["Estoque de produto com grade é controlado nas variações (filhos)."])
            p["estoque"] = float(quantidade) if modo == "definir" else p["estoque"] + float(quantidade)
            self._tocar_produto(p, estoque=True)
            return p

    def definir_loja_virtual(self, produto_id: str, enviar: bool) -> dict:
        with self._lock:
            p = self._produto(produto_id)
            raiz = self.produtos[p["paiId"]] if p["paiId"] else p
            if raiz["enviarParaLojaVirtual"] and not enviar:
                self.ja_enviados.add(raiz["id"])
            raiz["enviarParaLojaVirtual"] = enviar
            for item in [raiz, *self.filhos(raiz["id"])]:
                self._tocar_produto(item, estoque=True)
            return raiz

    def _produto(self, produto_id: str) -> dict:
        p = self.produtos.get(produto_id)
        if not p:
            raise KeyError(produto_id)
        return p

    # ------------------------------------------------------ pedido de venda
    def validar_pedido(self, corpo: dict) -> list[str]:
        erros: list[str] = []
        cliente = corpo.get("cliente") or {}
        if not cliente.get("documento"):
            erros.append("cliente.documento é obrigatório.")
        elif self.config.validacao_estrita and not documento_valido(cliente["documento"]):
            erros.append("cliente.documento inválido (CPF/CNPJ com dígito verificador incorreto).")
        if not cliente.get("nomeDoCliente"):
            erros.append("cliente.nomeDoCliente é obrigatório.")

        for nome_end in ("enderecoDeCobranca", "enderecoDeEntrega"):
            end = corpo.get(nome_end)
            if not end:
                erros.append(f"{nome_end} é obrigatório.")
                continue
            for campo in ("logradouro", "numero", "bairro", "cep"):
                if not end.get(campo):
                    erros.append(f"{nome_end}.{campo} é obrigatório.")
            if len(so_digitos(str(end.get("cep", "")))) != 8:
                erros.append(f"{nome_end}.cep deve ter 8 dígitos.")
            if not uf_do_ibge(end.get("codigoIbge") or 0) or len(str(end.get("codigoIbge"))) != 7:
                erros.append(f"{nome_end}.codigoIbge inválido.")

        itens = corpo.get("itens") or []
        if not itens:
            erros.append("O pedido precisa ter ao menos um item.")
        total_itens = 0.0
        for i, item in enumerate(itens):
            p = self.produtos.get(str(item.get("produtoId")))
            if not p:
                erros.append(f"itens[{i}].produtoId não encontrado. Somente produtos cadastrados no Hiper são aceitos.")
                continue
            if p["grade"] and not p["paiId"]:
                erros.append(f"itens[{i}].produtoId é o produto pai de uma grade; envie o id da variação (filho).")
            if not p["ativo"]:
                erros.append(f"itens[{i}] produto inativo no Hiper.")
            qtd = float(item.get("quantidade") or 0)
            if qtd <= 0:
                erros.append(f"itens[{i}].quantidade deve ser maior que zero.")
            liquido = item.get("precoUnitarioLiquido", item.get("precoUnitarioBruto"))
            if liquido is None or float(liquido) < 0:
                erros.append(f"itens[{i}].precoUnitarioLiquido inválido.")
            else:
                total_itens += round(float(liquido) * qtd, 2)

        meios = corpo.get("meiosDePagamento") or []
        if not meios:
            erros.append("Informe ao menos um meio de pagamento.")
        for i, m in enumerate(meios):
            if m.get("idMeioDePagamento") not in MEIOS_DE_PAGAMENTO:
                erros.append(f"meiosDePagamento[{i}].idMeioDePagamento inválido. Aceitos: {sorted(MEIOS_DE_PAGAMENTO)}.")
        total = round(total_itens + float(corpo.get("valorDoFrete") or 0), 2)
        pago = round(sum(float(m.get("valor") or 0) for m in meios), 2)
        if self.config.validacao_estrita and meios and abs(total - pago) > 0.01:
            erros.append(f"Soma dos meios de pagamento ({pago:.2f}) difere do total do pedido ({total:.2f}).")

        marketplace = corpo.get("marketplace") or corpo.get("Marketplace") or {}
        mk_cnpj = marketplace.get("cnpj") or marketplace.get("Cnpj")
        mk_nome = marketplace.get("nome") or marketplace.get("Nome")
        if self.conta["uf"] == "SC" and not (mk_cnpj and mk_nome):
            erros.append("Marketplace.Cnpj e Marketplace.Nome são obrigatórios para estabelecimentos de SC.")
        return erros

    def criar_pedido(self, corpo: dict) -> Pedido:
        with self._lock:
            erros = self.validar_pedido(corpo)
            if erros:
                raise ErroDeValidacao(erros)
            self.seq_pedido += 1
            pedido = Pedido(id=str(uuid.uuid4()), numero=self.seq_pedido, corpo=copy.deepcopy(corpo), criado_em=agora())
            self.pedidos[pedido.id] = pedido
            doc = so_digitos(corpo["cliente"]["documento"])
            self.clientes.setdefault(doc, {**corpo["cliente"], "cadastradoPelaApi": True, "endereco": corpo["enderecoDeCobranca"]})
            for item in corpo["itens"]:
                p = self.produtos[item["produtoId"]]
                p["reservado"] += float(item["quantidade"])
                self._tocar_produto(p, estoque=True)
            return pedido

    def processar_pendentes(self) -> None:
        """Simula o processamento assíncrono: após alguns segundos o pedido é gerado no Gestão."""
        with self._lock:
            for pedido in self.pedidos.values():
                if pedido.situacao == 1 and (agora() - pedido.criado_em).total_seconds() >= self.config.atraso_processamento_s:
                    pedido.situacao = 2
                    pedido.eventos.append(self._evento(1, pedido.codigo))
                    self._emitir(3, {"pedidoDeVendaId": pedido.id, "codigoDaSituacaoDeProcessamento": 2})

    def _evento(self, tipo: int, observacao: str, **extra) -> dict:
        return {
            "chaveDocumentoFiscal": extra.get("chave"),
            "codigoDoTipoDeEvento": tipo,
            "data": fmt(agora()),
            "observacao": observacao,
            "tipoDocumentoFiscal": extra.get("tipo_doc"),
            "urlArquivoXml": extra.get("url_xml"),
        }

    def dto_eventos(self, pedido: Pedido) -> dict:
        # Cancelamento feito no Gestão não aparece na API (comportamento documentado no FAQ).
        return {
            "cancelado": pedido.cancelado,
            "codigoDaSituacaoDeProcessamento": pedido.situacao,
            "codigoDoPedidoDeVenda": pedido.codigo if pedido.situacao >= 2 else None,
            "data": fmt(pedido.criado_em),
            "eventos": list(pedido.eventos),
            "pedidoDeVendaId": pedido.id,
            "errors": [],
            "message": None,
        }

    def pedido(self, pedido_id: str) -> Pedido:
        p = self.pedidos.get(pedido_id)
        if not p:
            raise KeyError(pedido_id)
        return p

    def _liberar_reserva(self, pedido: Pedido, baixar_estoque: bool) -> None:
        for item in pedido.corpo["itens"]:
            p = self.produtos[item["produtoId"]]
            qtd = float(item["quantidade"])
            p["reservado"] = max(0.0, p["reservado"] - qtd)
            if baixar_estoque:
                p["estoque"] -= qtd
            self._tocar_produto(p, estoque=True)

    def cancelar_via_api(self, pedido_id: str) -> Pedido:
        with self._lock:
            pedido = self.pedido(pedido_id)
            if pedido.cancelado:
                raise ErroDeValidacao(["Pedido já cancelado."])
            pedido.cancelado = True
            if not pedido.faturado and not pedido.cancelado_no_gestao:
                self._liberar_reserva(pedido, baixar_estoque=False)
            elif pedido.faturado:
                # Devolve o estoque baixado no faturamento.
                for item in pedido.corpo["itens"]:
                    p = self.produtos[item["produtoId"]]
                    p["estoque"] += float(item["quantidade"])
                    self._tocar_produto(p, estoque=True)
            pedido.eventos.append(self._evento(3, "Cancelamento recebido via API"))
            self._emitir(3, {"pedidoDeVendaId": pedido.id, "cancelado": True})
            return pedido

    def cancelar_no_gestao(self, pedido_id: str) -> Pedido:
        with self._lock:
            pedido = self.pedido(pedido_id)
            if not pedido.cancelado_no_gestao and not pedido.cancelado and not pedido.faturado:
                self._liberar_reserva(pedido, baixar_estoque=False)
            pedido.cancelado_no_gestao = True
            return pedido

    def atualizar_status(self, pedido_id: str, codigo: int) -> Pedido:
        with self._lock:
            pedido = self.pedido(pedido_id)
            pedido.status_externo = codigo
            pedido.eventos.append(self._evento(4, f"Status atualizado para {codigo}"))
            return pedido

    def faturar(self, pedido_id: str, gerar_xml: Callable[["Estado", Pedido, int], tuple[str, str, str]]) -> Pedido:
        with self._lock:
            self.processar_pendentes()
            pedido = self.pedido(pedido_id)
            if pedido.situacao != 2:
                raise ErroDeValidacao(["Pedido ainda não processado pelo Hiper; aguarde e tente novamente."])
            if pedido.cancelado or pedido.cancelado_no_gestao:
                raise ErroDeValidacao(["Pedido cancelado não pode ser faturado."])
            if pedido.faturado:
                raise ErroDeValidacao(["Pedido já faturado."])
            self.seq_nf += 1
            chave, tipo_doc, xml = gerar_xml(self, pedido, self.seq_nf)
            self.xmls[chave] = xml
            pedido.faturado = True
            pedido.chave_nf = chave
            self._liberar_reserva(pedido, baixar_estoque=True)
            url = f"{self.config.base_url_publica}/arquivos/xml/{chave}.xml"
            pedido.eventos.append(self._evento(2, f"{tipo_doc} autorizada", chave=chave, tipo_doc=tipo_doc, url_xml=url))
            self._emitir(3, {"pedidoDeVendaId": pedido.id, "chaveDocumentoFiscal": chave})
            return pedido

    # -------------------------------------------------------------- webhooks
    def _emitir(self, tipo: int, dados: dict) -> None:
        evento = {"tipo": tipo, "descricao": TIPOS_WEBHOOK[tipo], "contaHiperId": self.conta["contaHiperId"],
                  "data": fmt(agora()), "dados": dados}
        for ouvinte in list(self.ouvintes):
            try:
                ouvinte(tipo, evento)
            except Exception:  # noqa: BLE001 - ouvintes nunca derrubam a operação
                pass


def agora_epoch() -> float:
    return time.time()


def serializar_pedido_admin(p: Pedido) -> dict[str, Any]:
    total = sum(float(i["quantidade"]) * float(i.get("precoUnitarioLiquido") or 0) for i in p.corpo["itens"])
    return {
        "id": p.id,
        "codigo": p.codigo,
        "numeroPedidoDeVenda": p.corpo.get("numeroPedidoDeVenda"),
        "cliente": p.corpo["cliente"].get("nomeDoCliente"),
        "criadoEm": fmt(p.criado_em),
        "situacao": CODIGOS_SITUACAO.get(p.situacao, p.situacao),
        "cancelado": p.cancelado,
        "canceladoNoGestao": p.cancelado_no_gestao,
        "faturado": p.faturado,
        "chaveNF": p.chave_nf,
        "totalItens": round(total, 2),
        "frete": p.corpo.get("valorDoFrete", 0),
        "observacao": p.corpo.get("observacaoDoPedidoDeVenda") or p.corpo.get("observacao"),
        "itens": p.corpo["itens"],
    }
