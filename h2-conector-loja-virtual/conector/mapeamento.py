"""Tradução entre o modelo do Hiper e o do WooCommerce. Funções puras, fáceis de testar.

Decisões importantes (e o porquê):

1. Desconto: a API do Hiper não tem campo de desconto no pedido. O FAQ oficial orienta enviar o valor já
   com desconto em `precoUnitarioLiquido`. O WooCommerce já rateia cupons por linha (`subtotal` x `total`),
   então usamos `total / quantidade` como preço líquido e `subtotal / quantidade` como bruto.
   Taxas negativas (fee_lines < 0) também viram desconto rateado nas linhas.

2. Centavos: o Hiper multiplica `precoUnitarioLiquido × quantidade`. Se R$ 100,00 for dividido por 3,
   nenhum preço unitário com 2 casas fecha a conta. Nesse caso a linha é dividida em duas
   (2 × 33,33 e 1 × 33,34), para que o total no Hiper seja idêntico ao cobrado na loja.

3. Grade: no WooCommerce o produto pai vira "variable" e cada filho do Hiper vira uma variação.
   O pedido sempre vai com o id do FILHO (o Hiper rejeita o pai).

4. Estoque: publicado = quantidadeEmEstoque − quantidadeEmEstoqueReservado − estoque de segurança, nunca negativo
   (o Hiper permite estoque negativo; a loja não pode vender o que não existe).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from hiper_sdk import (
    Cliente, Endereco, ItemDoPedido, Marketplace, MeioDePagamento, PedidoDeVenda, Produto, Variacao,
)
from hiper_sdk.constantes import CARTAO_CREDITO, MEIOS_DE_PAGAMENTO

from .config import Config


class ErroDeMapeamento(Exception):
    def __init__(self, erros: list[str]):
        self.erros = erros
        super().__init__("; ".join(erros))


def hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def estoque_publicavel(estoque: float, reservado: float, seguranca: int = 0) -> int:
    return max(0, int(estoque - reservado - seguranca))


def _dimensoes(p: Produto) -> dict:
    return {"length": f"{p.comprimento:g}", "width": f"{p.largura:g}", "height": f"{p.altura:g}"}


def _status(p: Produto) -> str:
    return "publish" if p.ativo and not p.removido else "draft"


def _meta_hiper(p: Produto) -> list[dict]:
    meta = [{"key": "_hiper_id", "value": p.id}, {"key": "_hiper_codigo", "value": str(p.codigo)},
            {"key": "_hiper_ncm", "value": p.ncm or ""}]
    if p.atacadoAtivo and p.precoAtacado and p.precoAtacado.precos:
        faixas = [{"a_partir_de": f.quantidade, "preco": f.precoUnitario} for f in p.precoAtacado.precos]
        meta.append({"key": "_hiper_preco_atacado", "value": json.dumps(faixas)})
    return meta


def _imagens(p: Produto) -> list[dict]:
    imgs = [p.imagem] if p.imagem else []
    imgs += [i.imagem for i in (p.imagensAdicionais or []) if i.imagem]
    return [{"src": u} for u in imgs]


def produto_simples(p: Produto, categoria_id: Optional[int], seguranca: int = 0) -> dict:
    return {
        "name": p.nome,
        "type": "simple",
        "status": _status(p),
        "sku": p.codigoDeBarras or f"HIPER-{p.codigo}",
        "regular_price": f"{p.preco:.2f}",
        "description": p.descricao or "",
        "manage_stock": True,
        "stock_quantity": estoque_publicavel(p.quantidadeEmEstoque, p.quantidadeEmEstoqueReservado, seguranca),
        "weight": f"{p.peso:g}",
        "dimensions": _dimensoes(p),
        "categories": [{"id": categoria_id}] if categoria_id else [],
        "images": _imagens(p),
        "meta_data": _meta_hiper(p),
    }


def atributos_da_grade(pai: Produto) -> list[dict]:
    """Monta os atributos do produto variável a partir de tipoVariacaoA/B dos filhos."""
    ordem: dict[str, list[str]] = {}
    for v in pai.variacao or []:
        for tipo, nome in ((v.tipoVariacaoA, v.nomeVariacaoA), (v.tipoVariacaoB, v.nomeVariacaoB)):
            if tipo and nome:
                ordem.setdefault(tipo, [])
                if nome not in ordem[tipo]:
                    ordem[tipo].append(nome)
    return [{"name": tipo, "options": opcoes, "visible": True, "variation": True, "position": i}
            for i, (tipo, opcoes) in enumerate(ordem.items())]


def produto_variavel(pai: Produto, categoria_id: Optional[int]) -> dict:
    return {
        "name": pai.nome,
        "type": "variable",
        "status": _status(pai),
        "sku": f"HIPER-{pai.codigo}",
        "description": pai.descricao or "",
        "weight": f"{pai.peso:g}",
        "dimensions": _dimensoes(pai),
        "categories": [{"id": categoria_id}] if categoria_id else [],
        "images": _imagens(pai),
        "attributes": atributos_da_grade(pai),
        "meta_data": _meta_hiper(pai),
    }


def variacao(v: Variacao, preco: float, seguranca: int = 0) -> dict:
    atributos = [{"name": t, "option": n} for t, n in ((v.tipoVariacaoA, v.nomeVariacaoA), (v.tipoVariacaoB, v.nomeVariacaoB))
                 if t and n]
    return {
        "sku": v.codigoDeBarras or v.id,
        "regular_price": f"{preco:.2f}",
        "status": "publish" if v.variacaoAtiva else "private",
        "manage_stock": True,
        "stock_quantity": estoque_publicavel(v.quantidadeEmEstoque, v.quantidadeEmEstoqueReservado, seguranca),
        "attributes": atributos,
        "meta_data": [{"key": "_hiper_id", "value": v.id}],
    }


# =============================================================== pedidos
def _so_digitos(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def _meta(pedido: dict) -> dict:
    return {m.get("key"): m.get("value") for m in pedido.get("meta_data", [])}


def documento_do_cliente(pedido: dict) -> tuple[str, bool]:
    """CPF ou CNPJ do plugin Brazilian Market (campos em billing ou em meta_data)."""
    b, meta = pedido.get("billing", {}), _meta(pedido)
    cnpj = _so_digitos(b.get("cnpj") or meta.get("_billing_cnpj"))
    cpf = _so_digitos(b.get("cpf") or meta.get("_billing_cpf"))
    tipo = str(b.get("persontype") or meta.get("_billing_persontype") or "")
    if cnpj and (tipo in ("2", "J") or not cpf):
        return cnpj, True
    return cpf, False


def _endereco(dados: dict, meta: dict, prefixo: str, ibge: Optional[int]) -> Endereco:
    return Endereco(
        logradouro=dados.get("address_1") or "",
        numero=str(dados.get("number") or meta.get(f"_{prefixo}_number") or "S/N"),
        complemento=dados.get("address_2") or "",
        bairro=dados.get("neighborhood") or meta.get(f"_{prefixo}_neighborhood") or "Não informado",
        cep=_so_digitos(dados.get("postcode")),
        codigoIbge=ibge or 0,
    )


@dataclass
class PedidoMapeado:
    pedido: PedidoDeVenda
    conciliacao: dict = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def dividir_centavos(quantidade: int, total_linha: float) -> list[tuple[int, float]]:
    """Divide (quantidade, total) em até duas partes cujo preço unitário × quantidade fecha o total exato."""
    unitario = round(total_linha / quantidade, 2)
    if round(unitario * quantidade, 2) == round(total_linha, 2) or quantidade == 1:
        return [(quantidade, unitario)]
    ultimo = round(total_linha - unitario * (quantidade - 1), 2)
    return [(quantidade - 1, unitario), (1, ultimo)]


def pedido_woo_para_hiper(
    pedido: dict,
    resolver_produto: Callable[[int, int], Optional[str]],
    resolver_ibge: Callable[[str], Optional[int]],
    config: Config,
) -> PedidoMapeado:
    erros: list[str] = []
    avisos: list[str] = []
    meta = _meta(pedido)
    numero = str(pedido.get("number") or pedido["id"])

    # ---------------------------------------------------------- cliente
    documento, eh_pj = documento_do_cliente(pedido)
    b = pedido.get("billing", {})
    if len(documento) not in (11, 14):
        erros.append("Pedido sem CPF/CNPJ válido (instale/configure o plugin Brazilian Market no WooCommerce).")
    nome = (b.get("company") if eh_pj and b.get("company") else f"{b.get('first_name', '')} {b.get('last_name', '')}").strip()
    cliente = Cliente(documento=documento, nomeDoCliente=nome or "Cliente da loja virtual", email=b.get("email") or "",
                      nomeFantasia=b.get("company") or "")

    # --------------------------------------------------------- endereços
    s = pedido.get("shipping") or {}
    if not s.get("address_1"):
        s = b
    ibge_cob = resolver_ibge(b.get("postcode", ""))
    ibge_ent = resolver_ibge(s.get("postcode", "")) if s is not b else ibge_cob
    if not ibge_cob or not ibge_ent:
        erros.append("Não foi possível obter o código IBGE do município pelo CEP.")
    cobranca = _endereco(b, meta, "billing", ibge_cob)
    entrega = _endereco(s, meta, "shipping" if s is not b else "billing", ibge_ent)

    # -------------------------------------------------------------- itens
    linhas = pedido.get("line_items", [])
    totais = [float(l["total"]) for l in linhas]
    taxas = [float(f.get("total") or 0) for f in pedido.get("fee_lines", [])]
    desconto_taxas = -sum(t for t in taxas if t < 0)
    acrescimos = sum(t for t in taxas if t > 0)
    if desconto_taxas and sum(totais):
        base = sum(totais)
        rateio = [round(desconto_taxas * t / base, 2) for t in totais]
        rateio[-1] = round(rateio[-1] + desconto_taxas - sum(rateio), 2)
        totais = [round(t - r, 2) for t, r in zip(totais, rateio)]
    if acrescimos:
        avisos.append(f"Taxas adicionais de R$ {acrescimos:.2f} não têm campo no pedido do Hiper; ficaram fora do total.")

    itens: list[ItemDoPedido] = []
    linhas_divididas = 0
    for linha, total in zip(linhas, totais):
        hiper_id = resolver_produto(int(linha.get("product_id") or 0), int(linha.get("variation_id") or 0))
        if not hiper_id:
            erros.append(f"Item '{linha.get('name')}' (SKU {linha.get('sku') or '-'}) não está vinculado a um produto do Hiper.")
            continue
        qtd = int(linha["quantity"])
        bruto = round(float(linha["subtotal"]) / qtd, 2)
        partes = dividir_centavos(qtd, total)
        linhas_divididas += len(partes) - 1
        for q, liquido in partes:
            itens.append(ItemDoPedido(produtoId=hiper_id, quantidade=q, precoUnitarioBruto=bruto,
                                      precoUnitarioLiquido=liquido))

    # ---------------------------------------------------------- pagamento
    frete = round(float(pedido.get("shipping_total") or 0), 2)
    total_hiper = round(sum(i.precoUnitarioLiquido * i.quantidade for i in itens) + frete, 2)
    gateway = pedido.get("payment_method") or ""
    meio = config.mapa_pagamento.get(gateway)
    if meio is None:
        meio = config.meio_pagamento_padrao
        avisos.append(f"Gateway '{gateway}' sem mapeamento; usado {MEIOS_DE_PAGAMENTO[meio]} (id {meio}).")
    parcelas = int(meta.get("_installments") or 1) if meio == CARTAO_CREDITO else 1

    # --------------------------------------------------------- conciliação
    total_woo = round(float(pedido.get("total") or 0), 2)
    desconto_cupons = round(float(pedido.get("discount_total") or 0), 2)
    desconto_enviado = round(sum((i.precoUnitarioBruto - i.precoUnitarioLiquido) * i.quantidade for i in itens), 2)
    cupons = [c.get("code") for c in pedido.get("coupon_lines", [])]
    conciliacao = {
        "numero": numero,
        "total_loja": total_woo,
        "total_hiper": total_hiper,
        "frete": frete,
        "desconto_cupons": desconto_cupons,
        "desconto_taxas": round(desconto_taxas, 2),
        "desconto_nos_itens": desconto_enviado,
        "acrescimos_fora": round(acrescimos, 2),
        "cupons": cupons,
        "gateway": gateway,
        "meio_hiper": MEIOS_DE_PAGAMENTO.get(meio, str(meio)),
        "parcelas": parcelas,
        "linhas_divididas": linhas_divididas,
        "divergencia": round(total_woo - total_hiper, 2),
    }
    if abs(conciliacao["divergencia"]) > 0.009 and not acrescimos:
        avisos.append(f"Total da loja (R$ {total_woo:.2f}) difere do total enviado ao Hiper (R$ {total_hiper:.2f}).")

    if erros:
        raise ErroDeMapeamento(erros)

    obs = [f"Loja virtual #{numero}", f"Pagamento: {pedido.get('payment_method_title') or gateway}"]
    if cupons:
        obs.append(f"Cupons: {', '.join(cupons)} (R$ {desconto_cupons:.2f} rateado nos itens)")
    if acrescimos:
        obs.append(f"Acréscimos não enviados: R$ {acrescimos:.2f}")

    hiper = PedidoDeVenda(
        cliente=cliente,
        enderecoDeCobranca=cobranca,
        enderecoDeEntrega=entrega,
        itens=itens,
        meiosDePagamento=[MeioDePagamento(idMeioDePagamento=meio, parcelas=parcelas, valor=total_hiper)],
        numeroPedidoDeVenda=numero,
        observacaoDoPedidoDeVenda=" | ".join(obs)[:500],
        valorDoFrete=frete,
        marketplace=Marketplace(cnpj=config.marketplace_cnpj, nome=config.marketplace_nome)
        if config.marketplace_cnpj else None,
    )
    return PedidoMapeado(pedido=hiper, conciliacao=conciliacao, avisos=avisos)
