"""
47_report_md_to_pdf.py
Render a Markdown report to PDF: Markdown -> styled HTML -> Microsoft Edge
headless print-to-pdf. Usage: python 47_report_md_to_pdf.py <report.md> <out.pdf>
"""

import subprocess
import sys
from pathlib import Path

import markdown

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; line-height: 1.45; color: #1a1d21; }
h1 { font-size: 20pt; color: #1F3B57; margin: 0 0 6pt; }
h2 { font-size: 14pt; color: #1F3B57; border-bottom: 1.5pt solid #1F3B57; padding-bottom: 2pt; margin-top: 16pt; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #1F3B57; margin-top: 12pt; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 8.5pt; page-break-inside: auto; }
th { background: #1F3B57; color: #fff; text-align: left; padding: 3pt 5pt; }
td { border-bottom: 0.5pt solid #c9d1d9; padding: 3pt 5pt; vertical-align: top; }
tr { page-break-inside: avoid; }
code { font-family: Consolas, monospace; font-size: 8.5pt; background: #eef1f4; padding: 0 2pt; }
blockquote { border-left: 3pt solid #9aa7b4; margin: 6pt 0; padding: 2pt 8pt; color: #4f524e; }
a { color: #1c5cab; text-decoration: none; }
"""


def main(src, out):
    text = Path(src).read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>{Path(src).stem}</title><style>{CSS}</style></head><body>{body}</body></html>"
    tmp = Path(out).with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    edge = next(p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists())
    subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={Path(out).resolve()}", tmp.resolve().as_uri()], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    tmp.unlink()
    print("written", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
