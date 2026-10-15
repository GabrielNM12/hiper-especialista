"""Exporta o OpenAPI do mock para openapi.json (útil para gerar clientes em outras linguagens)."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from mock_hiper.app import criar_app  # noqa: E402

spec = criar_app(despachar_webhooks=False).openapi()
(RAIZ / "openapi.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
print("openapi.json atualizado com", len(spec["paths"]), "rotas")
