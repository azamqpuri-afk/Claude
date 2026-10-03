"""
Licence tool for Azam Puri - run on YOUR computer only. Never give the private key to anyone.

  python tools/issue_license.py init
      Once: creates your private key (default %USERPROFILE%\\SmartLayerKeys\\private_key.pem) and writes the
      matching public key into smart_layer/licence_key.pub, which goes into every package you build.
      Back up the private key safely (USB / password manager): without it you cannot issue or renew licences.

  python tools/issue_license.py issue --mill "Sitara Weaving" --machine 7F3A-91C2-44DE-0B19 --expires 2027-03-31
      Creates a licence for one mill's server. Prints the licence code (to paste in the dashboard) and saves
      license_<mill>_<expiry>.key (to send as a file). --machine "*" = any server (e.g. for your own testing).

  python tools/issue_license.py show <licence file or code>
      Shows what a licence contains and whether it is valid.
"""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smart_layer import licence  # noqa: E402

DEFAULT_KEY = Path(os.environ.get("USERPROFILE", Path.home())) / "SmartLayerKeys" / "private_key.pem"


def load_private(path):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Private key not found at {p} - run  python tools/issue_license.py init  first "
                         f"(or give --key <path> to your backed-up key).")
    return load_pem_private_key(p.read_bytes(), password=None)


def cmd_init(args):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_path = Path(args.key)
    if key_path.exists() and not args.force:
        print(f"A private key already exists at {key_path} - keeping it (use --force only if you really want a new one;")
        print("licences made with the old key would stop working in packages built with the new public key).")
        priv = load_private(key_path)
    else:
        priv = Ed25519PrivateKey.generate()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
        print(f"Private key created: {key_path}")
        print("  BACK IT UP NOW (USB / password manager) and never share it or put it in GitHub.")
    pub = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    licence.PUBLIC_KEY_FILE.write_bytes(pub)
    print(f"Public key written to {licence.PUBLIC_KEY_FILE.relative_to(ROOT)} - rebuild the package (tools/make_release.py).")


def cmd_issue(args):
    priv = load_private(args.key)
    try:
        date.fromisoformat(args.expires)
    except ValueError:
        raise SystemExit("--expires must be a date like 2027-03-31")
    machine = args.machine.strip().upper() if args.machine != "*" else "*"
    if machine != "*" and not re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", machine):
        raise SystemExit("--machine should look like 7F3A-91C2-44DE-0B19 (from: python -m smart_layer machine-id)")
    code = licence.new_licence(args.mill.strip(), machine, args.expires, priv)
    out = Path(args.out or f"license_{re.sub(r'[^A-Za-z0-9]+', '_', args.mill).strip('_')}_{args.expires}.key")
    out.write_text(code + "\n", encoding="ascii")
    print(f"Licence for {args.mill} (server {machine}) until {args.expires}")
    print(f"  file : {out.resolve()}   (send this file, or the code below)")
    print(f"  code : {code}")


def cmd_mapkey(args):
    """The key that seals the mapping SQL in the sold package. Keep it with the private key and back it up."""
    from cryptography.fernet import Fernet
    path = Path(args.mapkey_file)
    if path.exists() and not args.force:
        print(f"Mapping key already exists: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(Fernet.generate_key().decode("ascii") + "\n", encoding="ascii")
        print(f"Mapping key created: {path}  - BACK IT UP with your private key, never share it.")
    print("For a local protected build:  set SMARTLAYER_MAPPING_KEY to the line in that file.")
    print("For GitHub builds: add it as the repository secret SMARTLAYER_MAPPING_KEY.")
    print("  key:", path.read_text(encoding="ascii").strip())
    return 0


def cmd_show(args):
    text = Path(args.licence).read_text(encoding="ascii") if Path(args.licence).exists() else args.licence
    parts = text.strip().split(".")
    try:
        import json
        print(json.dumps(json.loads(licence._unb64(parts[1])), indent=2))
    except Exception:
        print("Not a licence code.")
        return 1
    st = licence.check_text(text, machine=parts and json.loads(licence._unb64(parts[1])).get("machine"))
    print(f"Signature / dates: {st.state} - {st.message}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart Layer licence tool (issuer only)")
    p.add_argument("--key", default=str(DEFAULT_KEY), help="your private key file")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--force", action="store_true")
    s = sub.add_parser("issue")
    s.add_argument("--mill", required=True)
    s.add_argument("--machine", required=True, help='server ID from the mill, or "*" for any server')
    s.add_argument("--expires", required=True, help="YYYY-MM-DD")
    s.add_argument("--out")
    mk = sub.add_parser("mapkey")
    mk.add_argument("--mapkey-file", default=str(DEFAULT_KEY.with_name("mapping_key.txt")))
    mk.add_argument("--force", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("licence")
    a = p.parse_args(argv)
    return {"init": cmd_init, "issue": cmd_issue, "mapkey": cmd_mapkey, "show": cmd_show}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
