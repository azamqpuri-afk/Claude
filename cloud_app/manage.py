"""
Run and manage the Smart Layer cloud (on Azam Puri's cloud server).

  python -m cloud_app.manage add --name "Sitara Weaving"     # register a mill: prints its id, upload token,
                                                             # view password and the exact agent command to send
  python -m cloud_app.manage list
  python -m cloud_app.manage disable --id sitara             # block upload + viewing at once
  python -m cloud_app.manage enable  --id sitara
  python -m cloud_app.manage reset-password --id sitara      # new dashboard (view) password
  python -m cloud_app.manage serve --host 0.0.0.0 --port 8090

Data lives in ./cloud.db (the registry) and ./tenants/<mill>/ (each mill's store + settings). Put a real HTTPS
reverse proxy (Caddy / Nginx) in front for the public address - see cloud_app/README.md.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloud_app.registry import Registry  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart Layer cloud management")
    p.add_argument("--db", default="cloud.db", help="the registry file")
    p.add_argument("--tenants", default="tenants", help="where each mill's data is kept")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--id")
    a.add_argument("--url", default="https://YOUR-CLOUD-ADDRESS", help="your cloud URL, for the printed agent command")
    sub.add_parser("list")
    for name in ("enable", "disable"):
        s = sub.add_parser(name)
        s.add_argument("--id", required=True)
    rp = sub.add_parser("reset-password")
    rp.add_argument("--id", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8090)
    args = p.parse_args(argv)
    reg = Registry(args.db)

    if args.cmd == "add":
        m = reg.add(args.name, args.id)
        print(f"Registered '{m['name']}' (id: {m['id']})")
        print(f"  View password (give to the mill's owner): {m['view_password']}")
        print(f"  Dashboard URL: {args.url.rstrip('/')}/{m['id']}/")
        print("  Agent command for the mill's server (keep the token secret):")
        print(f"    python -m cloud_app.agent --url {args.url} --mill {m['id']} --token {m['token']}")
        return 0
    if args.cmd == "list":
        for m in reg.all():
            print(f"  {m['id']:20} {m['name']:30} {'on ' if m['enabled'] else 'OFF'}  last upload: {m['last_upload'] or '-'}")
        return 0
    if args.cmd in ("enable", "disable"):
        if not reg.get(args.id):
            raise SystemExit(f"No mill with id '{args.id}'.")
        reg.set_enabled(args.id, args.cmd == "enable")
        print(f"{args.id}: {'enabled' if args.cmd == 'enable' else 'disabled'}")
        return 0
    if args.cmd == "reset-password":
        if not reg.get(args.id):
            raise SystemExit(f"No mill with id '{args.id}'.")
        print(f"New view password for {args.id}: {reg.reset_view_password(args.id)}")
        return 0
    if args.cmd == "serve":
        from cloud_app.server import serve
        serve(args.db, args.tenants, args.host, args.port)
        return 0


if __name__ == "__main__":
    sys.exit(main())
