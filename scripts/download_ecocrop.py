"""Baixa o CSV do FAO EcoCrop do repositório OpenCLIM/ecocrop para app/data/.

Uso: uv run python scripts/download_ecocrop.py [ref]
"""

import sys
import urllib.request
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/OpenCLIM/ecocrop/{ref}/EcoCrop_DB.csv"
DEST = Path(__file__).resolve().parent.parent / "app" / "data" / "EcoCrop_DB.csv"


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    url = REPO_RAW.format(ref=ref)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    # Valida que é o CSV esperado antes de sobrescrever.
    header = data.split(b"\n", 1)[0].decode("latin-1")
    if not header.startswith("EcoPortCode,ScientificName"):
        raise SystemExit(f"Cabeçalho inesperado em {url}: {header[:80]!r}")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(data)
    print(f"Salvo {len(data)} bytes em {DEST}")


if __name__ == "__main__":
    main()
