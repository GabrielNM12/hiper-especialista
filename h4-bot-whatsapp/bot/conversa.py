"""Máquina de estados da conversa: busca → produto → variação → quantidade → carrinho → checkout → pedido no Hiper.

Cada entrada (texto livre ou id de botão/lista) produz uma lista de mensagens de saída.
Comandos globais funcionam em qualquer etapa: "menu", "carrinho", "meu pedido", "atendente", "cancelar".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from hiper_sdk import (
    Cliente, Endereco as EnderecoHiper, HiperClient, HiperEnvioIncerto, HiperErro, HiperErroAPI, ItemDoPedido,
    Marketplace, MeioDePagamento, PedidoDeVenda,
)

from .banco import Banco
from .catalogo import Catalogo, ItemCatalogo, Opcao, normalizar
from .config import Config
from .enderecos import Endereco, buscar_cep, so_digitos
from .mensagens import Botoes, Imagem, LinhaLista, Lista, Mensagem, Texto

log = logging.getLogger("bot")

PAGAMENTOS = {"pix": ("Pix", 12), "cartao": ("Cartão na entrega", 4), "dinheiro": ("Dinheiro", 1)}
SAUDACOES = {"oi", "ola", "bom dia", "boa tarde", "boa noite", "menu", "inicio", "comecar", "start", "e ai", "opa"}


@dataclass
class Entrada:
    texto: str = ""
    id: Optional[str] = None  # id do botão/linha de lista tocado

    @property
    def norm(self) -> str:
        return normalizar(self.texto)


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def cpf_valido(cpf: str) -> bool:
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for i in (9, 10):
        if (sum(int(c[n]) * ((i + 1) - n) for n in range(i)) * 10) % 11 % 10 != int(c[i]):
            return False
    return True


def mascarar(telefone: str) -> str:
    return f"***{telefone[-4:]}" if telefone else ""


class Conversa:
    def __init__(self, config: Config, banco: Banco, catalogo: Catalogo, hiper: HiperClient,
                 buscar_endereco: Callable[[str], Optional[Endereco]] | None = None):
        self.config = config
        self.banco = banco
        self.catalogo = catalogo
        self.hiper = hiper
        self.buscar_endereco = buscar_endereco or (lambda cep: buscar_cep(cep, config.usar_viacep))

    # ============================================================ entrada
    def processar(self, telefone: str, entrada: Entrada, nome_perfil: str = "") -> list[Mensagem]:
        estado, dados = self.banco.sessao(telefone, self.config.sessao_expira_minutos)
        dados["_telefone"] = telefone
        if nome_perfil:
            dados["perfil"] = nome_perfil
        if dados.get("atendimento_humano") and entrada.norm not in {"menu", "bot", "voltar"}:
            return []  # um atendente está conversando; o bot fica em silêncio
        try:
            novo_estado, saida = self._rotear(estado, dados, entrada)
        except HiperErro as exc:
            log.exception("erro ao falar com o Hiper para %s", mascarar(telefone))
            novo_estado, saida = estado, [Texto(f"Tive um problema para consultar a loja agora ({exc.__class__.__name__}). "
                                                "Pode tentar de novo em instantes?")]
        dados.pop("expirou", None)
        self.banco.salvar_sessao(telefone, novo_estado, dados)
        return saida

    def _rotear(self, estado: str, dados: dict, e: Entrada) -> tuple[str, list[Mensagem]]:
        t = e.norm
        if e.id:
            return self._resposta(estado, dados, e.id, e)
        if t in SAUDACOES or estado == "inicio":
            if t not in SAUDACOES and estado == "inicio" and len(t) > 2 and not dados.get("expirou"):
                return self._buscar(dados, e.texto)  # primeira mensagem já é uma busca ("tem tênis 38?")
            return "menu", self._menu(dados)
        if t in {"carrinho", "ver carrinho", "sacola"}:
            return "carrinho", self._carrinho(dados)
        if t in {"meu pedido", "meus pedidos", "pedido", "status", "rastrear"}:
            return "menu", self._meus_pedidos(dados)
        if t in {"atendente", "humano", "falar com atendente", "pessoa"}:
            dados["atendimento_humano"] = True
            return "humano", [Texto("Certo! Vou chamar alguém da loja para falar com você. Para voltar ao atendimento "
                                    "automático, é só escrever *menu*.")]
        if t in {"cancelar", "sair", "parar"}:
            dados.pop("checkout", None)
            return "menu", [Texto("Tudo bem, parei por aqui.")] + self._menu(dados)
        if t in {"bot", "voltar"} and dados.pop("atendimento_humano", None):
            return "menu", self._menu(dados)

        manipulador = {
            "quantidade": self._texto_quantidade, "checkout_nome": self._texto_nome, "checkout_cpf": self._texto_cpf,
            "checkout_cep": self._texto_cep, "checkout_numero": self._texto_numero,
            "checkout_endereco": self._texto_endereco_completo,
        }.get(estado)
        if manipulador:
            return manipulador(dados, e.texto)
        return self._buscar(dados, e.texto)

    # ===================================================== botões e listas
    def _resposta(self, estado: str, dados: dict, rid: str, e: Entrada) -> tuple[str, list[Mensagem]]:
        tipo, _, valor = rid.partition(":")
        if tipo == "menu":
            if valor == "buscar":
                return "busca", [Texto("O que você procura? Escreva o nome do produto e, se quiser, o tamanho. "
                                       "Ex.: *camiseta M*, *tênis 38*, *bolsa*.")]
            if valor == "carrinho":
                return "carrinho", self._carrinho(dados)
            if valor == "pedidos":
                return "menu", self._meus_pedidos(dados)
            return "menu", self._menu(dados)
        if tipo == "prod":
            return self._mostrar_produto(dados, valor)
        if tipo == "opa":
            return self._opcoes_do_atributo(dados, valor)
        if tipo == "opc":
            return self._escolher_opcao(dados, valor)
        if tipo == "qtd":
            return self._adicionar(dados, int(valor))
        if tipo == "cart":
            if valor == "finalizar":
                return self._iniciar_checkout(dados)
            if valor == "limpar":
                dados["carrinho"] = []
                return "menu", [Texto("Carrinho esvaziado.")] + self._menu(dados)
            if valor == "ver":
                return "carrinho", self._carrinho(dados)
            return "busca", [Texto("Beleza! O que mais você procura?")]
        if tipo == "nome":
            dados.setdefault("checkout", {})["nome"] = dados.get("perfil", "")
            return "checkout_cpf", [Texto("Agora preciso do seu *CPF* para a nota fiscal (só números).")]
        if tipo == "pag":
            return self._resumo_pedido(dados, valor)
        if tipo == "conf":
            if valor == "sim":
                return self._confirmar(dados)
            if valor == "editar":
                return "carrinho", self._carrinho(dados)
            dados.pop("checkout", None)
            return "menu", [Texto("Pedido não enviado. Seu carrinho continua salvo.")] + self._menu(dados)
        return estado, [Texto("Não entendi essa opção. Escreva *menu* para recomeçar.")]

    # ============================================================== menu
    def _menu(self, dados: dict) -> list[Mensagem]:
        nome = (dados.get("perfil") or "").split(" ")[0]
        ola = f"Olá{', ' + nome if nome else ''}! 👋 Aqui é o atendimento da *{self.config.loja_nome}*."
        corpo = ola + "\nPosso consultar preço e estoque e já deixar seu pedido separado na loja."
        if dados.get("expirou"):
            corpo = "Sua conversa anterior expirou, então recomeçamos. " + corpo
        return [Botoes(corpo, [("menu:buscar", "🔎 Buscar produto"), ("menu:carrinho", "🛒 Meu carrinho"),
                               ("menu:pedidos", "📦 Meus pedidos")], rodape="Escreva 'atendente' para falar com a equipe")]

    # ============================================================= busca
    def _buscar(self, dados: dict, consulta: str) -> tuple[str, list[Mensagem]]:
        itens, filtro = self.catalogo.buscar(consulta)
        dados["filtro"] = filtro
        if not itens:
            return "busca", [Texto(f"Não encontrei nada parecido com *{consulta.strip()}*. 😕 Tente outra palavra, "
                                   "como *camiseta*, *calça*, *tênis* ou *bolsa*.")]
        if len(itens) == 1:
            return self._mostrar_produto(dados, itens[0].id)
        linhas = [LinhaLista(f"prod:{i.id}", i.nome, self._descricao_curta(i, filtro)) for i in itens]
        corpo = f"Encontrei {len(itens)} opções" + (f" com tamanho *{filtro.upper()}*" if filtro else "") + ":"
        return "resultado", [Lista(corpo, "Ver produtos", [("Produtos", linhas)])]

    def _descricao_curta(self, i: ItemCatalogo, filtro: Optional[str]) -> str:
        if not i.tem_estoque:
            return f"{brl(i.preco)} · esgotado"
        if i.grade:
            opcoes = i.opcoes_com_estoque
            if filtro:
                opcoes = [o for o in opcoes if filtro in {normalizar(o.nome_a or ""), normalizar(o.nome_b or "")}]
            return f"{brl(i.preco)} · {len(opcoes)} opç{'ão' if len(opcoes) == 1 else 'ões'} em estoque"
        return f"{brl(i.preco)} · {int(i.disponivel)} em estoque"

    # =========================================================== produto
    def _mostrar_produto(self, dados: dict, item_id: str) -> tuple[str, list[Mensagem]]:
        self.catalogo.atualizar()
        item = self.catalogo.itens.get(item_id)
        if not item:
            return "busca", [Texto("Esse produto não está mais disponível. Quer buscar outro?")]
        dados["item"] = item_id
        saida: list[Mensagem] = []
        if item.imagem:
            saida.append(Imagem(item.imagem, f"{item.nome} · {brl(item.preco)}"))
        if not item.tem_estoque:
            return "busca", saida + [Botoes(f"*{item.nome}* está esgotado no momento. 😕",
                                            [("menu:buscar", "Buscar outro"), ("menu:menu", "Menu")])]
        if not item.grade:
            dados["opcao"] = item.id
            return "quantidade", saida + self._pergunta_quantidade(item.nome, item.preco, item.disponivel)

        opcoes = item.opcoes_com_estoque
        filtro = dados.get("filtro")
        if filtro:
            filtradas = [o for o in opcoes if filtro in {normalizar(o.nome_a or ""), normalizar(o.nome_b or "")}]
            if len(filtradas) == 1:
                saida.append(Texto(f"Tenho *{item.nome}* em {filtradas[0].rotulo}."))
                return self._escolher_opcao(dados, filtradas[0].id, saida)
            if filtradas:
                opcoes = filtradas
        if len(opcoes) <= 10:
            return "variacao", saida + [self._lista_opcoes(item, opcoes)]
        # muitas combinações: primeiro o atributo A (ex.: cor), depois o B (ex.: tamanho)
        valores = list(dict.fromkeys(o.nome_a for o in opcoes if o.nome_a))
        linhas = [LinhaLista(f"opa:{v}", v, f"{sum(1 for o in opcoes if o.nome_a == v)} tamanhos com estoque") for v in valores[:10]]
        tipo = opcoes[0].tipo_a or "Opção"
        return "variacao", saida + [Lista(f"*{item.nome}* · {brl(item.preco)}\nEscolha {tipo.lower()}:", tipo[:20],
                                          [(tipo, linhas)])]

    def _lista_opcoes(self, item: ItemCatalogo, opcoes: list[Opcao]) -> Lista:
        linhas = [LinhaLista(f"opc:{o.id}", o.rotulo, f"{int(o.disponivel)} em estoque · {brl(item.preco_de(o.id))}")
                  for o in opcoes]
        tipos = " e ".join(x for x in (opcoes[0].tipo_a, opcoes[0].tipo_b) if x) or "a opção"
        return Lista(f"*{item.nome}* · {brl(item.preco)}\nEscolha {tipos.lower()}:", "Ver opções", [("Disponíveis", linhas)])

    def _opcoes_do_atributo(self, dados: dict, valor: str) -> tuple[str, list[Mensagem]]:
        item = self.catalogo.itens.get(dados.get("item", ""))
        if not item:
            return "busca", [Texto("Vamos recomeçar a busca. O que você procura?")]
        opcoes = [o for o in item.opcoes_com_estoque if o.nome_a == valor]
        return "variacao", [self._lista_opcoes(item, opcoes)]

    def _escolher_opcao(self, dados: dict, opcao_id: str, prefixo: list[Mensagem] | None = None) -> tuple[str, list[Mensagem]]:
        item = self.catalogo.itens.get(dados.get("item", ""))
        opcao = next((o for o in item.opcoes if o.id == opcao_id), None) if item else None
        if not opcao:
            return "busca", [Texto("Essa opção não está mais disponível. Vamos buscar de novo?")]
        dados["opcao"] = opcao_id
        return "quantidade", (prefixo or []) + self._pergunta_quantidade(f"{item.nome} {opcao.rotulo}",
                                                                         item.preco_de(opcao_id), opcao.disponivel)

    def _pergunta_quantidade(self, nome: str, preco: float, disponivel: float) -> list[Mensagem]:
        n = int(disponivel)
        botoes = [(f"qtd:{q}", f"{q} unidade{'s' if q > 1 else ''}") for q in (1, 2, 3) if q <= n]
        extra = f"\nTenho {n} em estoque." if n <= 5 else ""
        return [Botoes(f"*{nome}* · {brl(preco)}{extra}\nQuantas você quer? (pode escrever outro número)", botoes)]

    def _texto_quantidade(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        m = re.search(r"\d+", texto)
        if not m:
            return self._buscar(dados, texto)  # mudou de ideia e buscou outra coisa
        return self._adicionar(dados, int(m.group()))

    def _adicionar(self, dados: dict, quantidade: int) -> tuple[str, list[Mensagem]]:
        item = self.catalogo.itens.get(dados.get("item", ""))
        opcao_id = dados.get("opcao")
        if not item or not opcao_id:
            return "busca", [Texto("Me diga de novo qual produto você quer.")]
        if item.grade:
            opcao = next(o for o in item.opcoes if o.id == opcao_id)
            disponivel, rotulo = opcao.disponivel, opcao.rotulo
        else:
            disponivel, rotulo = item.disponivel, ""
        if quantidade <= 0:
            return "quantidade", [Texto("A quantidade precisa ser pelo menos 1.")]
        ja_no_carrinho = sum(c["qtd"] for c in dados.get("carrinho", []) if c["produto_id"] == opcao_id)
        if quantidade + ja_no_carrinho > disponivel:
            return "quantidade", [Texto(f"Só tenho {int(disponivel - ja_no_carrinho)} disponível(is) agora. Quantas você quer?")]
        carrinho = dados.setdefault("carrinho", [])
        existente = next((c for c in carrinho if c["produto_id"] == opcao_id), None)
        if existente:
            existente["qtd"] += quantidade
        else:
            carrinho.append({"produto_id": opcao_id, "item_id": item.id, "nome": item.nome, "rotulo": rotulo,
                             "preco": item.preco_de(opcao_id), "qtd": quantidade})
        dados.pop("filtro", None)
        subtotal = sum(c["preco"] * c["qtd"] for c in carrinho)
        return "carrinho", [Botoes(f"✅ Adicionei {quantidade}× *{item.nome}{' ' + rotulo if rotulo else ''}*.\n"
                                   f"Carrinho: {len(carrinho)} item(ns) · {brl(subtotal)}",
                                   [("cart:finalizar", "Finalizar pedido"), ("cart:continuar", "Continuar comprando"),
                                    ("cart:ver", "Ver carrinho")])]

    # ========================================================== carrinho
    def _carrinho(self, dados: dict) -> list[Mensagem]:
        carrinho = dados.get("carrinho", [])
        if not carrinho:
            return [Botoes("Seu carrinho está vazio.", [("menu:buscar", "🔎 Buscar produto")])]
        linhas = [f"• {c['qtd']}× {c['nome']}{' ' + c['rotulo'] if c['rotulo'] else ''} — {brl(c['preco'] * c['qtd'])}"
                  for c in carrinho]
        subtotal = sum(c["preco"] * c["qtd"] for c in carrinho)
        return [Botoes("🛒 *Seu carrinho*\n" + "\n".join(linhas) + f"\n\nSubtotal: *{brl(subtotal)}*",
                       [("cart:finalizar", "Finalizar pedido"), ("cart:continuar", "Continuar comprando"),
                        ("cart:limpar", "Esvaziar")])]

    # ========================================================== checkout
    def _iniciar_checkout(self, dados: dict) -> tuple[str, list[Mensagem]]:
        if not dados.get("carrinho"):
            return "menu", self._carrinho(dados)
        dados["checkout"] = {}
        perfil = dados.get("perfil")
        if perfil:
            return "checkout_nome", [Botoes("Vamos fechar seu pedido! Qual é o seu *nome completo*?",
                                            [("nome:perfil", f"Usar {perfil.split(' ')[0]}"[:20])],
                                            rodape="Ou escreva seu nome")]
        return "checkout_nome", [Texto("Vamos fechar seu pedido! Qual é o seu *nome completo*?")]

    def _texto_nome(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        nome = " ".join(texto.split())
        if len(nome) < 3:
            return "checkout_nome", [Texto("Pode me dizer seu nome completo?")]
        dados.setdefault("checkout", {})["nome"] = nome
        return "checkout_cpf", [Texto("Agora preciso do seu *CPF* para a nota fiscal (só números).")]

    def _texto_cpf(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        if not cpf_valido(texto):
            return "checkout_cpf", [Texto("Esse CPF não parece válido. Confere e me manda de novo? (11 números)")]
        dados["checkout"]["cpf"] = so_digitos(texto)
        return "checkout_cep", [Texto("Qual o *CEP* de entrega?")]

    def _texto_cep(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        end = self.buscar_endereco(texto)
        if not end:
            return "checkout_cep", [Texto("Não encontrei esse CEP. Pode conferir? (8 números)")]
        dados["checkout"]["endereco"] = end.__dict__
        if end.logradouro:
            return "checkout_numero", [Texto(f"📍 {end.logradouro}, {end.bairro} — {end.cidade}/{end.uf}.\n"
                                             "Qual o *número* (e complemento, se tiver)?")]
        return "checkout_endereco", [Texto(f"📍 {end.cidade}/{end.uf}. Me passe *rua, número e bairro*.\n"
                                           "Ex.: Rua das Flores, 120, Centro")]

    def _texto_numero(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        m = re.match(r"\s*(\d+[a-zA-Z]?|s/?n)\b[\s,.-]*(.*)", texto, re.I)
        if not m:
            return "checkout_numero", [Texto("Me diga o número do endereço (ex.: 120, apto 3).")]
        dados["checkout"]["numero"] = m.group(1).upper()
        dados["checkout"]["complemento"] = m.group(2).strip()
        return self._perguntar_pagamento()

    def _texto_endereco_completo(self, dados: dict, texto: str) -> tuple[str, list[Mensagem]]:
        partes = [p.strip() for p in texto.split(",")]
        if len(partes) < 3 or not re.search(r"\d", partes[1]):
            return "checkout_endereco", [Texto("Preciso de *rua, número e bairro*, separados por vírgula. Ex.: Rua das Flores, 120, Centro")]
        end = dados["checkout"]["endereco"]
        end["logradouro"], end["bairro"] = partes[0], partes[2]
        dados["checkout"]["numero"], dados["checkout"]["complemento"] = partes[1], ", ".join(partes[3:])
        return self._perguntar_pagamento()

    def _perguntar_pagamento(self) -> tuple[str, list[Mensagem]]:
        return "checkout_pagamento", [Botoes("Como você prefere pagar?",
                                             [("pag:pix", "Pix"), ("pag:cartao", "Cartão na entrega"), ("pag:dinheiro", "Dinheiro")])]

    def _frete(self, subtotal: float) -> float:
        return 0.0 if subtotal >= self.config.frete_gratis_acima else self.config.frete_fixo

    def _resumo_pedido(self, dados: dict, pagamento: str) -> tuple[str, list[Mensagem]]:
        if pagamento not in PAGAMENTOS or "checkout" not in dados:
            return self._perguntar_pagamento()
        dados["checkout"]["pagamento"] = pagamento
        ck, carrinho = dados["checkout"], dados.get("carrinho", [])
        subtotal = round(sum(c["preco"] * c["qtd"] for c in carrinho), 2)
        frete = self._frete(subtotal)
        end = ck["endereco"]
        itens = "\n".join(f"• {c['qtd']}× {c['nome']}{' ' + c['rotulo'] if c['rotulo'] else ''} — {brl(c['preco'] * c['qtd'])}"
                          for c in carrinho)
        texto = (f"*Confira seu pedido*\n{itens}\n\nSubtotal: {brl(subtotal)}\nEntrega: {'grátis' if not frete else brl(frete)}\n"
                 f"*Total: {brl(subtotal + frete)}*\n\n👤 {ck['nome']} · CPF •••.{ck['cpf'][3:6]}.{ck['cpf'][6:9]}-••\n"
                 f"📍 {end['logradouro']}, {ck['numero']}{' ' + ck['complemento'] if ck.get('complemento') else ''} — "
                 f"{end['bairro']}, {end['cidade']}/{end['uf']}\n💳 {PAGAMENTOS[pagamento][0]}")
        return "confirmacao", [Botoes(texto, [("conf:sim", "✅ Confirmar"), ("conf:editar", "Alterar carrinho"),
                                              ("conf:nao", "Cancelar")])]

    # ======================================================= confirmação
    def _confirmar(self, dados: dict) -> tuple[str, list[Mensagem]]:
        ck, carrinho = dados.get("checkout", {}), dados.get("carrinho", [])
        if not carrinho or "pagamento" not in ck:
            return "menu", [Texto("Não encontrei um pedido para confirmar.")] + self._menu(dados)
        # Estoque em tempo real: o cache pode estar até alguns segundos atrasado em relação à loja física.
        faltas = []
        for c in carrinho:
            disponivel = self.catalogo.estoque_em_tempo_real(c["produto_id"])
            if disponivel < c["qtd"]:
                faltas.append((c, int(disponivel)))
        if faltas:
            for c, disp in faltas:
                c["qtd"] = disp
            dados["carrinho"] = [c for c in carrinho if c["qtd"] > 0]
            aviso = "\n".join(f"• {c['nome']} {c['rotulo']}: agora só tem {d}" for c, d in faltas)
            self.catalogo.atualizar(forcar=True)
            return "carrinho", [Texto("Ops! Enquanto conversávamos, o estoque mudou:\n" + aviso + "\nAjustei seu carrinho.")] \
                + self._carrinho(dados)

        subtotal = round(sum(c["preco"] * c["qtd"] for c in carrinho), 2)
        frete = self._frete(subtotal)
        total = round(subtotal + frete, 2)
        end = ck["endereco"]
        endereco = EnderecoHiper(logradouro=end["logradouro"], numero=ck["numero"], complemento=ck.get("complemento", ""),
                                 bairro=end["bairro"] or "Não informado", cep=end["cep"], codigoIbge=end["codigo_ibge"])
        numero = f"WA{self.banco.proximo_numero():05d}"
        telefone = dados.get("_telefone", "")
        pedido = PedidoDeVenda(
            cliente=Cliente(documento=ck["cpf"], nomeDoCliente=ck["nome"], email=""),
            enderecoDeCobranca=endereco, enderecoDeEntrega=endereco,
            itens=[ItemDoPedido(produtoId=c["produto_id"], quantidade=c["qtd"], precoUnitarioBruto=c["preco"],
                                precoUnitarioLiquido=c["preco"]) for c in carrinho],
            meiosDePagamento=[MeioDePagamento(idMeioDePagamento=PAGAMENTOS[ck["pagamento"]][1], valor=total)],
            valorDoFrete=frete, numeroPedidoDeVenda=numero,
            observacaoDoPedidoDeVenda=f"Pedido via WhatsApp {telefone} · pagamento: {PAGAMENTOS[ck['pagamento']][0]}",
            marketplace=Marketplace(cnpj=self.config.marketplace_cnpj, nome=self.config.marketplace_nome)
            if self.config.marketplace_cnpj else None,
        )
        resumo = "; ".join(f"{c['qtd']}x {c['nome']} {c['rotulo']}".strip() for c in carrinho)
        try:
            recebido = self.hiper.criar_pedido_de_venda(pedido)
        except HiperEnvioIncerto:
            self.banco.novo_pedido(telefone, numero, total, resumo, None, "incerto")
            dados.pop("checkout", None)
            dados["carrinho"] = []
            return "menu", [Texto(f"Recebi seu pedido *{numero}* ({brl(total)}), mas a confirmação da loja demorou. "
                                  "A equipe vai conferir e te avisa por aqui. 🙏")]
        except HiperErroAPI as exc:
            log.warning("Hiper recusou pedido %s: %s", numero, exc.erros)
            return "confirmacao", [Texto("A loja não conseguiu registrar o pedido agora. Vou chamar alguém da equipe; "
                                         "se preferir, escreva *atendente*.")]
        self.banco.novo_pedido(telefone, numero, total, resumo, recebido.id, "enviado")
        # Minimização de dados: CPF e endereço saem da sessão depois do pedido.
        dados.pop("checkout", None)
        dados["carrinho"] = []
        self.catalogo.atualizar(forcar=True)
        extra = "\nA loja vai te enviar a chave Pix para pagamento." if ck["pagamento"] == "pix" else ""
        return "menu", [Botoes(f"🎉 Pedido *{numero}* recebido! Total {brl(total)}.{extra}\n"
                               "Te aviso aqui quando a loja confirmar e quando a nota fiscal for emitida.",
                               [("menu:pedidos", "📦 Meus pedidos"), ("menu:buscar", "🔎 Comprar mais")])]

    # ======================================================= meus pedidos
    def _meus_pedidos(self, dados: dict) -> list[Mensagem]:
        pedidos = self.banco.pedidos_do_telefone(dados.get("_telefone", ""))
        if not pedidos:
            return [Botoes("Você ainda não tem pedidos por aqui.", [("menu:buscar", "🔎 Buscar produto")])]
        rotulo = {"enviado": "aguardando a loja", "faturado": "nota fiscal emitida ✅", "cancelado": "cancelado",
                  "incerto": "em conferência"}
        linhas = [f"• *{p['numero']}*{' (' + p['codigo'] + ')' if p['codigo'] else ''} — {brl(p['total'])} — "
                  f"{rotulo.get(p['status'], p['status'])}" for p in pedidos]
        return [Texto("📦 *Seus últimos pedidos*\n" + "\n".join(linhas))]
