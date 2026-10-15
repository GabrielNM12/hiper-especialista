"""Baixa XMLs de notas emitidas pelo Hiper.

Na API real, cada pedido de venda faturado traz em `eventos[].urlArquivoXml` o link do XML
(GET /api/v1/pedido-de-venda/eventos/{id}). `importar_por_eventos` usa exatamente esse caminho.
`importar_do_mock` lista os pedidos pelo simulador do mock H1 e depois segue o mesmo fluxo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import httpx


def _salvar(http: httpx.Client, url: str, chave: str, destino: Path) -> bool:
    r = http.get(url)
    if r.status_code != 200 or b"<" not in r.content[:100]:
        return False
    (destino / f"{chave}-nfe.xml").write_bytes(r.content)
    return True


def importar_por_eventos(http: httpx.Client, token: str, pedidos: Iterable[str], destino: str | Path,
                         base_url: str = "https://ms-ecommerce.hiper.com.br") -> int:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    total = 0
    for pedido_id in pedidos:
        ev = http.get(f"{base_url}/api/v1/pedido-de-venda/eventos/{pedido_id}",
                      headers={"Authorization": f"Bearer {token}"}).json()
        for e in ev.get("eventos", []):
            if e.get("urlArquivoXml") and e.get("chaveDocumentoFiscal"):
                total += _salvar(http, e["urlArquivoXml"], e["chaveDocumentoFiscal"], destino)
    return total


def importar_do_mock(url: str, destino: str | Path) -> int:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=url, timeout=30) as http:
        total = 0
        for pedido in http.get("/simulador/pedidos").json():
            if pedido.get("chaveNF"):
                total += _salvar(http, f"/arquivos/xml/{pedido['chaveNF']}.xml", pedido["chaveNF"], destino)
        return total
