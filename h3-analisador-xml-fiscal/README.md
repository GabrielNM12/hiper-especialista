# H3 · Analisador de XMLs fiscais do lojista

Lê a pasta (ou o .zip) de XMLs de NF-e e NFC-e que o lojista baixa no Hiper Gestão em **Contabilidade → Download de XML**
(ou de qualquer ERP) e devolve, em segundos, o que o contador e o dono da loja querem saber:

- **Vendas**: receita líquida, ticket médio, vendas por dia e por hora, meios de pagamento;
- **Curva ABC** de produtos;
- **Margem por NCM**, usando o custo real das notas de entrada (com frete e ICMS-ST);
- **CFOPs** usados, **notas canceladas** e quanto tempo depois foram canceladas;
- **21 checagens fiscais** com explicação e "como corrigir", incluindo a preparação para a **reforma tributária (IBS/CBS)**.

Saídas: dashboard interativo, relatório HTML (com modo escuro), **relatório PDF mensal** e CSVs no padrão do
Excel brasileiro.

![Relatório HTML](../docs/img/h3-relatorio.png)

## Rodando

```bash
pip install -r requirements.txt

python -m analisador exemplos exemplos/xml          # gera um mês sintético (658 notas, com erros plantados)
python -m analisador analisar exemplos/xml          # relatorios/relatorio.html, relatorio.pdf e CSVs
python -m analisador dashboard                       # http://localhost:8501
```

Quer ver sem instalar nada? O relatório do mês de exemplo está em
[`exemplos/relatorio-setembro-2026/`](exemplos/relatorio-setembro-2026/) (HTML, PDF e CSVs) e os XMLs em
`exemplos/xml-exemplo-setembro-2026.zip`, que você pode enviar direto no dashboard.

Integração com o H1: com o mock do Hiper rodando, `python -m analisador importar-hiper` baixa os XMLs das notas
faturadas. Na API real o caminho é o mesmo: cada pedido faturado traz o link do XML em
`eventos[].urlArquivoXml` (`importar.importar_por_eventos`).

## Checagens

| Regra | O que verifica | Severidade |
|---|---|---|
| R01, R02 | Dígito verificador da chave de acesso; chave coerente com CNPJ, modelo, série e número | erro |
| R03, R04 | CNPJ do emitente e CPF/CNPJ do destinatário | erro / aviso |
| R05 | Nota sem protocolo de autorização | erro |
| R06 | Documentos de homologação (sem valor fiscal) | info |
| R07 | vNF = vProd − vDesc + vFrete + vSeg + vOutro + vST + vIPI, e soma dos itens = vProd | erro |
| R08, R09, R10 | CFOP x tipo de operação, x destino (interna/interestadual), CFOP 6xxx em NFC-e | erro |
| R11 | CRT x grupo de ICMS (Simples usa CSOSN, regime normal usa CST) | erro |
| R12 | Substituição tributária: CSOSN 500 / CST 60 com CFOP 5102, ou CFOP 5405 sem ST | erro |
| R13 | Produto com ST sem CEST | aviso |
| R14, R15 | NCM com 8 dígitos; GTIN com dígito verificador válido | erro / aviso |
| R16, R17, R18 | **IBS/CBS (2026)**: grupo `IBSCBS` presente para o regime normal, alíquotas de teste (CBS 0,9%, IBS 0,1%) e base × alíquota | aviso |
| R19, R20 | Mesmo produto vendido com NCM ou tributação de ICMS diferentes ao longo do mês | erro / aviso |
| R21 | Simples Nacional: grupo IBS/CBS ainda não exigido em 2026 | info |

As regras de 2026 seguem a NT 2025.002-RTC e a LC 214/2025: para o regime normal o grupo é exigido, com a
rejeição em produção suspensa até nova data; para o Simples Nacional a validação aguarda NT futura. Confira sempre
a versão vigente da nota técnica.

O mês de exemplo tem **7 erros plantados** (lista em `analisador/gerador.py`) e os testes garantem que todos são
encontrados e que nenhum outro erro grave é apontado.

## Como funciona

```
analisador/
  leitor.py       nfeProc, NFe sem protocolo, eventos de cancelamento; pastas e ZIPs; XML seguro (sem XXE)
  validacoes.py   as 21 regras, cada uma com severidade e sugestão de correção
  analises.py     faturamento, ABC, margem por NCM, CFOPs, cancelamentos (pandas)
  relatorio.py    HTML com gráficos SVG (tooltip, modo escuro), PDF (reportlab) e CSVs
  dashboard.py    Streamlit: enviar XMLs/ZIP, abas, downloads
  gerador.py      mês sintético com erros plantados
  importar.py     baixa XMLs pelos eventos do pedido de venda do Hiper
```

Decisões que importam na prática:

- **Perspectiva da empresa**: a nota do fornecedor é saída para ele e **entrada** para a loja; a empresa analisada
  é detectada como o emitente mais frequente das saídas (ou informada com `--cnpj`).
- **Identidade do produto entre notas** é o GTIN (o código interno do fornecedor é diferente do da loja). Um GTIN
  errado separa o produto em dois, e isso aparece na curva ABC e na cobertura de custo.
- **Custo médio ponderado** de entrada inclui frete e ICMS-ST pagos, que fazem parte do custo do Simples Nacional.
- Cancelamentos vêm do evento 110111 (ou de `cStat` 101/135) e saem da receita.

## Testes

```bash
pytest -q    # 16 testes: leitura, cada regra, erros plantados, análises, relatório e uma NF-e gerada pelo mock H1
```
