"""
Seal the mapping SQL for the sold package.

For every mappings/*.toml it writes mappings/*.map (encrypted with SMARTLAYER_MAPPING_KEY). The built product
reads the .map; the plain .toml is NOT shipped in the protected package, so a mill never sees the ERP SQL.

    set SMARTLAYER_MAPPING_KEY=<the line from SmartLayerKeys\\mapping_key.txt>
    python tools/pack_mappings.py

On your own computer, with no key, nothing is sealed and the plain .toml mappings are used as before.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smart_layer import secret  # noqa: E402


def main():
    if not secret.have_mapping_key():
        raise SystemExit("Set SMARTLAYER_MAPPING_KEY first (python tools/issue_license.py mapkey shows it).")
    maps = sorted((ROOT / "mappings").glob("*.toml"))
    for toml in maps:
        out = toml.with_suffix(".map")
        out.write_bytes(secret.encrypt_mapping(toml.read_text(encoding="utf-8")))
        # round-trip check so a bad key never ships
        assert secret.decrypt_mapping(out.read_bytes()) == toml.read_text(encoding="utf-8")
        print(f"sealed {toml.name} -> {out.name}")
    print(f"{len(maps)} mapping(s) sealed.")


if __name__ == "__main__":
    main()
