"""Motor de sincronização Hiper ↔ WooCommerce.

Ciclo (nesta ordem):
1. pedidos    loja  → Hiper  (pedidos pagos viram pedido de venda e reservam estoque no Hiper)
2. cancelamentos loja → Hiper
3. catálogo   Hiper → loja   (listagem paginada desde o último ponto de sincronização)
4. estoque    Hiper → loja   (estoques alterados desde o último ponto; atualização em lote)
5. eventos    Hiper → loja   (código LV, NF-e emitida → pedido concluído com a chave)

Pedidos vêm antes do estoque de propósito: se o estoque fosse publicado primeiro, a loja receberia
de volta as unidades recém-vendidas (o Hiper ainda não as teria reservado) e poderia vender duas vezes.

Garantias:
- Idempotência: mapa hiper_id ↔ woo_id + hash do payload; nada é reenviado se não mudou.
- Nada se perde: o ponto de sincronização só avança depois do lote; produto que falhar vai para
  `pendencia_produto` com o DTO guardado (a API do Hiper não tem rota para buscar um produto por id).
- Pedido nunca duplica: grava "enviando" antes do POST; timeout/5xx vira "incerto" e exige conferência.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Optional

from hiper_sdk import HiperClient, HiperEnvioIncerto, HiperErro, HiperErroAPI, Produto, Variacao

from . import mapeamento as mp
from .banco import Banco, agora
from .config import Config
from .ibge import ResolvedorIbge
from .woocommerce import WooClient, WooErro

log = logging.getLogger("conector")

STATUS_FINAIS = {"enviado", "faturado", "cancelado", "incerto", "cancelar_manual"}


class Sincronizador:
    def __init__(self, config: Config, banco: Banco, hiper: HiperClient, woo: WooClient, ibge: ResolvedorIbge):
        self.config = config
        self.banco = banco
        self.hiper = hiper
        self.woo = woo
        self.ibge = ibge
        self._categorias: dict[str, int] = {}
        self._lock = threading.Lock()

    # ============================================================ catálogo
    def sincronizar_catalogo(self) -> dict:
        ponto = self.banco.ler("ponto_produtos", 0)
        produtos: dict[str, Produto] = {}
        novo_ponto = ponto
        for lote in self.hiper.iterar_listagem_de_produtos(ponto):
            novo_ponto = max(novo_ponto, lote.pontoDeSincronizacao)
            for p in lote.produtos:
                produtos[p.id] = p
        # pendências de ciclos anteriores entram junto (o dado novo, se houver, prevalece)
        for pend in self.banco.pendencias():
            produtos.setdefault(pend["hiper_id"], Produto.model_validate_json(pend["dto"]))

        pais = {i: p for i, p in produtos.items() if p.eh_pai_de_grade}
        filhos = {i: p for i, p in produtos.items() if p.eh_filho_de_grade}
        simples = {i: p for i, p in produtos.items() if not p.grade}
        resumo = defaultdict(int)

        for p in simples.values():
            self._com_pendencia(p, lambda p=p: self._sync_simples(p, resumo))
        for pai in pais.values():
            self._com_pendencia(pai, lambda pai=pai: self._sync_variavel(pai, filhos, resumo))
        # filhos que mudaram sem o pai vir junto: atualiza só preço/estoque/status da variação
        for filho in filhos.values():
            if filho.produtoPrimarioId not in pais:
                self._com_pendencia(filho, lambda f=filho: self._sync_filho_avulso(f, resumo))

        self.banco.gravar("ponto_produtos", novo_ponto)
        resumo["ponto"] = novo_ponto
        resumo["recebidos"] = len(produtos)
        if produtos:
            self.banco.auditar("info", "catalogo", "sincronizado", detalhe=dict(resumo))
        return dict(resumo)

    def _com_pendencia(self, p: Produto, acao) -> None:
        try:
            acao()
            self.banco.resolver_pendencia(p.id)
        except (WooErro, HiperErro) as exc:
            self.banco.registrar_pendencia(p.id, p.model_dump(mode="json"), str(exc))
            self.banco.auditar("erro", "catalogo", "falha ao publicar", p.id, f"{p.nome}: {exc}")

    def _categoria(self, nome: Optional[str]) -> Optional[int]:
        return self.woo.garantir_categoria(nome, self._categorias) if nome else None

    def _upsert_produto(self, p: Produto, payload: dict, tipo: str, resumo) -> int:
        h = mp.hash_payload(payload)
        mapa = self.banco.mapa(p.id)
        if mapa and mapa["hash"] == h:
            resumo["sem_alteracao"] += 1
            return mapa["woo_id"]
        if mapa:
            self.woo.atualizar_produto(mapa["woo_id"], payload)
            woo_id = mapa["woo_id"]
            resumo["atualizados"] += 1
            self.banco.auditar("info", "catalogo", "produto atualizado", p.id, f"{p.nome} → woo #{woo_id}")
        else:
            existente = self.woo.produto_por_sku(payload["sku"])  # recupera vínculo se o banco local foi perdido
            if existente:
                woo_id = self.woo.atualizar_produto(existente["id"], payload)["id"]
                resumo["revinculados"] += 1
            else:
                woo_id = self.woo.criar_produto(payload)["id"]
                resumo["criados"] += 1
                self.banco.auditar("info", "catalogo", "produto criado", p.id, f"{p.nome} → woo #{woo_id}")
        self.banco.salvar_mapa(p.id, tipo, woo_id, sku=payload["sku"], hash_=h)
        return woo_id

    def _sync_simples(self, p: Produto, resumo) -> None:
        payload = mp.produto_simples(p, self._categoria(p.categoria), self.config.estoque_seguranca)
        self._upsert_produto(p, payload, "simples", resumo)

    def _sync_variavel(self, pai: Produto, filhos: dict[str, Produto], resumo) -> None:
        payload = mp.produto_variavel(pai, self._categoria(pai.categoria))
        woo_pai = self._upsert_produto(pai, payload, "variavel", resumo)
        existentes: Optional[dict[str, dict]] = None
        atualizar: list[dict] = []
        pendentes_hash: list[tuple[Variacao, int, dict, str]] = []
        for v in pai.variacao or []:
            filho = filhos.get(v.id)
            preco = filho.preco if filho else pai.preco
            var_payload = mp.variacao(v, preco, self.config.estoque_seguranca)
            if filho and (not filho.ativo or filho.removido):
                var_payload["status"] = "private"
            h = mp.hash_payload(var_payload)
            mapa = self.banco.mapa(v.id)
            if mapa and mapa["hash"] == h:
                continue
            if mapa:
                atualizar.append({"id": mapa["woo_id"], **var_payload})
                pendentes_hash.append((v, mapa["woo_id"], var_payload, h))
                continue
            if existentes is None:
                existentes = {x["sku"]: x for x in self.woo.variacoes(woo_pai)}
            achada = existentes.get(var_payload["sku"])
            if achada:
                atualizar.append({"id": achada["id"], **var_payload})
                pendentes_hash.append((v, achada["id"], var_payload, h))
            else:
                criada = self.woo.criar_variacao(woo_pai, var_payload)
                self.banco.salvar_mapa(v.id, "variacao", criada["id"], woo_pai_id=woo_pai, hiper_pai_id=pai.id,
                                       sku=var_payload["sku"], hash_=h)
                resumo["variacoes_criadas"] += 1
        if atualizar:
            resultado = {r.get("id"): r for r in self.woo.lote_variacoes(woo_pai, atualizar)}
            for v, woo_id, var_payload, h in pendentes_hash:
                if "error" in resultado.get(woo_id, {}):
                    raise WooErro(400, resultado[woo_id]["error"])
                self.banco.salvar_mapa(v.id, "variacao", woo_id, woo_pai_id=woo_pai, hiper_pai_id=pai.id,
                                       sku=var_payload["sku"], hash_=h)
            resumo["variacoes_atualizadas"] += len(atualizar)

    def _sync_filho_avulso(self, filho: Produto, resumo) -> None:
        mapa = self.banco.mapa(filho.id)
        if not mapa:
            raise WooErro(409, {"message": "Variação chegou sem o produto pai; aguardando o pai na próxima sincronização."})
        dados = {"regular_price": f"{filho.preco:.2f}",
                 "stock_quantity": mp.estoque_publicavel(filho.quantidadeEmEstoque, filho.quantidadeEmEstoqueReservado,
                                                         self.config.estoque_seguranca),
                 "status": "publish" if filho.ativo and not filho.removido else "private"}
        self.woo.atualizar_variacao(mapa["woo_pai_id"], mapa["woo_id"], dados)
        resumo["variacoes_atualizadas"] += 1

    # ============================================================= estoque
    def sincronizar_estoque(self) -> dict:
        ponto = self.banco.ler("ponto_estoque", 0)
        novo_ponto = ponto
        simples: list[dict] = []
        por_pai: dict[int, list[dict]] = defaultdict(list)
        ignorados = 0
        for lote in self.hiper.iterar_estoques(ponto):
            novo_ponto = max(novo_ponto, lote.pontoDeSituacao)
            for e in lote.estoquesDeProdutos:
                mapa = self.banco.mapa(e.produtoId)
                if not mapa:
                    ignorados += 1
                    continue
                qtd = mp.estoque_publicavel(e.quantidadeEmEstoque, e.quantidadeEmEstoqueReservado, self.config.estoque_seguranca)
                if mapa["tipo"] == "simples":
                    simples.append({"id": mapa["woo_id"], "stock_quantity": qtd, "manage_stock": True})
                elif mapa["tipo"] == "variacao":
                    por_pai[mapa["woo_pai_id"]].append({"id": mapa["woo_id"], "stock_quantity": qtd, "manage_stock": True})
        if simples:
            self.woo.lote_produtos(simples)
        for pai, itens in por_pai.items():
            self.woo.lote_variacoes(pai, itens)
        total = len(simples) + sum(len(v) for v in por_pai.values())
        self.banco.gravar("ponto_estoque", novo_ponto)
        if total:
            self.banco.auditar("info", "estoque", "estoque atualizado", detalhe={"itens": total, "ponto": novo_ponto})
        return {"atualizados": total, "ignorados": ignorados, "ponto": novo_ponto}

    # ============================================================= pedidos
    def _resolver_produto(self, product_id: int, variation_id: int) -> Optional[str]:
        if variation_id:
            return self.banco.hiper_id_por_woo(variation_id)
        return self.banco.hiper_id_por_woo(product_id)

    def enviar_pedidos(self) -> dict:
        resumo = defaultdict(int)
        for pedido in self.woo.pedidos(self.config.status_pedido_pago):
            woo_id = int(pedido["id"])
            if self.config.pedidos_a_partir_de and (pedido.get("date_created") or "") < self.config.pedidos_a_partir_de:
                continue
            reg = self.banco.pedido(woo_id)
            if reg and reg["status"] in STATUS_FINAIS:
                continue
            if reg and reg["status"] == "enviando":
                # processo caiu entre gravar "enviando" e receber a resposta: não dá para saber se chegou
                self.banco.salvar_pedido(woo_id, status="incerto", erro="Interrompido durante o envio; confira no Hiper.")
                resumo["incertos"] += 1
                continue
            if reg and reg["status"] == "erro" and reg["tentativas"] >= self.config.max_tentativas_pedido:
                continue
            self._enviar_um(pedido, reg, resumo)
        return dict(resumo)

    def _enviar_um(self, pedido: dict, reg: Optional[dict], resumo) -> None:
        woo_id, numero = int(pedido["id"]), str(pedido.get("number") or pedido["id"])
        tentativas = (reg["tentativas"] if reg else 0) + 1
        try:
            mapeado = mp.pedido_woo_para_hiper(pedido, self._resolver_produto, self.ibge.codigo, self.config)
        except mp.ErroDeMapeamento as exc:
            erro = "; ".join(exc.erros)
            if not reg or reg.get("erro") != erro:
                self.banco.auditar("erro", "pedido", "não foi possível montar o pedido", numero, erro)
            self.banco.salvar_pedido(woo_id, numero=numero, status="erro", erro=erro, tentativas=tentativas)
            resumo["erros"] += 1
            return

        self.banco.salvar_pedido(woo_id, numero=numero, status="enviando", tentativas=tentativas,
                                 conciliacao=mapeado.conciliacao, erro=None)
        try:
            recebido = self.hiper.criar_pedido_de_venda(mapeado.pedido)
        except HiperEnvioIncerto as exc:
            self.banco.salvar_pedido(woo_id, status="incerto", erro=str(exc))
            self.banco.auditar("aviso", "pedido", "envio incerto: conferir no Hiper antes de reenviar", numero, str(exc))
            resumo["incertos"] += 1
            return
        except HiperErroAPI as exc:
            erro = "; ".join(exc.erros) or str(exc)
            self.banco.salvar_pedido(woo_id, status="erro", erro=erro)
            self.banco.auditar("erro", "pedido", f"Hiper recusou (HTTP {exc.status})", numero, erro)
            resumo["erros"] += 1
            return

        self.banco.salvar_pedido(woo_id, status="enviado", hiper_id=recebido.id, erro=None)
        for aviso in mapeado.avisos:
            self.banco.auditar("aviso", "pedido", aviso, numero)
        self.banco.auditar("info", "pedido", "enviado ao Hiper", numero,
                           f"pedido de venda {recebido.id} · total R$ {mapeado.conciliacao['total_hiper']:.2f}")
        try:
            self.woo.atualizar_pedido(woo_id, {"meta_data": [{"key": "_hiper_pedido_id", "value": recebido.id}]})
            self.woo.anotar(woo_id, f"Pedido enviado ao Hiper (id {recebido.id}).")
        except WooErro as exc:
            self.banco.auditar("aviso", "pedido", "não foi possível anotar o pedido na loja", numero, str(exc))
        resumo["enviados"] += 1

    def acompanhar_pedidos(self) -> dict:
        resumo = defaultdict(int)
        for reg in self.banco.pedidos(("enviado",)):
            try:
                ev = self.hiper.eventos_do_pedido(reg["hiper_id"])
            except HiperErro as exc:
                self.banco.auditar("aviso", "pedido", "falha ao consultar eventos", reg["numero"], str(exc))
                continue
            woo_id = reg["woo_id"]
            if ev.codigoDoPedidoDeVenda and not reg["hiper_codigo"]:
                self.banco.salvar_pedido(woo_id, hiper_codigo=ev.codigoDoPedidoDeVenda)
                self.woo.atualizar_pedido(woo_id, {"meta_data": [{"key": "_hiper_codigo_pedido", "value": ev.codigoDoPedidoDeVenda}]})
                resumo["codigos"] += 1
            if ev.cancelado:
                self.banco.salvar_pedido(woo_id, status="cancelado")
                self.banco.auditar("aviso", "pedido", "cancelado no Hiper", reg["numero"])
                continue
            doc = ev.documento_fiscal
            if doc:
                self.banco.salvar_pedido(woo_id, status="faturado", chave_nfe=doc.chaveDocumentoFiscal, url_xml=doc.urlArquivoXml)
                dados = {"meta_data": [{"key": "_nfe_chave", "value": doc.chaveDocumentoFiscal},
                                       {"key": "_nfe_xml", "value": doc.urlArquivoXml or ""}]}
                if self.config.concluir_pedido_ao_faturar:
                    dados["status"] = "completed"
                self.woo.atualizar_pedido(woo_id, dados)
                self.woo.anotar(woo_id, f"Nota fiscal emitida ({doc.tipoDocumentoFiscal or 'NF-e'}). "
                                        f"Chave de acesso: {doc.chaveDocumentoFiscal}", para_cliente=True)
                self.banco.auditar("info", "pedido", "faturado no Hiper", reg["numero"], doc.chaveDocumentoFiscal)
                resumo["faturados"] += 1
        return dict(resumo)

    def cancelar_pedidos(self) -> dict:
        resumo = defaultdict(int)
        for pedido in self.woo.pedidos(self.config.status_pedido_cancelado):
            reg = self.banco.pedido(int(pedido["id"]))
            if not reg or reg["status"] not in ("enviado", "faturado"):
                continue
            if reg["status"] == "faturado":
                # A NF-e já foi emitida: cancelar/devolver é decisão fiscal, não deve ser automática.
                self.banco.salvar_pedido(reg["woo_id"], status="cancelar_manual",
                                         erro="Pedido faturado foi cancelado na loja: tratar NF-e/devolução no Hiper Gestão.")
                self.banco.auditar("aviso", "pedido", "cancelado na loja após faturamento: ação manual", reg["numero"])
                resumo["manuais"] += 1
                continue
            try:
                self.hiper.cancelar_pedido(reg["hiper_id"])
            except HiperErro as exc:
                self.banco.auditar("erro", "pedido", "falha ao cancelar no Hiper", reg["numero"], str(exc))
                continue
            self.banco.salvar_pedido(reg["woo_id"], status="cancelado")
            self.woo.anotar(reg["woo_id"], "Pedido cancelado também no Hiper.")
            self.banco.auditar("info", "pedido", "cancelado no Hiper via API", reg["numero"])
            resumo["cancelados"] += 1
        return dict(resumo)

    # ---------------------------------------------- ações manuais (painel)
    def marcar_para_reenvio(self, woo_id: int) -> None:
        self.banco.salvar_pedido(woo_id, status="reenviar", tentativas=0, erro=None)
        self.banco.auditar("aviso", "pedido", "marcado para reenvio manualmente", str(woo_id))

    def confirmar_existente(self, woo_id: int, hiper_id: str) -> None:
        self.banco.salvar_pedido(woo_id, status="enviado", hiper_id=hiper_id, erro=None)
        self.banco.auditar("info", "pedido", "vinculado manualmente a pedido existente no Hiper", str(woo_id), hiper_id)

    def ressincronizar_catalogo(self) -> None:
        self.banco.gravar("ponto_produtos", 0)
        self.banco.gravar("ponto_estoque", 0)
        self.banco.executar("UPDATE mapa_produto SET hash = NULL")
        self.banco.auditar("aviso", "catalogo", "ressincronização completa solicitada")

    # =============================================================== ciclo
    def ciclo(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"ignorado": "ciclo anterior ainda em execução"}
        inicio = time.perf_counter()
        resultado: dict = {"inicio": agora()}
        try:
            for nome, etapa in (("pedidos", self.enviar_pedidos), ("cancelamentos", self.cancelar_pedidos),
                                ("catalogo", self.sincronizar_catalogo), ("estoque", self.sincronizar_estoque),
                                ("eventos", self.acompanhar_pedidos)):
                try:
                    resultado[nome] = etapa()
                except (HiperErro, WooErro) as exc:
                    resultado[nome] = {"erro": str(exc)}
                    self.banco.auditar("erro", "ciclo", f"etapa {nome} falhou", detalhe=str(exc))
            resultado["duracao_s"] = round(time.perf_counter() - inicio, 2)
            self.banco.gravar("ultimo_ciclo", resultado)
            return resultado
        finally:
            self._lock.release()
