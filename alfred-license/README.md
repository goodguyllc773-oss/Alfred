# alfred-license

Tiny access-code service for Alfred — a Cloudflare Worker backed by KV.
The app calls it to activate a code and to re-check on launch; the Administration
tab in Alfred calls the `/admin/*` routes to generate, list and revoke codes.

Nothing here is secret except `ADMIN_SECRET`, which is **not** in these files —
it's set as a Worker secret.

## One-time deploy (~10 min)

```bash
npm i -g wrangler
wrangler login                       # opens a browser, log into Cloudflare (free account is fine)

cd alfred-license

# 1. create the KV namespace, copy the id it prints
wrangler kv namespace create CODES
#   -> paste that id into wrangler.toml  (id = "…")

# 2. pick a long random admin key and set it as a secret
#    (this is what unlocks the Administration tab's server calls — keep it private)
wrangler secret put ADMIN_SECRET
#   -> paste a long random string when prompted, e.g. from:
#      node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))"

# 3. deploy
wrangler deploy
#   -> prints your URL, e.g. https://alfred-license.YOUR-SUBDOMAIN.workers.dev
```

Then in Alfred:

1. Open the app → the setup screen → **"I'm the app owner"**.
2. Paste the **Worker URL** and your **admin key** (`ADMIN_SECRET`).
3. Set your **master password** (this unlocks Administration on any machine you install on).
4. Administration tab → **Generate code** for each person. Each generated code is also
   appended to `Documents/Alfred/access-codes.csv` on your machine as a backup.

Give me the Worker URL and I'll bake it into the build so end users never see it —
they only ever type their access code.

## Routes

| Route | Body | Notes |
|---|---|---|
| `POST /activate` | `{code, deviceId, deviceName}` | Binds the code to the first device; returns `{ok, name, expiresAt}` |
| `POST /check` | `{code, deviceId}` | Launch re-check; `{ok:false, reason:"revoked"\|"expired"\|"device_mismatch"}` |
| `POST /admin/verify` | `{adminSecret}` | |
| `POST /admin/list` | `{adminSecret}` | all codes + status/usage |
| `POST /admin/generate` | `{adminSecret, name, expiresAt?}` | |
| `POST /admin/revoke` / `/admin/unrevoke` / `/admin/release` | `{adminSecret, code}` | release = unbind device |
