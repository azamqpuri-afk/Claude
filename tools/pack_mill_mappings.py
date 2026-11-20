"""
Seal each mill's ERP mapping into its own versioned, deliverable package (item S).

The sold installer ships ONLY the demo mapping - a mill's real ERP SQL must never go to every customer. But each
mill still needs its own sealed mapping, matched to the app version, to drop into its install (SmartLayer.exe
install-mapping <file>). This writes, for every mappings/*.toml that is not the demo:

    dist/mill-mappings/<name>-v<version>-app<app_version>.map

sealed with SMARTLAYER_MAPPING_KEY, so the build can upload them as a SEPARATE artifact (not inside the installer).

    set SMARTLAYER_MAPPING_KEY=<the mapping key>
    python tools/pack_mill_mappings.py
"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smart_layer import secret  # noqa: E402

DEMO = {"demo_sqlite"}                                     # the only mapping allowed into the general build


def main():
    if not secret.have_mapping_key():
        raise SystemExit("Set SMARTLAYER_MAPPING_KEY first (python tools/issue_license.py mapkey shows it).")
    out_dir = ROOT / "dist" / "mill-mappings"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for toml in sorted((ROOT / "mappings").glob("*.toml")):
        if toml.stem in DEMO:
            continue
        text = toml.read_text(encoding="utf-8")
        meta = tomllib.loads(text).get("mapping", {})
        ver = str(meta.get("version", "0")).strip() or "0"
        app = str(meta.get("app_version", "0")).strip() or "0"
        out = out_dir / f"{toml.stem}-v{ver}-app{app}.map"
        out.write_bytes(secret.encrypt_mapping(text))
        assert secret.decrypt_mapping(out.read_bytes()) == text      # never ship an unopenable package
        print(f"sealed {toml.name} -> mill-mappings/{out.name}")
        n += 1
    print(f"{n} mill mapping(s) packaged in {out_dir}.")
    if not n:
        print("(no mill mappings found - only the demo is present)")


if __name__ == "__main__":
    main()
