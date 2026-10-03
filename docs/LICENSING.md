# Licences - for Azam Puri only (not shipped to customers)

Every mill's copy needs a licence: a short signed code tied to **one server** and an **expiry date**.
You sign licences with your **private key**; the product only carries the matching **public key**, so a
licence can be checked by anyone but made or changed only by you. Nothing is sent over the internet and
nothing costs money.

## Once, on your own computer

```
python -m pip install cryptography
python tools/issue_license.py init
```

- Creates your private key in `%USERPROFILE%\SmartLayerKeys\private_key.pem`.
  **Back it up** (USB + password manager). Lose it and you cannot issue or renew licences;
  leak it and anyone can make licences. Never put it in GitHub, email or WhatsApp.
- Writes `smart_layer/licence_key.pub` (the public key - safe to commit). Commit and push it, then build
  packages with `python tools/make_release.py` (it refuses to build without the public key).
- Run `init` only once. A new key makes every licence issued with the old one stop working in new packages.

## For each mill

1. During installation the installer shows **this server's ID**, e.g. `7F3A-91C2-44DE-0B19`
   (any time later: `python -m smart_layer machine-id`, or the dashboard's **Licence** button).
2. On your computer:
   ```
   python tools/issue_license.py issue --mill "Sitara Weaving" --machine 7F3A-91C2-44DE-0B19 --expires 2027-03-31
   ```
   It prints the licence code and saves `license_Sitara_Weaving_2027-03-31.key`.
3. Send the code or the file. The mill installs it in the dashboard (**Licence** → paste or choose the file →
   **Install licence**), or the installer asks for it, or `python -m smart_layer licence install <file or code>`.

`python tools/issue_license.py show <file or code>` shows what a licence contains.

## What the mill sees

| State | When | What happens |
|---|---|---|
| valid | before expiry | everything works |
| expiring | last 15 days | works; yellow banner on the dashboard; phone alert from the daily run |
| grace | up to 7 days after expiry | still works, with a warning |
| expired, missing, wrong server, changed | after that | sync, reports, audit, chat and dashboard figures stop. Their data stays; everything works again the moment a new licence is installed |

The bundled demo mill runs without a licence. `--machine "*"` makes a licence for any server (only for your own testing).

## Renewal, new server

- **Renewal:** issue a new licence for the same machine ID with the new date; they install it the same way
  (the old one is kept as `license.key.bak`).
- **The mill replaces its server:** they send the new server's ID; you issue a licence for it.
  The old server's licence stops at its expiry date - keep periods short (e.g. 3-12 months) so copies run out.

## What it protects, and what it does not

- Copying the folder to another computer does not work there: the licence names this server's ID and is signed.
  Editing the licence (a later date, another ID) breaks the signature.
- The program is Python: someone technical can read the code and the mapping SQL, and could remove the check.
  This stops ordinary copying and sharing between mills; together with the licence agreement (LICENSE.txt)
  it is the normal level for software sold to mills. Stronger protection (compiling the code so it cannot be
  read, e.g. with Nuitka or PyArmor) can be added before wider sale.
