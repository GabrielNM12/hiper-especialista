"""Bot de WhatsApp para consulta de estoque e pedidos no Hiper."""

import sys
from pathlib import Path

_SDK = Path(__file__).resolve().parents[2] / "hiper-sdk"
if _SDK.exists() and str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

__version__ = "1.0.0"
