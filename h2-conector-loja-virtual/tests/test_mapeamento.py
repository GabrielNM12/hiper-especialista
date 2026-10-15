import pytest

from conector import mapeamento as mp
from conector.config import Config


def pedido_base(**extra):
    p = {
        "id": 10, "number": "1001", "total": "0", "discount_total": "0", "shipping_total": "0", "payment_method": "pix",
        "payment_method_title": "Pix",
        "billing": {"first_name": "Ana", "last_name": "Souza", "email": "a@b.com", "cpf": "529.982.247-25",
                    "postcode": "88802-000", "address_1": "Rua A", "number": "1", "neighborhood": "Centro"},
        "shipping": {}, "line_items": [], "coupon_lines": [], "fee_lines": [], "meta_data": [],
    }
    p.update(extra)
    return p


def mapear(pedido, config=None):
    return mp.pedido_woo_para_hiper(pedido, lambda pid, vid: f"hiper-{vid or pid}", lambda cep: 4204608,
                                    config or Config(marketplace_cnpj="12605982000124"))


@pytest.mark.parametrize("qtd,total,esperado", [
    (1, 59.9, [(1, 59.9)]),
    (2, 100.0, [(2, 50.0)]),
    (3, 100.0, [(2, 33.33), (1, 33.34)]),
    (3, 161.73, [(3, 53.91)]),
    (7, 10.0, [(6, 1.43), (1, 1.42)]),
])
def test_dividir_centavos_fecha_total(qtd, total, esperado):
    partes = mp.dividir_centavos(qtd, total)
    assert partes == esperado
    assert round(sum(q * v for q, v in partes), 2) == round(total, 2)


def test_cupom_rateado_vira_preco_liquido_e_total_confere():
    pedido = pedido_base(
        total="190.00", discount_total="10.00", shipping_total="20.00",
        line_items=[{"name": "Camiseta", "product_id": 1, "variation_id": 2, "quantity": 3, "subtotal": "180.00", "total": "170.00"}],
        coupon_lines=[{"code": "DESC10"}],
    )
    m = mapear(pedido)
    itens = m.pedido.itens
    assert [(i.quantidade, i.precoUnitarioLiquido) for i in itens] == [(2, 56.67), (1, 56.66)]
    assert all(i.precoUnitarioBruto == 60.0 for i in itens)
    assert m.conciliacao["total_hiper"] == 190.0 and m.conciliacao["divergencia"] == 0
    assert m.conciliacao["desconto_nos_itens"] == 10.0 and m.conciliacao["linhas_divididas"] == 1
    assert m.pedido.meiosDePagamento[0].valor == 190.0
    assert m.pedido.itens[0].produtoId == "hiper-2"  # sempre o id da variação (filho)


def test_taxa_negativa_vira_desconto_nos_itens():
    pedido = pedido_base(
        total="90.00", line_items=[{"name": "X", "product_id": 1, "variation_id": 0, "quantity": 1, "subtotal": "100.00", "total": "100.00"}],
        fee_lines=[{"name": "Desconto Pix", "total": "-10.00"}],
    )
    m = mapear(pedido)
    assert m.pedido.itens[0].precoUnitarioLiquido == 90.0
    assert m.conciliacao["divergencia"] == 0


def test_taxa_positiva_gera_aviso_e_divergencia():
    pedido = pedido_base(
        total="105.00", line_items=[{"name": "X", "product_id": 1, "variation_id": 0, "quantity": 1, "subtotal": "100.00", "total": "100.00"}],
        fee_lines=[{"name": "Embrulho", "total": "5.00"}],
    )
    m = mapear(pedido)
    assert m.conciliacao["divergencia"] == 5.0
    assert any("Taxas adicionais" in a for a in m.avisos)


def test_cartao_usa_parcelas_e_gateway_desconhecido_usa_padrao():
    linha = [{"name": "X", "product_id": 1, "variation_id": 0, "quantity": 1, "subtotal": "300.00", "total": "300.00"}]
    m = mapear(pedido_base(total="300.00", payment_method="credit_card", line_items=linha,
                           meta_data=[{"key": "_installments", "value": "3"}]))
    assert m.pedido.meiosDePagamento[0].idMeioDePagamento == 4 and m.pedido.meiosDePagamento[0].parcelas == 3
    m = mapear(pedido_base(total="300.00", payment_method="boleto", line_items=linha))
    assert any("sem mapeamento" in a for a in m.avisos)


def test_cnpj_do_plugin_brasileiro():
    b = {"first_name": "Ana", "last_name": "S", "company": "Ana Modas ME", "persontype": "2", "cnpj": "45.723.174/0001-10",
         "postcode": "88802000", "address_1": "Rua A", "number": "1", "neighborhood": "Centro"}
    linha = [{"name": "X", "product_id": 1, "variation_id": 0, "quantity": 1, "subtotal": "10.00", "total": "10.00"}]
    m = mapear(pedido_base(total="10.00", billing=b, line_items=linha))
    assert m.pedido.cliente.documento == "45723174000110" and m.pedido.cliente.nomeDoCliente == "Ana Modas ME"


def test_erros_de_mapeamento_listados_juntos():
    pedido = pedido_base(billing={"first_name": "Sem", "postcode": "00000-000", "address_1": "Rua"},
                         line_items=[{"name": "Órfão", "product_id": 9, "variation_id": 0, "quantity": 1, "subtotal": "1", "total": "1"}])
    with pytest.raises(mp.ErroDeMapeamento) as exc:
        mp.pedido_woo_para_hiper(pedido, lambda pid, vid: None, lambda cep: None, Config())
    texto = " ".join(exc.value.erros)
    assert "CPF/CNPJ" in texto and "IBGE" in texto and "não está vinculado" in texto


def test_estoque_publicavel_nunca_negativo():
    assert mp.estoque_publicavel(-2, 0) == 0
    assert mp.estoque_publicavel(10, 3, seguranca=2) == 5
