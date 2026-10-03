"""Open every tab of every dashboard in headless Edge and report script errors and empty tables."""
import re
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
TMP = OUT.parent / "_tabtest"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CATCH = ('<script>window.__errs=[];window.addEventListener("error",e=>{window.__errs.push(e.message)});'
         'window.addEventListener("load",()=>setTimeout(()=>{const d=document.createElement("pre");d.id="__result";'
         'd.textContent="ERRS="+JSON.stringify(window.__errs)+" TABLEROWS="+[...document.querySelectorAll("section:not([hidden]) table tbody tr, .wrap table tbody tr, .desk table tbody tr, .app table tbody tr")].length'
         '+" CANVAS="+document.querySelectorAll("canvas").length;document.body.appendChild(d)},2500));</script>')
VARIANTS = {
    "A_board_report.html": [None],
    "B_sales_cockpit.html": [("b-tab", "sales", t) for t in ["sales", "ledgers", "arap", "cash", "looms", "expenses", "profit"]],
    "C_mill_floor.html": [None],
    "D_ledger_desk.html": [None],
    "E_yarn_fabric.html": [("e-tab", "summary", t) for t in ["summary", "yarn", "beams", "fabric", "contracts", "registers"]],
}
TMP.mkdir(exist_ok=True)
for page, vs in VARIANTS.items():
    src = (OUT / page).read_text(encoding="utf-8")
    for v in vs:
        html = CATCH + src
        name = page
        if v:
            key, default, tab = v
            html = html.replace(f'store.get("{key}", "{default}")', f'store.get("{key}", "{tab}")')
            name = f"{page[:-5]}__{tab}.html"
        f = TMP / name
        f.write_text(html, encoding="utf-8")
        r = subprocess.run([EDGE, "--headless", "--disable-gpu", "--virtual-time-budget=12000", "--dump-dom", f.as_uri()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        m = re.search(r'<pre id="__result">(.*?)</pre>', r.stdout)
        print(f"{name:40} {m.group(1) if m else 'NO RESULT (page did not finish)'}")
