# Alfred (desktop)

A personal "chief of staff" desk. The UI is `index.html` — it runs three ways:

| How | Gmail |
|---|---|
| Double-click `index.html` in a browser | none (add emails by hand) |
| Serve it (`python -m http.server`) and open `http://localhost:8000/alfred/` | **read-only** via Google sign-in (Settings → Gmail) |
| The Electron app (below) | **full** read + send + file + delete (IMAP app-password or OAuth) |

## Run it

```bash
cd alfred
npm install
npm start          # dev run
npm run pack       # portable build -> dist/win-unpacked/Alfred.exe  (no installer, always works)
npm run dist       # NSIS installer -> dist/Alfred-Setup-<version>.exe
```

`npm run dist` needs Windows **Developer Mode** on (Settings → Privacy & security → For
developers) or an elevated shell — electron-builder's signing tools unpack symlinks.
Without that, use `npm run pack`: the `dist/win-unpacked/` folder is the whole app —
move it anywhere and pin `Alfred.exe` to the taskbar.

## Connect Gmail

Two ways. Pick one — the app uses whichever you set up (IMAP wins if both exist).

### Option A — App Password (simplest, no Google Cloud project)

1. Google account → **Security** → turn on **2-Step Verification** (required for app
   passwords).
2. https://myaccount.google.com/apppasswords → create one named "Alfred" → copy the
   16-character code.
3. Gmail on the web → **Settings → See all settings → Forwarding and POP/IMAP** →
   **Enable IMAP** → Save.
4. In Alfred: **Settings → Gmail** → enter your address + the App Password →
   **Link Gmail**. Miles and Alfred can read the inbox the moment it links.

Read happens over IMAP (`imap.gmail.com:993`), sending over SMTP
(`smtp.gmail.com:465`), both with the app password. The password is encrypted at rest
(`safeStorage`) in `%APPDATA%/Alfred/imap-creds.bin`.

### Option B — Google sign-in (OAuth)

Under **Settings → Gmail → "Advanced: Google sign-in"**. More setup, but no app
password and it uses a proper scoped token.

#### 1. Google Cloud project
1. https://console.cloud.google.com → create a project ("Alfred").
2. **APIs & Services → Library** → **Gmail API** → **Enable**.
3. **APIs & Services → OAuth consent screen**
   - User type **External** → Create.
   - Fill app name + your email in the contact fields.
   - **Scopes** → Add → `https://mail.google.com/` → Update.
   - **Test users** → add your Gmail address.
   - **Publish app** → set to **In production**. It stays *unverified* (fine for
     personal use) but this stops the sign-in from expiring every 7 days.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type **Desktop app**.
   - Copy the **Client ID** and **Client secret**.

#### 2. In Alfred
1. **Settings → Gmail → Advanced** → paste the Client ID + secret → **Save**.
2. **Connect via Google** → the system browser opens → *"Google hasn't verified this
   app"* → **Advanced → Go to Alfred (unsafe)** → approve.
3. Done. The Inbox tab now shows your real mail; Miles briefs on it.

## How the sign-in is stored

- **IMAP:** address + app password (+ host/port) encrypted with Electron `safeStorage`
  (Windows DPAPI) at `%APPDATA%/Alfred/imap-creds.bin`.
- **OAuth Client ID / secret:** `%APPDATA%/Alfred/google-creds.json` (a Desktop-app
  client secret is not confidential per Google's docs, but it's still kept out of the
  repo), or `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `alfred/.env` (gitignored).
- **OAuth refresh token:** encrypted at `%APPDATA%/Alfred/gmail-refresh.bin`. The
  1-hour access token stays in memory and is refreshed silently.

Nothing above is committed. `Disconnect Gmail` in Settings deletes whichever files
apply.

## Safety

- **Every write asks first.** Send, draft, archive/label, and trash each pop a native
  confirmation dialog from the main process before anything happens.
- **Email text → AI is per-action.** Sending an email body to the Anthropic API
  prompts each time, unless you tick the "without asking" box in Settings → Gmail.
  Senders and subject lines are not gated.
- Instructions found *inside* an email are data, not commands — Miles/Alfred act on
  your requests, not on text in a message.

## Files

| File | Role |
|---|---|
| `index.html` | the whole UI + logic (one IIFE). Detects `window.alfredNative`. |
| `main.js` | Electron main: window, OAuth loopback+PKCE, token storage, Gmail IPC, write confirms. |
| `preload.js` | `contextBridge` → `window.alfredNative.gmail.*`. |
| `package.json` | scripts + `electron-builder` config. |
