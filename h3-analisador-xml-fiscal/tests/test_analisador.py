import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "h3-analisador-xml-fiscal"))

from analisador.analises import analisar  # noqa: E402
from analisador.gerador import Gerador, gerar_exemplos  # noqa: E402
from analisador.leitor import Lote, aplicar_eventos, ler, ler_bytes  # noqa: E402
from analisador.relatorio import exportar  # noqa: E402
from analisador.util_fiscal import dv_chave  # noqa: E402
from analisador.validacoes import validar  # noqa: E402


@pytest.fixture(scope="module")
def arquivos():
    return Gerador().gerar()


@pytest.fixture(scope="module")
def lote(arquivos):
    lt = Lote()
    for nome, xml in arquivos.items():
        ler_bytes(nome, xml.encode(), lt)
    aplicar_eventos(lt)
    return lt


@pytest.fixture(scope="module")
def resultado(lote):
    return analisar(lote)


def _um(arquivos, prefixo="saidas/", contem="<mod>65</mod>"):
    return next(x for n, x in arquivos.items() if n.startswith(prefixo) and contem in x)


def _validar_xml(xml: str):
    lt = Lote()
    ler_bytes("teste.xml", xml.encode(), lt)
    assert lt.documentos, lt.falhas
    return {a.regra for a in validar(lt)}


# ---------------------------------------------------------------- gerador e leitor
def test_gerador_e_leitor(lote, arquivos):
    assert len(lote.falhas) == 0
    assert len(lote.documentos) == sum(1 for n in arquivos if not n.startswith("eventos/"))
    assert all(dv_chave(d.chave[:43]) == int(d.chave[43]) for d in lote.documentos)
    assert {d.modelo for d in lote.documentos} == {55, 65}
    assert sum(d.cancelada for d in lote.documentos) == len(lote.eventos) > 0


def test_le_pasta_e_zip(tmp_path):
    gerar_exemplos(tmp_path / "xml", semente=3)
    pasta = ler(tmp_path / "xml")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in (tmp_path / "xml").rglob("*.xml"):
            z.write(f, f.relative_to(tmp_path))
    (tmp_path / "lote.zip").write_bytes(buf.getvalue())
    zipado = ler(tmp_path / "lote.zip")
    assert len(zipado.documentos) == len(pasta.documentos) > 0


def test_nfe_sem_protocolo_e_xml_invalido(arquivos):
    xml = _um(arquivos)
    sem_prot = re.sub(r"<protNFe.*</protNFe>", "", xml)
    nfe_nua = re.sub(r"^.*?<NFe>", '<NFe xmlns="http://www.portalfiscal.inf.br/nfe">', sem_prot).replace("</nfeProc>", "")
    lt = Lote()
    ler_bytes("sem.xml", nfe_nua.encode(), lt)
    ler_bytes("quebrado.xml", b"<nfeProc><NFe>", lt)
    ler_bytes("outro.xml", b"<cte/>", lt)
    assert len(lt.documentos) == 1 and lt.documentos[0].c_stat is None
    assert len(lt.falhas) == 2
    assert "R05" in {a.regra for a in validar(lt)}


def test_xxe_nao_e_resolvido():
    malicioso = b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>
    <nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe1"><ide><natOp>&e;</natOp></ide></infNFe></NFe></nfeProc>"""
    lt = Lote()
    ler_bytes("x.xml", malicioso, lt)
    assert all("root:" not in (d.nat_op or "") for d in lt.documentos)


# ----------------------------------------------------------------- erros plantados
def test_todos_os_erros_plantados_sao_encontrados(resultado):
    regras = {a.regra for a in resultado.achados}
    assert {"R07", "R10", "R13", "R15", "R16", "R19", "R20"} <= regras
    r19 = next(a for a in resultado.achados if a.regra == "R19")
    assert "65059000" in r19.mensagem and "Bone" in r19.produto
    r16 = next(a for a in resultado.achados if a.regra == "R16")
    assert "Passo Firme" in r16.mensagem


def test_sem_falsos_positivos_graves(resultado):
    # só os 3 erros plantados podem aparecer como "erro"
    erros = [a for a in resultado.achados if a.severidade == "erro"]
    assert sorted(a.regra for a in erros) == ["R07", "R10", "R19"]
    assert not any(a.regra in ("R17", "R18") for a in resultado.achados)  # IBS/CBS calculados certo


# ---------------------------------------------------------- regras em casos montados
def test_r01_r02_chave(arquivos):
    xml = _um(arquivos)
    chave = re.search(r'Id="NFe(\d{44})"', xml).group(1)
    dv_errado = chave[:43] + str((int(chave[43]) + 1) % 10)
    assert "R01" in _validar_xml(xml.replace(chave, dv_errado))
    outra_serie = chave[:22] + "009" + chave[25:43]
    outra_serie += str(dv_chave(outra_serie))
    assert "R02" in _validar_xml(xml.replace(chave, outra_serie))


def test_r08_r09_cfop(arquivos):
    xml = _um(arquivos, contem="<CFOP>6108</CFOP>")
    assert "R09" in _validar_xml(xml.replace("<idDest>2</idDest>", "<idDest>1</idDest>"))
    assert "R08" in _validar_xml(xml.replace("<tpNF>1</tpNF>", "<tpNF>0</tpNF>"))


