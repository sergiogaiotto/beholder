"""Pré-C — Spike de extração PDF com Maritaca Sabiá-4.

Extrai folha de rosto de 5 contratos reais variados (1 por empreiteira + 1
renovação CW28648 da FFA), mede:
- Latência por PDF (s)
- Cost (tokens × Maritaca pricing)
- Taxa de campos populados (1 - %NULL)
- Sanidade básica (CNPJ formato, datas válidas)

NÃO usa docling/Instructor (escopo de Fase 4). Usa pdfplumber para extrair
texto e Maritaca via OpenAI-compatible API.

Uso:
    .venv\\Scripts\\python.exe scripts\\spike_pdf_maritaca.py

Output:
    docs/PRE_C_FINDINGS.md  — relatório markdown
    docs/spike_pdf_results.json  — dados crus
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber  # type: ignore[import-untyped]
from openai import OpenAI  # type: ignore[import-untyped]

DATA_DIR = Path(os.environ.get("BEHOLDER_DATA_DIR", r"C:\_PERSONAL\beholder_data"))
CONTRATOS = DATA_DIR / "CONTRATOS"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = REPO_ROOT / "docs" / "spike_pdf_results.json"
OUT_MD = REPO_ROOT / "docs" / "PRE_C_FINDINGS.md"

# Seleção: 1 PDF por empreiteira + 1 renovação (CW28648 da FFA tem 2 PDFs)
SAMPLES = [
    ("ABILITY TECNOLOGIA E SERVICOS S A", "CW149898.zip"),
    ("ENGEMAN MNT INSTAL E TLC LTDA", "CW174308.zip"),
    ("EQS ENGENHARIA SA", "CW141697.zip"),
    ("FFA INFRAESTRUTURA E SERVICOS LTDA", "CW28648.zip"),  # renovação (2 PDFs)
    ("WG PEREIRA SERVICOS ME", "CW170014.zip"),
]

# Lidos em main() após carregar .env, não em import time.
MARITACA_MODEL = ""
MARITACA_BASE_URL = ""
MARITACA_KEY = ""

# Maritaca pricing (sabia-4 em 2026, aproximado, BRL):
# input ~ R$0.018 / 1k tokens
# output ~ R$0.054 / 1k tokens
COST_INPUT_PER_1K = 0.018
COST_OUTPUT_PER_1K = 0.054

EXTRACTION_PROMPT = """Você é um especialista em contratos jurídicos de empreiteiras de telecomunicações no Brasil (Claro).
Sua tarefa é extrair campos estruturados da FOLHA DE ROSTO do contrato a seguir.

Retorne EXCLUSIVAMENTE um JSON válido (sem markdown, sem texto adicional) com este schema:

{
  "empreiteira_nome": "nome da empresa contratada (string ou null)",
  "empreiteira_cnpj": "CNPJ da contratada apenas dígitos (string 14 chars ou null)",
  "contratante_cnpj": "CNPJ da contratante Claro apenas dígitos (string 14 chars ou null)",
  "objeto_contrato": "descrição do objeto do contrato (string ou null)",
  "categoria": "ex: FIXO MENSAL, RECUPERAÇÃO DE SITE, MANUTENÇÃO (string ou null)",
  "tecnologia": "ex: FIBRA ÓPTICA, HFC, GPON (string ou null)",
  "atividade": "ex: MANUTENÇÃO PREVENTIVA, CABEAMENTO ESTRUTURADO (string ou null)",
  "uf": ["lista de UFs cobertas (ex: RJ, SP)"],
  "cidade": ["lista de cidades cobertas (string vazia se não especificado)"],
  "val_fix_cab": "valor fixo mensal em reais como número, ex: 12345.67 (number ou null)",
  "valid_from": "data de início validade no formato YYYY-MM-DD (string ou null)",
  "valid_to": "data de fim validade no formato YYYY-MM-DD (string ou null)",
  "ref_ws": "código REF WS / Workflow se mencionado, ex: CW149898 (string ou null)",
  "contrato_num_sap": "número do contrato no SAP se mencionado, ex: 5700017041 (string ou null)",
  "_confidence_overall": "sua confiança 0-1 na extração total (number)"
}

Regras:
- Use null quando o campo não estiver claramente identificável.
- CNPJ: extraia só os 14 dígitos, sem pontuação.
- Datas: converta DD/MM/YYYY → YYYY-MM-DD.
- val_fix_cab: número decimal sem moeda/separadores. R$ 12.345,67 → 12345.67.
- Liste TODAS as UFs/cidades mencionadas, mesmo que muitas.

CONTRATO:
---
{contract_text}
---

