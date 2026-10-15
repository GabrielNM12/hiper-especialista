"""Modelos do contrato da API personalizada (e-commerce) do Hiper.

Os nomes de campos seguem exatamente os DTOs publicados no Swagger oficial
(https://ms-ecommerce.hiper.com.br/swagger/index.html), por isso ficam em camelCase.
`extra="allow"` garante que campos novos adicionados pelo Hiper não quebrem a integração.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _Resposta(_Base):
    errors: list[str] = Field(default_factory=list)
    message: Optional[str] = None


# --------------------------------------------------------------------------- auth
class Token(_Resposta):
    chaveDeSeguranca: Optional[str] = None
    token: Optional[str] = None


# ----------------------------------------------------------------------- produtos
class ImagemDoProduto(_Base):
    imagem: Optional[str] = None


class Variacao(_Base):
    """Item de `variacao`: um produto filho de uma grade."""

    id: str
    codigo: int = 0
    codigoDeBarras: Optional[str] = None
    variacaoAtiva: bool = True
    tipoVariacaoA: Optional[str] = None
    nomeVariacaoA: Optional[str] = None
    tipoVariacaoB: Optional[str] = None
    nomeVariacaoB: Optional[str] = None
    quantidadeEmEstoque: float = 0
    quantidadeEmEstoqueReservado: float = 0
    quantidadeMinimaEmEstoque: float = 0


class ItemPrecoAtacado(_Base):
    precoUnitario: float
    quantidade: float


class PrecoAtacado(_Base):
    precos: list[ItemPrecoAtacado] = Field(default_factory=list)


class Produto(_Base):
    id: str
    codigo: int = 0
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativo: bool = True
    removido: bool = False
    categoria: Optional[str] = None
    categoriaDoProdutoId: Optional[str] = None
    marca: Optional[str] = None
    ncm: Optional[str] = None
    unidade: Optional[str] = None
    codigoDeBarras: Optional[str] = None
    preco: float = 0
    altura: float = 0
    largura: float = 0
    comprimento: float = 0
    peso: float = 0
    imagem: Optional[str] = None
    imagensAdicionais: Optional[list[ImagemDoProduto]] = None
    grade: bool = False
    produtoPrimarioId: str = ZERO_GUID
    variacao: Optional[list[Variacao]] = None
    variacaoAtiva: bool = False
    quantidadeEmEstoque: float = 0
    quantidadeEmEstoqueReservado: float = 0
    quantidadeMinimaEmEstoque: float = 0
    atacadoAtivo: bool = False
    precoAtacado: Optional[PrecoAtacado] = None
    # Campos legados. A documentação oficial pede para NÃO usar (substituídos por `variacao`).
    cor: Optional[str] = None
    tamanho: Optional[str] = None

    @property
    def eh_filho_de_grade(self) -> bool:
        return bool(self.produtoPrimarioId) and self.produtoPrimarioId != ZERO_GUID

    @property
    def eh_pai_de_grade(self) -> bool:
        return self.grade and not self.eh_filho_de_grade


class ListaDeProdutos(_Resposta):
    pontoDeSincronizacao: int = 0
    produtos: list[Produto] = Field(default_factory=list)


# ------------------------------------------------------------------------ estoque
class EstoqueDoProduto(_Resposta):
    pontoDeSincronizacao: int = 0
    produtoId: str
    quantidadeEmEstoque: float = 0
    quantidadeEmEstoqueReservado: float = 0
    quantidadeMinimaEmEstoque: float = 0

    @property
    def disponivel(self) -> float:
        return max(0.0, self.quantidadeEmEstoque - self.quantidadeEmEstoqueReservado)


class ListaDeEstoques(_Resposta):
    contaHiperId: Optional[str] = None
    localDeEstoqueId: Optional[str] = None
    # O Swagger oficial chama este campo de "pontoDeSituacao" (sic) na listagem de estoques.
    pontoDeSituacao: int = 0
    pagina: int = 1
    estoquesDeProdutos: list[EstoqueDoProduto] = Field(default_factory=list)


# ---------------------------------------------------------------- pedido de venda
class Cliente(_Base):
    documento: str
    email: Optional[str] = ""
    inscricaoEstadual: Optional[str] = ""
    nomeDoCliente: str
    nomeFantasia: Optional[str] = ""


class Endereco(_Base):
    bairro: str
    cep: str
    codigoIbge: int
    complemento: Optional[str] = ""
    logradouro: str
    numero: str


class ItemDoPedido(_Base):
    produtoId: str
    quantidade: float
    precoUnitarioBruto: float
    precoUnitarioLiquido: float


class MeioDePagamento(_Base):
    idMeioDePagamento: int
    parcelas: int = 1
    valor: float


class Marketplace(_Base):
    cnpj: Optional[str] = None
    nome: Optional[str] = None


class Transportadora(_Base):
    documento: Optional[str] = None
    nome: Optional[str] = None


class PedidoDeVenda(_Base):
    cliente: Cliente
    enderecoDeCobranca: Endereco
    enderecoDeEntrega: Endereco
    itens: list[ItemDoPedido]
    meiosDePagamento: list[MeioDePagamento]
    numeroPedidoDeVenda: Optional[str] = ""
    observacaoDoPedidoDeVenda: Optional[str] = ""
    observacao: Optional[str] = None
    valorDoFrete: float = 0
    marketplace: Optional[Marketplace] = None
    transportadora: Optional[Transportadora] = None
    rastreio: Optional[str] = None


class RecebimentoDoPedido(_Resposta):
    id: Optional[str] = None


class Evento(_Base):
    chaveDocumentoFiscal: Optional[str] = None
    codigoDoTipoDeEvento: int = 0
    data: Optional[str] = None
    observacao: Optional[str] = None
    tipoDocumentoFiscal: Optional[str] = None
    urlArquivoXml: Optional[str] = None


class EventosDoPedido(_Resposta):
    cancelado: bool = False
    codigoDaSituacaoDeProcessamento: int = 0
    codigoDoPedidoDeVenda: Optional[str] = None
    data: Optional[str] = None
    eventos: list[Evento] = Field(default_factory=list)
    pedidoDeVendaId: Optional[str] = None

    @property
    def documento_fiscal(self) -> Optional[Evento]:
        """Último evento que carrega uma chave de documento fiscal (pedido faturado)."""
        com_chave = [e for e in self.eventos if e.chaveDocumentoFiscal]
        return com_chave[-1] if com_chave else None


class Mensagem(_Resposta):
    """Resposta simples (cancelamento, atualização de status)."""


# ------------------------------------------------------------------------ webhook
class ConfiguracaoDeWebhook(_Resposta):
    id: Optional[str] = None
    contaHiperId: Optional[str] = None
    ativo: bool = True
    tipo: list[int] = Field(default_factory=list)
    url: Optional[str] = None
    limiteDeTentativas: int = 3
    intervaloEmSegundosEntreTentativas: int = 0
