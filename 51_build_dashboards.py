"""
51_build_dashboards.py
Builds the four dashboard pages (A board report, B sales cockpit, C mill floor, D ledger desk)
from dashboards/*.html + dashboards/core.js with the latest JSON snapshot embedded, into
dashboards/out/. The published artifacts refresh themselves daily from the Drive JSON files
(business_dashboard_data.json, business_dashboard_ledgers.json) written by 40_business_dashboard.py,
so this only needs re-running when a page design changes.
"""
import configparser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "dashboards"
OUT = SRC / "out"
PAGES = {"A_board_report.html": False, "B_sales_cockpit.html": True, "C_mill_floor.html": False, "D_ledger_desk.html": True,
         "E_yarn_fabric.html": False}


def main():
    cfg = configparser.ConfigParser()
    cfg.read(HERE / "report_config.ini")
    data_dir = Path(cfg["output"]["folder"])
    main_json = (data_dir / "business_dashboard_data.json").read_text(encoding="utf-8").replace("</", "<\\/")
    led_json = (data_dir / "business_dashboard_ledgers.json").read_text(encoding="utf-8").replace("</", "<\\/")
    yf_json = (data_dir / "yarn_fabric_data.json").read_text(encoding="utf-8").replace("</", "<\\/")
    yf_id = cfg.get("dashboard", "yarn_fabric_file_id", fallback="__YF_FILE_ID__")
    core = (SRC / "core.js").read_text(encoding="utf-8")
    core = (core.replace("__DRIVE_FILE_ID__", cfg.get("dashboard", "drive_file_id"))
                .replace("__LEDGER_FILE_ID__", cfg.get("dashboard", "ledger_file_id")))
    OUT.mkdir(exist_ok=True)
    for page, needs_ledger in PAGES.items():
        c = core.replace("/*__DASHBOARD_DATA__*/null", "null" if page.startswith("E_") else main_json)
        c = c.replace("/*__LEDGER_DATA__*/null", led_json if needs_ledger else "null")
        html = ((SRC / page).read_text(encoding="utf-8").replace("/*__CORE__*/", c)
                .replace("/*__YF_DATA__*/null", yf_json).replace("__YF_FILE_ID__", yf_id))
        (OUT / page).write_text(html, encoding="utf-8")
        print(page, len(html))


if __name__ == "__main__":
    main()