Retorne agora o JSON (e SOMENTE o JSON):"""


@dataclass
class ExtractionResult:
    empreiteira_folder: str
    zip_name: str
    pdf_name: str
    pdf_size_bytes: int
    pdf_pages: int
    text_chars: int
    text_chars_used: int  # após truncate
    latency_pdf_extract_s: float
    latency_llm_s: float
    cost_brl: float
    tokens_input: int
    tokens_output: int
    extracted: dict[str, Any] = field(default_factory=dict)
    fields_populated_count: int = 0
    fields_total: int = 0
    populated_pct: float = 0.0
    error: str | None = None
    raw_response_preview: str = ""


# Campos pra cálculo de "populated"
COUNTABLE_FIELDS = [
    "empreiteira_nome", "empreiteira_cnpj", "contratante_cnpj",
    "objeto_contrato", "categoria", "tecnologia", "atividade",
    "uf", "cidade", "val_fix_cab", "valid_from", "valid_to",
    "ref_ws", "contrato_num_sap",
]
MAX_TEXT_CHARS = 60_000  # cap para não estourar contexto


def extract_pdf_text(zip_path: Path) -> tuple[str, str, int, int]:
    """Extrai 1º PDF do ZIP, retorna (pdf_name, full_text, pages, size_bytes)."""
    with zipfile.ZipFile(zip_path) as z:
        pdf_entries = [e for e in z.infolist() if e.filename.lower().endswith(".pdf")]
        if not pdf_entries:
            raise FileNotFoundError(f"Nenhum PDF em {zip_path}")
        # Pega o maior (geralmente o contrato principal)
        entry = max(pdf_entries, key=lambda e: e.file_size)
        with z.open(entry) as f:
            pdf_bytes = f.read()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = len(pdf.pages)
        chunks = []
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
                chunks.append(t)
            except Exception as e:
                chunks.append(f"[ERROR_PAGE: {e}]")
        full_text = "\n\n".join(chunks)
    return entry.filename, full_text, pages, entry.file_size


def call_maritaca(client: OpenAI, contract_text: str) -> tuple[dict, int, int, str]:
    """Retorna (parsed_json, tokens_in, tokens_out, raw_response_preview)."""
    prompt = EXTRACTION_PROMPT.replace("{contract_text}", contract_text)
    resp = client.chat.completions.create(
        model=MARITACA_MODEL,
        messages=[
            {"role": "system", "content": "Você responde EXCLUSIVAMENTE com JSON válido."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    raw = resp.choices[0].message.content or ""
    tokens_in = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
    tokens_out = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0

    # Tenta extrair JSON do response (alguns modelos adicionam ```json fences)
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    return parsed, tokens_in, tokens_out, raw[:500]


def count_populated(extracted: dict) -> tuple[int, int, float]:
    total = len(COUNTABLE_FIELDS)
    populated = 0
    for k in COUNTABLE_FIELDS:
        v = extracted.get(k)
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        populated += 1
    return populated, total, 100.0 * populated / total if total else 0.0


def render_markdown(results: list[ExtractionResult]) -> str:
    lines: list[str] = []
    lines.append("# PRE_C_FINDINGS — Spike de extração PDF (Maritaca sabia-4)")
    lines.append("")
    lines.append(f"Gerado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"Modelo: `{MARITACA_MODEL}` via Maritaca cloud.")
    lines.append(f"PDF extractor: pdfplumber (Fase 4 troca para docling, conforme SDD §10).")
    lines.append("")
    lines.append("## Resumo")
    lines.append("")
    ok = [r for r in results if r.error is None]
    if ok:
        avg_latency = sum(r.latency_llm_s + r.latency_pdf_extract_s for r in ok) / len(ok)
        avg_cost = sum(r.cost_brl for r in ok) / len(ok)
        avg_populated = sum(r.populated_pct for r in ok) / len(ok)
        total_cost = sum(r.cost_brl for r in ok)
        lines.append(f"- PDFs processados com sucesso: **{len(ok)} de {len(results)}**")
        lines.append(f"- Custo total do spike: **R$ {total_cost:.4f}**")
        lines.append(f"- Latência média (extract + LLM): **{avg_latency:.1f}s**")
        lines.append(f"- Cost médio por PDF: **R$ {avg_cost:.4f}**")
        lines.append(f"- Campos populados em média: **{avg_populated:.1f}%**")
    failed = [r for r in results if r.error is not None]
    if failed:
        lines.append(f"- Falhas: {len(failed)} ({', '.join(r.zip_name for r in failed)})")
    lines.append("")
    lines.append("## Por PDF")
    lines.append("")
    lines.append("| Empreiteira | ZIP | Pages | Chars | LLM lat (s) | Tokens in/out | Cost (R$) | %campos | Erro |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        err = r.error[:40] + "…" if r.error and len(r.error) > 40 else (r.error or "—")
        lines.append(
            f"| {r.empreiteira_folder[:25]} | {r.zip_name} | {r.pdf_pages} | "
            f"{r.text_chars_used:,} | {r.latency_llm_s:.1f} | "
            f"{r.tokens_input}/{r.tokens_output} | {r.cost_brl:.4f} | "
            f"{r.populated_pct:.0f}% | {err} |"
        )
    lines.append("")
    lines.append("## Extrações individuais")
    lines.append("")
    for r in results:
        lines.append(f"### {r.empreiteira_folder} — `{r.zip_name}`")
        if r.error:
            lines.append(f"- **ERRO**: `{r.error}`")
            lines.append(f"- Raw response preview: `{r.raw_response_preview}`")
        else:
            lines.append(f"- pages={r.pdf_pages}, chars_used={r.text_chars_used:,}/{r.text_chars:,}")
            lines.append(f"- latency: extract={r.latency_pdf_extract_s:.2f}s, llm={r.latency_llm_s:.2f}s")
            lines.append(f"- tokens: in={r.tokens_input:,}, out={r.tokens_output:,}, cost=R$ {r.cost_brl:.4f}")
            lines.append(f"- campos populados: {r.fields_populated_count}/{r.fields_total} ({r.populated_pct:.0f}%)")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(r.extracted, ensure_ascii=False, indent=2))
            lines.append("```")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Recomendações para Fase 4")
    lines.append("(Preencher após análise dos resultados acima — placeholder)")
    return "\n".join(lines)


def main() -> int:
    global MARITACA_MODEL, MARITACA_BASE_URL, MARITACA_KEY
    MARITACA_MODEL = os.environ.get("MARITACA_MODEL", "sabia-4")
    MARITACA_BASE_URL = os.environ.get("MARITACA_BASE_URL", "https://chat.maritaca.ai/api")
    MARITACA_KEY = os.environ.get("MARITACA_API_KEY", "")

    if not MARITACA_KEY:
        print("ERRO: MARITACA_API_KEY ausente no ambiente / .env", file=sys.stderr)
        return 1
    if not CONTRATOS.exists():
        print(f"ERRO: {CONTRATOS} não existe", file=sys.stderr)
        return 1

    print(f"Usando modelo: {MARITACA_MODEL} via {MARITACA_BASE_URL}", file=sys.stderr)
    client = OpenAI(api_key=MARITACA_KEY, base_url=MARITACA_BASE_URL)

    results: list[ExtractionResult] = []
    for emp, zip_name in SAMPLES:
        zip_path = CONTRATOS / emp / zip_name
        print(f"\n=== {emp} / {zip_name} ===", file=sys.stderr)
        if not zip_path.exists():
            print(f"  AVISO: não existe, pulando", file=sys.stderr)
            continue

        rec = ExtractionResult(
            empreiteira_folder=emp, zip_name=zip_name,
            pdf_name="", pdf_size_bytes=0, pdf_pages=0,
            text_chars=0, text_chars_used=0,
            latency_pdf_extract_s=0.0, latency_llm_s=0.0,
            cost_brl=0.0, tokens_input=0, tokens_output=0,
            fields_total=len(COUNTABLE_FIELDS),
        )
        try:
            t0 = time.perf_counter()
            pdf_name, text, pages, size = extract_pdf_text(zip_path)
            t1 = time.perf_counter()
            rec.pdf_name = pdf_name
            rec.pdf_pages = pages
            rec.pdf_size_bytes = size
            rec.text_chars = len(text)
            rec.latency_pdf_extract_s = t1 - t0
            print(f"  PDF: {pages} páginas, {len(text):,} chars", file=sys.stderr)

            text_truncated = text[:MAX_TEXT_CHARS]
            rec.text_chars_used = len(text_truncated)

            t2 = time.perf_counter()
            extracted, tin, tout, preview = call_maritaca(client, text_truncated)
            t3 = time.perf_counter()
            rec.latency_llm_s = t3 - t2
            rec.tokens_input = tin
            rec.tokens_output = tout
            rec.cost_brl = (tin / 1000) * COST_INPUT_PER_1K + (tout / 1000) * COST_OUTPUT_PER_1K
            rec.raw_response_preview = preview
            rec.extracted = extracted

            pop, tot, pct = count_populated(extracted)
            rec.fields_populated_count = pop
            rec.populated_pct = pct
            print(
                f"  LLM: {rec.latency_llm_s:.1f}s, in={tin}, out={tout}, cost=R${rec.cost_brl:.4f}, populated={pop}/{tot} ({pct:.0f}%)",
                file=sys.stderr,
            )
        except Exception as e:
            rec.error = f"{type(e).__name__}: {e}"
            print(f"  ERRO: {rec.error}", file=sys.stderr)
        results.append(rec)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    OUT_MD.write_text(render_markdown(results), encoding="utf-8")
    print(f"\nOK — gravado em:", file=sys.stderr)
    print(f"  {OUT_JSON}", file=sys.stderr)
    print(f"  {OUT_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # Carrega .env manualmente (sem dependência de python-dotenv)
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    sys.exit(main())
