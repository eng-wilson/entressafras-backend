"""Extrai crop_params.tsv do pacote R INRAE CropWat para app/data/.

Uso: uv run python scripts/download_cropwat.py [versao]
"""

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

PACKAGE_URL = "https://inrae.r-universe.dev/src/contrib/CropWat_{version}.tar.gz"
MEMBER = "CropWat/inst/extdata/crop_params.tsv"
DEST = Path(__file__).resolve().parent.parent / "app" / "data" / "cropwat_crop_params.tsv"
EXPECTED_HEADER = "crop\tname\tLini\tLdev\tLmid\tLend\tKini\tKmax\tKend"


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "0.3.0"
    url = PACKAGE_URL.format(version=version)
    # O CDN do r-universe recusa (403) o User-Agent padrão do urllib.
    request = urllib.request.Request(url, headers={"User-Agent": "entressafras-backend"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        package = resp.read()
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        member = tar.extractfile(MEMBER)
        if member is None:
            raise SystemExit(f"{MEMBER} não encontrado em {url}")
        data = member.read()
    # Valida que é o TSV esperado antes de sobrescrever.
    header = data.split(b"\n", 1)[0].decode("utf-8")
    if not header.startswith(EXPECTED_HEADER):
        raise SystemExit(f"Cabeçalho inesperado em {MEMBER}: {header[:80]!r}")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(data)
    print(f"Salvo {len(data)} bytes em {DEST}")


if __name__ == "__main__":
    main()
