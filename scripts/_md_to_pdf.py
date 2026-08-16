from pathlib import Path
import markdown
import subprocess

reports_dir = Path("/workspace/daily_stock_analysis/reports")
out_dir = reports_dir

sections = [
    ("大盘复盘", reports_dir / "market_review_20260816.md"),
    ("大盘 ETF（SPY / QQQ / IWM / SMH）", reports_dir / "report_20260816.md"),
    ("个股（Mag 7 + AI）", reports_dir / "report_20260816_mag7_ai.md"),
]

md_ext = markdown.Markdown(extensions=["tables", "fenced_code", "nl2br"])
parts = []
for title, path in sections:
    text = path.read_text(encoding="utf-8")
    html = md_ext.reset().convert(text)
    parts.append(f'<section class="block"><h1 class="part">{title}</h1>{html}</section>')

css = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Noto Sans CJK HK", "Noto Sans CJK SC", "Noto Sans", sans-serif;
  font-size: 11.5px;
  line-height: 1.55;
  color: #1a1a1a;
}
h1.part {
  font-size: 18px;
  margin: 0 0 12px;
  padding-bottom: 6px;
  border-bottom: 2px solid #111;
  page-break-before: always;
}
section.block:first-child h1.part { page-break-before: avoid; }
h1 { font-size: 16px; margin: 1.1em 0 0.4em; }
h2 { font-size: 14px; margin: 1em 0 0.35em; }
h3 { font-size: 12.5px; margin: 0.9em 0 0.3em; }
h4 { font-size: 12px; margin: 0.8em 0 0.25em; }
p, li { margin: 0.35em 0; }
blockquote {
  margin: 0.6em 0;
  padding: 6px 12px;
  background: #f4f6f8;
  border-left: 3px solid #3b82f6;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.6em 0 1em;
  font-size: 10.5px;
}
th, td {
  border: 1px solid #d0d5dd;
  padding: 4px 6px;
  text-align: left;
  vertical-align: top;
}
th { background: #eef2f6; font-weight: 600; }
tr { page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.2em 0; }
.cover { margin-bottom: 18px; }
.cover .meta { color: #555; font-size: 12px; }
code { font-family: "Noto Sans Mono CJK HK", ui-monospace, monospace; font-size: 10.5px; }
"""

doc = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>2026-08-16 美股分析</title>
<style>{css}</style>
</head>
<body>
<header class="cover">
  <h1 class="part" style="page-break-before:avoid">2026-08-16 美股分析</h1>
  <p class="meta">DeepSeek v4 flash · OpenRouter · 非交易日（美股周日，上一根完整日K为 2026-08-14）</p>
  <p class="meta">涵盖：大盘复盘 · SPY/QQQ/IWM/SMH · Mag 7 + CBRS/NOK/MU/LITE/SPCX（CBRS 模型空返回，未入个股看板）</p>
  <p class="meta">仅供参考，不构成投资建议。</p>
</header>
{''.join(parts)}
</body>
</html>
"""

html_path = out_dir / "report_20260816_us.html"
pdf_path = out_dir / "20260816_us_stock_analysis.pdf"
html_path.write_text(doc, encoding="utf-8")

cmd = [
    "google-chrome",
    "--headless=new",
    "--disable-gpu",
    "--no-pdf-header-footer",
    f"--print-to-pdf={pdf_path}",
    str(html_path.resolve()),
]
r = subprocess.run(cmd, capture_output=True, text=True)
print("chrome_rc", r.returncode)
print("stderr", (r.stderr or "")[-800:])
print("pdf_exists", pdf_path.exists(), "size", pdf_path.stat().st_size if pdf_path.exists() else 0)
