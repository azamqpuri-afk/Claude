# Smart Layer Cloud (separate from the on-prem product)

A multi-mill web app. **The mill's raw ERP never leaves the mill** - a tiny agent at the mill reads the ERP
read-only (the ordinary sync) and pushes only the processed store + settings (no passwords) to the cloud over
HTTPS. The product code lives only on the cloud server, so a mill never has the code, and you turn each mill's
access on or off centrally.

This folder is self-contained and reuses `smart_layer` as a library. It is **not** part of the installer or the
customer package - it will be folded into the main set-up when you decide to offer the cloud plan.

```
Mill's Windows server                          Your cloud server
┌───────────────────────────┐                  ┌──────────────────────────────┐
│ ERP (Oracle, read-only)   │                  │ cloud_app.manage serve        │
│   │ smart_layer sync      │   HTTPS upload   │   registry: cloud.db          │
│   ▼ data/smart_layer.db   │ ───────────────► │   tenants/<mill>/store.db     │
│ cloud_app.agent (push)    │   (zip, token)   │   dashboard + reports + chat  │
└───────────────────────────┘                  └──────────────────────────────┘
        raw ERP stays here                       owner logs in at /<mill>/
```

## Why a mill can trust it
- Only the **processed store** is sent - the same numbers the dashboard already shows - never the live ERP, and
  never any password (`.env` is never uploaded). The agent connects to the ERP with the **read-only** user.
- Each mill's data sits in its own folder and is served only to someone with that mill's **view password**.
- You can offer a mill the on-prem product instead (nothing leaves their building) - same code, their choice.

## Run the cloud (on your server)
```
python -m cloud_app.manage add --name "Sitara Weaving" --url https://cloud.yourdomain.com
     # prints the mill id, the owner's view password, and the exact agent command (with the secret token)
python -m cloud_app.manage serve --host 127.0.0.1 --port 8090
python -m cloud_app.manage list
python -m cloud_app.manage disable --id sitara-weaving     # instantly blocks upload + viewing
```

Put a real HTTPS reverse proxy in front (so the public address is `https://`), for example **Caddy**:
```
cloud.yourdomain.com {
    reverse_proxy 127.0.0.1:8090
}
```
Caddy gets a free TLS certificate automatically. (Nginx + certbot works too.) Run `serve` under a service
manager (systemd) so it restarts on reboot. Set `ANTHROPIC_API_KEY` in the environment to switch on the chat.

## At the mill (the agent)
Install Python + the ERP driver, put `config.toml` + `.env` there (same as on-prem), then schedule:
```
python -m cloud_app.agent --url https://cloud.yourdomain.com --mill sitara-weaving --token <token>
```
It syncs the ERP locally and uploads. Run it on a schedule (e.g. every morning), like the on-prem daily run.

## What is MVP here, and what to add before selling the cloud plan
- **Done:** multi-mill isolation, token upload, per-mill login, dashboard + reports + chat, central enable/disable.
- **Add later:** an admin page for you (instead of the CLI), per-mill licence/billing dates, audit-log of logins,
  rate limiting, backups of `tenants/`, and e-mail of the view password. All straightforward on this base.
