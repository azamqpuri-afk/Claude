"""Owner dashboard: data builder, page and a small standard-library web server."""

import json
from pathlib import Path

from .data import build

PAGE = Path(__file__).with_name("dashboard.html")


def static_page(con, cfg, as_of=None):
    """The dashboard as one self-contained HTML file with the data embedded (chat needs the server)."""
    payload = json.dumps(build(con, cfg, as_of), default=str).replace("</", "<\\/")
    return PAGE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload, 1)
