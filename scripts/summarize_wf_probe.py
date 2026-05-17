"""Resume probe_analitico_wf.json em formato denso (PT-BR, UTF-8 stdout)."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

p = Path(r"C:\_PERSONAL\prj_code\beholder\docs\probe_analitico_wf.json")
d = json.loads(p.read_text(encoding="utf-8"))
headers = d["headers"]
non_null = d["non_null_count_per_col"]
unique = d["unique_count_per_col"]
total_rows = d["declared_rows"]

print(f"Total declared rows: {total_rows:,}")
print()
print(f'{"#":>3} {"COL":<30} {"NON_NULL":>10} {"PCT":>5} {"UNIQUE":>10}')
for i, (h, nn, uq) in enumerate(zip(headers, non_null, unique)):
    pct = 100 * nn / total_rows if total_rows else 0
    print(f"{i:>3} {h[:30]:<30} {nn:>10,} {pct:>4.0f}% {str(uq):>10}")

print()
print("=== Colunas categoricas (n<=50 unicos) ===")
cat = d.get("categorical_preview", {})
for col, vals in cat.items():
    extra = "..." if len(vals) > 12 else ""
    print(f"{col} ({len(vals)} vals): {vals[:12]}{extra}")
