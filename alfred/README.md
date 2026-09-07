# Alfred (desktop)

A personal "chief of staff" desk. The UI is `index.html` (also openable straight in a
browser as a static tool). Wrapped in Electron it gains **real Gmail access** for
Miles and Alfred.

## Run it

```bash
cd alfred
npm install
npm start
```

Build a Windows installer (`dist/Alfred-Setup-<version>.exe`):

```bash
npm run dist
```

## Connect Gmail (one-time)

### 1. Google Cloud project
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

### 2. In Alfred
1. **Settings → Gmail** → paste the Client ID + secret → **Save**.
2. **Connect Gmail** → the system browser opens → *"Google hasn't verified this
   app"* → **Advanced → Go to Alfred (unsafe)** → approve.
3. Done. The Inbox tab now shows your real mail; Miles briefs on it.

## How the sign-in is stored

- The **Client ID / secret** go in `%APPDATA%/Alfred/google-creds.json` (a Desktop-app
  client secret is not confidential per Google's docs, but it's still kept out of the
  repo). You can instead set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in
  `alfred/.env` (gitignored) for development.
- The **renewal (refresh) token** is encrypted with Electron `safeStorage` (Windows
  DPAPI) at `%APPDATA%/Alfred/gmail-refresh.bin`. The 1-hour access token lives only
  in memory and is refreshed silently.

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
