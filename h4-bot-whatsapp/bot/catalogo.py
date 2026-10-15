"""Cache do catálogo do Hiper com busca tolerante a erros de digitação.

O catálogo é atualizado de forma incremental pelo ponto de sincronização (barato) e o estoque é conferido
em tempo real (GET /estoques/pontoDeSincronizacao?produtoId=) antes de fechar o pedido, porque entre
uma sincronização e outra a loja física pode ter vendido a última peça.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from hiper_sdk import HiperClient, Produto
from rapidfuzz import fuzz, process

TAMANHOS = {"pp", "p", "m", "g", "gg", "xg", "xgg", "eg", "u"}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", texto).strip()


@dataclass
class Opcao:
    id: str
    nome_a: Optional[str]
    nome_b: Optional[str]
    tipo_a: Optional[str]
    tipo_b: Optional[str]
    disponivel: float
    ativa: bool = True

    @property
    def rotulo(self) -> str:
        return " / ".join(x for x in (self.nome_a, self.nome_b) if x) or "Único"


@dataclass
class ItemCatalogo:
    id: str
    nome: str
    preco: float
    categoria: str
    marca: str
    imagem: Optional[str]
    grade: bool
    disponivel: float = 0
    opcoes: list[Opcao] = field(default_factory=list)
    precos_filhos: dict[str, float] = field(default_factory=dict)

    @property
    def opcoes_com_estoque(self) -> list[Opcao]:
        return [o for o in self.opcoes if o.ativa and o.disponivel > 0]

    @property
    def tem_estoque(self) -> bool:
        return bool(self.opcoes_com_estoque) if self.grade else self.disponivel > 0

    def preco_de(self, produto_id: str) -> float:
        return self.precos_filhos.get(produto_id, self.preco)


class Catalogo:
    def __init__(self, hiper: HiperClient, ttl: int = 60):
        self.hiper = hiper
        self.ttl = ttl
        self.itens: dict[str, ItemCatalogo] = {}
        self.ponto = 0
        self.atualizado_em = 0.0
        self._lock = threading.Lock()

    def atualizar(self, forcar: bool = False) -> int:
        with self._lock:
            if not forcar and time.time() - self.atualizado_em < self.ttl and self.itens:
                return 0
            lote = self.hiper.produtos_desde(self.ponto)
            filhos: dict[str, Produto] = {}
            alterados = 0
            for p in lote.produtos:
                if p.eh_filho_de_grade:
                    filhos[p.id] = p
                    continue
                alterados += 1
                if p.removido or not p.ativo:
                    self.itens.pop(p.id, None)
                    continue
                item = ItemCatalogo(id=p.id, nome=p.nome or "", preco=p.preco, categoria=p.categoria or "",
                                    marca=p.marca or "", imagem=p.imagem, grade=p.eh_pai_de_grade,
                                    disponivel=max(0.0, p.quantidadeEmEstoque - p.quantidadeEmEstoqueReservado))
                for v in p.variacao or []:
                    item.opcoes.append(Opcao(id=v.id, nome_a=v.nomeVariacaoA, nome_b=v.nomeVariacaoB, tipo_a=v.tipoVariacaoA,
                                             tipo_b=v.tipoVariacaoB, ativa=v.variacaoAtiva,
                                             disponivel=max(0.0, v.quantidadeEmEstoque - v.quantidadeEmEstoqueReservado)))
                self.itens[p.id] = item
            # filhos alterados sem o pai: atualiza preço/estoque da opção já em cache
            for f in filhos.values():
                pai = self.itens.get(f.produtoPrimarioId)
                if pai:
                    pai.precos_filhos[f.id] = f.preco
                    for o in pai.opcoes:
                        if o.id == f.id:
                            o.disponivel = max(0.0, f.quantidadeEmEstoque - f.quantidadeEmEstoqueReservado)
            self.ponto = lote.pontoDeSincronizacao
            self.atualizado_em = time.time()
            return alterados

    def buscar(self, consulta: str, limite: int = 8) -> tuple[list[ItemCatalogo], Optional[str]]:
        """Devolve itens mais parecidos e, se a consulta tinha um tamanho/numeração, esse filtro."""
        self.atualizar()
        termos = normalizar(consulta).split()
        filtro = next((t for t in termos if t in TAMANHOS or re.fullmatch(r"\d{2}", t)), None)
        palavras = [t for t in termos if t != filtro and t not in {"tem", "voce", "voces", "quero", "um", "uma", "de", "do", "da",
                                                                  "o", "a", "os", "as", "tamanho", "tam", "numero", "n"}]
        consulta_limpa = " ".join(palavras)
        if not consulta_limpa:
            return [], filtro
        candidatos = {i.id: normalizar(f"{i.nome} {i.categoria} {i.marca}") for i in self.itens.values()}
        resultados = process.extract(consulta_limpa, candidatos, scorer=fuzz.WRatio, limit=limite * 2, score_cutoff=62)
        itens = [self.itens[chave] for _, _, chave in resultados]
        if filtro:
            itens = [i for i in itens if not i.grade or any(_casa(o, filtro) for o in i.opcoes_com_estoque)]
        itens.sort(key=lambda i: not i.tem_estoque)  # sem estoque vai para o fim, mas aparece
        return itens[:limite], filtro

    def estoque_em_tempo_real(self, produto_id: str) -> float:
        e = self.hiper.estoque_do_produto(produto_id)
        return e.disponivel


def _casa(opcao: Opcao, filtro: str) -> bool:
    return filtro in {normalizar(opcao.nome_a or ""), normalizar(opcao.nome_b or "")}
