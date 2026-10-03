"""
Builds the customer package: dist/SmartLayer-<version>.zip - what you copy to a pilot mill's server.

    python tools/make_release.py

Left out on purpose: Dost Textiles' own scripts (legacy/), its settings (examples/) and its ERP mapping,
tests, local data, logs and anything with passwords.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smart_layer import __version__  # noqa: E402

INCLUDE = ["smart_layer", "mappings", "docs", "demo/build_demo_erp.py", "config.example.toml", "config.demo.toml",
           "requirements.txt", "README.md", "LICENSE.txt", "Install Smart Layer.bat", "install_windows.ps1",
           "uninstall_windows.ps1", "run_daily.ps1", "run_dashboard.ps1"]
EXCLUDE_NAMES = {"dost_textiles_oracle.toml", "FEATURE_COVERAGE.md", "__pycache__", ".env", "config.toml"}


def files():
    for item in INCLUDE:
        p = ROOT / item
        for f in ([p] if p.is_file() else sorted(p.rglob("*"))):
            if f.is_file() and not (set(f.relative_to(ROOT).parts) & EXCLUDE_NAMES) and f.suffix != ".pyc":
                yield f


def main():
    out = ROOT / "dist" / f"SmartLayer-{__version__}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            z.write(f, f"SmartLayer/{f.relative_to(ROOT).as_posix()}")
    print(f"Built {out} ({out.stat().st_size / 1024:,.0f} KB)")
    return out


if __name__ == "__main__":
    main()