def test_r11_r12_r14(arquivos):
    xml = _um(arquivos, contem="<CSOSN>500</CSOSN>")
    assert "R11" in _validar_xml(xml.replace("<CRT>1</CRT>", "<CRT>3</CRT>"))
    assert "R12" in _validar_xml(re.sub(r"<CFOP>5405</CFOP>", "<CFOP>5102</CFOP>", xml))
    assert "R14" in _validar_xml(re.sub(r"<NCM>\d{8}</NCM>", "<NCM>6109</NCM>", xml, count=1))


def test_r17_r18_ibscbs(arquivos):
    xml = _um(arquivos, prefixo="entradas/", contem="<IBSCBS>")
    assert not {"R16", "R17", "R18"} & _validar_xml(xml)
    assert "R17" in _validar_xml(xml.replace("<pCBS>0.9000</pCBS>", "<pCBS>8.8000</pCBS>", 1))
    vcbs = re.search(r"<vCBS>([\d.]+)</vCBS>", xml).group(1)
    assert "R18" in _validar_xml(xml.replace(f"<vCBS>{vcbs}</vCBS>", "<vCBS>999.99</vCBS>", 1))


# ------------------------------------------------------------------------- análises
def test_resumo_exclui_canceladas(resultado, lote):
    validas = [d for d in lote.documentos if d.tp_nf == 1 and d.emit_cnpj == resultado.cnpj_loja and not d.cancelada]
    esperado = round(sum(i.v_liquido for d in validas for i in d.itens), 2)
    assert resultado.resumo["receita_liquida"] == esperado
    assert resultado.resumo["canceladas"] == len(lote.eventos)
    assert resultado.resumo["notas_de_entrada"] == 8


def test_curva_abc(resultado):
    abc = resultado.abc
    assert abc["participacao"].sum() == pytest.approx(1.0)
    assert list(abc["receita"]) == sorted(abc["receita"], reverse=True)
    assert abc["classe"].iat[0] == "A" and abc["classe"].iat[-1] == "C"
    assert abc.loc[abc["classe"] == "A", "participacao"].sum() >= 0.8


def test_margem_usa_custo_das_entradas(resultado):
    mg = resultado.margem_ncm.set_index("ncm")
    camisa = mg.loc["61091000"]
    vendidas = resultado.abc.loc[resultado.abc["ncm"] == "61091000", "qtd"].sum()
    assert camisa["custo"] == pytest.approx(22.40 * vendidas, rel=1e-6)  # custo do gerador
    body = mg.loc["33030020"]
    assert body["custo"] / resultado.abc.loc[resultado.abc["ncm"] == "33030020", "qtd"].sum() > 18.70  # inclui ST


def test_cfops_na_perspectiva_da_loja(resultado):
    entradas = resultado.cfops[resultado.cfops["operacao"] == "Entrada"]
    assert not entradas.empty and entradas["descricao"].str.startswith("CFOP do fornecedor").all()


# ---------------------------------------------------------------- integração com H1
def test_le_nfe_gerada_pelo_mock_do_hiper():
    pytest.importorskip("fastapi", reason="instale h1-mock-api-hiper/requirements.txt para este teste")
    pytest.importorskip("jwt")
    sys.path.insert(0, str(RAIZ / "h1-mock-api-hiper"))
    from mock_hiper.estado import Config, Estado
    from mock_hiper.nfe import gerar_nfe
    estado = Estado(Config(atraso_processamento_s=0))
    filho = next(p for p in estado.produtos.values() if p["paiId"] and p["estoque"] > 1)
    corpo = {"cliente": {"documento": "52998224725", "nomeDoCliente": "Ana"},
             "enderecoDeCobranca": {"bairro": "C", "cep": "01310100", "codigoIbge": 3550308, "logradouro": "R", "numero": "1"},
             "enderecoDeEntrega": {"bairro": "C", "cep": "01310100", "codigoIbge": 3550308, "logradouro": "R", "numero": "1"},
             "itens": [{"produtoId": filho["id"], "quantidade": 2, "precoUnitarioBruto": filho["preco"],
                        "precoUnitarioLiquido": round(filho["preco"] - 5, 2)}],
             "meiosDePagamento": [{"idMeioDePagamento": 12, "valor": round((filho["preco"] - 5) * 2 + 10, 2)}],
             "valorDoFrete": 10, "Marketplace": {"Cnpj": "12605982000124", "Nome": "Loja"}}
    pedido = estado.criar_pedido(corpo)
    estado.processar_pendentes()
    _, _, xml = gerar_nfe(estado, pedido, 1)
    lt = Lote()
    ler_bytes("hiper.xml", xml.encode(), lt)
    doc = lt.documentos[0]
    assert doc.v_nf == round((filho["preco"] - 5) * 2 + 10, 2) and doc.v_desc == 10.0
    assert {a.severidade for a in validar(lt)} <= {"info"}  # nenhum erro ou aviso na nota do H1


# ------------------------------------------------------------------------ relatório
def test_exportar(resultado, tmp_path):
    saidas = exportar(resultado, tmp_path)
    html = saidas["html"].read_text(encoding="utf-8")
    for trecho in ("Curva ABC", "Margem por NCM", "Problemas fiscais", "R19", "<svg"):
        assert trecho in html
    assert saidas["pdf"].read_bytes().startswith(b"%PDF")
    achados = pd.read_csv(saidas["achados"], sep=";", encoding="utf-8-sig")
    assert len(achados) == len(resultado.achados)
