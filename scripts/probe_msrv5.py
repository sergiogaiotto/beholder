"""Probe streaming do MSRV5 - EXTRAÇÃO LPU.txt (3,1M linhas, 352 MB).

Não carrega para tabela — apenas:
- detecta encoding correto (CP-1252/Latin-1)
- conta linhas válidas de dados, separadores, headers repetidos
- sampleia primeiras/últimas N linhas
- valida que colunas parseiam consistentemente
- estima distribuição de datas para particionamento

Uso:
    .venv\\Scripts\\python.exe scripts\\probe_msrv5.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path(r"C:\_PERSONAL\beholder_data\MSRV5 - EXTRAÇÃO LPU.txt")
ENCODING_CANDIDATES = ["cp1252", "latin-1", "utf-8"]

SEPARATOR_RE = re.compile(r"^[\-\|]+$")
HEADER_DETECT_RE = re.compile(r"Data doc\.|N° de docu", re.IGNORECASE)


def detect_encoding(path: Path) -> str:
    """Tenta encodings na ordem; retorna o primeiro que decodifica os
    primeiros 256 KB sem erros e reconhece um header de tabela esperado."""
    sample = path.read_bytes()[:262_144]
    for enc in ENCODING_CANDIDATES:
        try:
            text = sample.decode(enc, errors="strict")
            if "Data doc" in text or "Pre" in text:
                return enc
        except UnicodeDecodeError:
            continue
    # fallback: cp1252 com replace
    return "cp1252"


def parse_row(line: str) -> list[str] | None:
    """|a|b|c|...| -> ['a','b','c',...]. Retorna None se a linha não parece data row."""
    line = line.strip()
    if not line or not line.startswith("|") or not line.endswith("|"):
        return None
    # Remove leading/trailing pipes
    parts = [p.strip() for p in line[1:-1].split("|")]
    return parts


def main() -> int:
    path = DEFAULT_PATH
    if not path.exists():
        print(f"ERRO: {path} não existe", file=sys.stderr)
        return 1

    encoding = detect_encoding(path)
    print(f"Encoding detectado: {encoding}", file=sys.stderr)

    total_lines = 0
    separator_lines = 0
    blank_lines = 0
    header_lines = 0
    data_lines = 0
    malformed_lines = 0

    head_samples: list[str] = []
    tail_samples: list[str] = []
    middle_samples: list[str] = []
    detected_headers: set[str] = set()

    year_counter: Counter[int] = Counter()
    column_count_counter: Counter[int] = Counter()
    column_sample_per_idx: list[list[str]] = []

    HEAD_N = 30
    MID_N = 5
    TAIL_N = 20

    file_size = path.stat().st_size
    print(f"Tamanho: {file_size/1024/1024:.1f} MB — iniciando stream ...", file=sys.stderr)

    with path.open("r", encoding=encoding, errors="replace") as f:
        for line in f:
            total_lines += 1
            stripped = line.rstrip("\n").rstrip("\r")

            if not stripped.strip():
                blank_lines += 1
                continue
            if SEPARATOR_RE.match(stripped.strip()):
                separator_lines += 1
                continue

            row = parse_row(stripped)
            if row is None:
                malformed_lines += 1
                continue

            # Detect header rows (column names embedded in data)
            joined = "|".join(row)
            if HEADER_DETECT_RE.search(joined) or any(
                "Data doc" in r or "N° de" in r or "Texto breve" in r for r in row
            ):
                header_lines += 1
                detected_headers.add(joined)
                continue

            # Data row
            data_lines += 1
            column_count_counter[len(row)] += 1

            # init column samples once we know typical width
            if not column_sample_per_idx and len(row) > 0:
                column_sample_per_idx = [[] for _ in range(len(row))]
            for j, v in enumerate(row):
                if j < len(column_sample_per_idx) and len(column_sample_per_idx[j]) < 5 and v:
                    column_sample_per_idx[j].append(v)

            # Extract year if first column looks like dd.mm.yyyy
            first = row[0] if row else ""
            if re.match(r"^\d{2}\.\d{2}\.\d{4}$", first):
                try:
                    year_counter[int(first[-4:])] += 1
                except ValueError:
                    pass

            if data_lines <= HEAD_N:
                head_samples.append(stripped)
            elif data_lines % 500_000 == 0 and len(middle_samples) < MID_N:
                middle_samples.append(stripped)

            if total_lines % 500_000 == 0:
                print(
                    f"  ... linha {total_lines:,} (data={data_lines:,}, sep={separator_lines:,})",
                    file=sys.stderr,
                    flush=True,
                )

    # Tail: collect last TAIL_N data rows by re-reading from end
    print("Coletando tail ...", file=sys.stderr)
    with path.open("rb") as fb:
        fb.seek(0, 2)
        end = fb.tell()
        block = 64 * 1024
        buf = b""
        while fb.tell() > 0 and buf.count(b"\n") < (TAIL_N * 5):
            step = min(block, fb.tell())
            fb.seek(-step, 1)
            buf = fb.read(step) + buf
            fb.seek(-step, 1)
        tail_text = buf.decode(encoding, errors="replace")
        candidate_tails = [
            ln for ln in tail_text.splitlines() if parse_row(ln) and not SEPARATOR_RE.match(ln.strip())
        ][-TAIL_N:]
        tail_samples = candidate_tails

    out = {
        "path": str(path),
        "file_size_bytes": file_size,
        "encoding": encoding,
        "total_lines": total_lines,
        "separator_lines": separator_lines,
        "blank_lines": blank_lines,
        "header_lines": header_lines,
        "data_lines": data_lines,
        "malformed_lines": malformed_lines,
        "detected_headers": sorted(detected_headers),
        "column_count_distribution": dict(column_count_counter.most_common()),
        "column_sample_first_5_rows_per_col": column_sample_per_idx,
        "year_distribution": dict(sorted(year_counter.items())),
        "head_samples": head_samples,
        "middle_samples": middle_samples,
        "tail_samples": tail_samples,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
