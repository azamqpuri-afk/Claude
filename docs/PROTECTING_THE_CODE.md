# Protecting the product - for Azam Puri only (not shipped to customers)

How a mill's copy is kept from being read or copied, and how **you** still edit and troubleshoot freely.

## The three layers

| Layer | What it stops | Where it lives |
|---|---|---|
| **Licence** (done) | Running a copy on another server; editing the expiry/server | `smart_layer/licence.py`, your private key |
| **Compiled code** (Nuitka) | Reading your Python code; deleting the licence check in Notepad | the build - mills get `SmartLayer.exe`, no `.py` |
| **Sealed mappings** | Reading your ERP SQL (your know-how) | `.map` files + the mapping key baked into the exe |

Plus the signed **licence agreement** (LICENSE.txt → have a lawyer finalise it) for the legal side.

> No software that runs on someone else's computer is 100% uncopyable - they own the machine. These layers make a
> copy useless on another server and make the code and SQL unreadable, which is the normal, strong level for
> software sold to mills. For 100%, only the cloud app keeps the code on your server.

## One-time setup on your own computer

```
python -m pip install cryptography
python tools/issue_license.py init       # licence private key + licence_key.pub (commit the .pub)
python tools/issue_license.py mapkey     # mapping key -> SmartLayerKeys\mapping_key.txt (prints the key)
```

Back up **both** keys (USB + password manager). Commit `smart_layer/licence_key.pub` (safe). **Never** commit the
private key or the mapping key.

Then, once, on GitHub: repo **Settings → Secrets and variables → Actions → New repository secret**
→ name `SMARTLAYER_MAPPING_KEY`, value = the mapping key line. (This is the only secret the build needs; the
public key is already in the repo.)

## Building the installer

On the GitHub website: **Actions → "Build Windows installer" → Run workflow**, type the version (e.g. `0.5.0`),
Run. When it finishes, open that run and download **SmartLayer-Setup-<version>.exe** from *Artifacts*.
(Or push a tag: `git tag v0.5.0 && git push --tags`.)

GitHub builds it on a Windows machine for free: it compiles the code, seals the mappings with your secret, and
makes the installer. The repo stays private.

To build on your own Windows laptop instead: install Python, Nuitka and
[Inno Setup 6](https://jrsoftware.org/isdl.php), then run the steps in `.github/workflows/build-windows.yml`.

## At the mill

They run `SmartLayer-Setup-<version>.exe`: it installs to `C:\Program Files\Smart Layer`, asks the setup
questions (mill, database, dashboard, licence), shows the server ID, does the first sync, and schedules the daily
run and the dashboard. It appears in Windows **Settings → Apps**. Updates: send a new setup .exe, they run it, and
settings, data and licence are kept.

The ERP password they type is **sealed to that server** (Windows DPAPI): a copied `.env` is useless elsewhere.

## Editing and troubleshooting - still easy for you

Your readable code and the plain `mappings/*.toml` stay in the private repo and on your laptop. The mill only gets
the compiled exe and sealed `.map`.

- **Change the program:** edit here or on your laptop → push → build a new setup .exe → run it on the mill's server.
- **Change a mill's settings** (closing method, limits, dashboard password): edit `config.toml` in Notepad on their
  server, or Start Menu → *Smart Layer setup*. No rebuild.
- **Fix a mapping's SQL:** edit the plain `.toml` on your laptop → `set SMARTLAYER_MAPPING_KEY=...` →
  `python tools/pack_mappings.py` → copy the new `.map` to the mill's `mappings` folder. No full reinstall.
- **Diagnose on the server:** `SmartLayer.exe status`, `SmartLayer.exe check`, `SmartLayer.exe report ... --date ...`,
  and the logs in the `output` folder.

## What is still not perfect, and the answer

- **A full virtual-machine clone** copies every hardware fact, so one licence could run on the clone. Rare at mills;
  handled by **short licence periods** (3-12 months) and the agreement.
- **A determined reverse-engineer** could, with real effort, pull apart the exe. The agreement and short periods are
  the backstop. If you later sell widely, we can add extra hardening (e.g. PyArmor on top of Nuitka).
