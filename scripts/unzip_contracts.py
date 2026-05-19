"""Descompacta os 58 ZIPs em CONTRATOS_PDF/ com nomes canônicos.

Convenção: `<empreiteira_short>__<CW######>__<original>.pdf`
  - empreiteira_short: primeiras 3 palavras da pasta da empreiteira em
    UPPER_SNAKE.
  - CW######: nome do ZIP (sem extensão).
  - original: nome do PDF dentro do ZIP, slugificado (espaços→_, sem
    acentos), preservando .pdf no final.

Saída: `C:\\_PERSONAL\\beholder_data\\CONTRATOS_PDF\\<arquivo>.pdf`.

Idempotente — se o destino já existe e tem mesmo tamanho, pula. Útil pra
rodar sem medo após edits.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import zipfile
from pathlib import Path


SOURCE = Path(r"C:\_PERSONAL\beholder_data\CONTRATOS")
DEST = Path(r"C:\_PERSONAL\beholder_data\CONTRATOS_PDF")


def _slug(text: str) -> str:
    """Remove acentos + colapsa espaços/símbolos não alfanuméricos → _."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("_")
    return cleaned or "unknown"


def _empreiteira_short(folder_name: str) -> str:
    """Folder name → primeira palavra significativa.

    "ABILITY TECNOLOGIA E SERVICOS S A" → "ABILITY"
    "ENGEMAN MNT INSTAL E TLC LTDA" → "ENGEMAN"
    "WG PEREIRA SERVICOS ME" → "WG_PEREIRA"
    """
    slug = _slug(folder_name).upper()
    parts = slug.split("_")
    # Pega até 2 primeiras palavras se a segunda não for ruído.
    noise = {"DE", "DA", "DO", "E", "MNT", "TLC", "SA", "S_A", "LTDA", "ME"}
    keep = []
    for p in parts[:3]:
        if p in noise:
            break
        keep.append(p)
        if len(keep) == 2:
            break
    return "_".join(keep) if keep else parts[0]


def main() -> int:
    if not SOURCE.exists():
        print(f"[ERRO] origem não existe: {SOURCE}", file=sys.stderr)
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    total_pdfs = 0
    skipped = 0
    extracted = 0

    for empreiteira_dir in sorted(SOURCE.iterdir()):
        if not empreiteira_dir.is_dir():
            continue
        short = _empreiteira_short(empreiteira_dir.name)
        for zip_path in sorted(empreiteira_dir.glob("*.zip")):
            cw_id = zip_path.stem
            with zipfile.ZipFile(zip_path) as zf:
                pdfs = [
                    info for info in zf.infolist()
                    if info.filename.lower().endswith(".pdf") and not info.is_dir()
                ]
                if not pdfs:
                    print(f"[WARN] zip sem PDF: {zip_path}", file=sys.stderr)
                    continue
                for info in pdfs:
                    total_pdfs += 1
                    original_name = Path(info.filename).name
                    out_name = f"{short}__{cw_id}__{_slug(original_name)}"
                    if not out_name.lower().endswith(".pdf"):
                        out_name += ".pdf"
                    out_path = DEST / out_name

                    if out_path.exists() and out_path.stat().st_size == info.file_size:
                        skipped += 1
                        continue

                    with zf.open(info) as src, out_path.open("wb") as dst:
                        dst.write(src.read())
                    extracted += 1
                    print(f"  {out_path.name} ({info.file_size / 1024:.0f} KB)")

    print(
        f"\n[OK] total={total_pdfs} extraidos={extracted} skip(ja_existe)={skipped} "
        f"-> {DEST}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
