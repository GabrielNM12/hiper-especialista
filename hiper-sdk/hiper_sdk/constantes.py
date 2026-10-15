"""Constantes publicadas na documentação técnica da API personalizada do Hiper."""

URL_PRODUCAO = "https://ms-ecommerce.hiper.com.br"

# IDs aceitos em meiosDePagamento[].idMeioDePagamento (tabela da documentação oficial).
MEIOS_DE_PAGAMENTO = {
    1: "Dinheiro",
    2: "Cheque",
    3: "Devolução",
    4: "Cartão de crédito",
    5: "Cartão de débito",
    6: "Crediário",
    11: "Cartão voucher",
    12: "Pix",
}

DINHEIRO = 1
CHEQUE = 2
DEVOLUCAO = 3
CARTAO_CREDITO = 4
CARTAO_DEBITO = 5
CREDIARIO = 6
VOUCHER = 11
PIX = 12

# Tempo de vida do token: o exemplo da documentação tem nbf/exp separados por 21.600 s (6 h).
VALIDADE_TOKEN_SEGUNDOS = 6 * 60 * 60

FORMATO_DATA = "%d/%m/%Y %H:%M:%S"
