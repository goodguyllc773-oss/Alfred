"use strict";
/* ---------------------------------------------------------------------------
   Alfred — Electron main process.

   Responsibilities:
   - open the window that loads index.html (the existing UI, unchanged in spirit)
   - two ways to reach Gmail, whichever the user set up:
     * IMAP + SMTP with a Google App Password (simplest — no Cloud project). Active
       whenever imap-creds.bin exists.
     * Google OAuth 2.0, loopback + PKCE "Desktop app" flow (browser sign-in,
       redirect caught on 127.0.0.1, refresh token kept).
   - all credentials/tokens encrypted at rest via Electron safeStorage (Windows
     DPAPI). OAuth access token stays in memory and is refreshed silently.
   - expose Gmail read/write over IPC. EVERY write (send / draft / archive / trash)
     pops a native confirm dialog first — the user chose "confirm everything that
     writes".
   Nothing secret is ever written into the repo: Google client id/secret live in
   the OS user-data dir (or an env var for dev); tokens and the app password are
   encrypted.
--------------------------------------------------------------------------- */
const { app, BrowserWindow, ipcMain, shell, dialog, safeStorage } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const crypto = require("crypto");

// --- tiny .env loader (dev convenience; .env is gitignored) -----------------
(function loadDotEnv() {
  try {
    const p = path.join(__dirname, ".env");
    if (!fs.existsSync(p)) return;
    for (const line of fs.readFileSync(p, "utf8").split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch {}
})();

const SCOPE = "https://mail.google.com/";
const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_URL = "https://oauth2.googleapis.com/token";
const GBASE = "https://gmail.googleapis.com/gmail/v1/users/me";

const dataFile = name => path.join(app.getPath("userData"), name);
const CREDS_FILE = () => dataFile("google-creds.json");
const TOKEN_FILE = () => dataFile("gmail-refresh.bin");

let win = null;
let accessToken = null;
let accessTokenExp = 0;

/* ===================== window ===================== */
function createWindow() {
  const iconPath = path.join(__dirname, "assets", "alfred.png");
  win = new BrowserWindow({
    width: 1200, height: 820, minWidth: 900, minHeight: 600,
    backgroundColor: "#0b1322",
    title: "Alfred",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });
  win.removeMenu();
  win.loadFile("index.html");
  // open external links (Google sign-in help, tracking URLs) in the real browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
  });
}

app.whenReady().then(() => { createWindow(); setupUpdates(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

/* ===================== auto-update (electron-updater + GitHub Releases) =====
   The renderer shows a card on the Alfred home tab. On launch we check once and
   push status to it; the user clicks to download, and again to restart into the
   new version. Nothing installs without a click.                              */
let autoUpdater = null;
function pushUpdate(payload) {
  if (win && !win.isDestroyed()) win.webContents.send("update:status", { current: app.getVersion(), ...payload });
}
function setupUpdates() {
  try { autoUpdater = require("electron-updater").autoUpdater; }
  catch { pushUpdate({ state: "unsupported", reason: "updater not installed" }); return; }

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("checking-for-update", () => pushUpdate({ state: "checking" }));
  autoUpdater.on("update-available",     i => pushUpdate({ state: "available", version: i.version, notes: typeof i.releaseNotes === "string" ? i.releaseNotes : "" }));
  autoUpdater.on("update-not-available", () => pushUpdate({ state: "current" }));
  autoUpdater.on("download-progress",    p => pushUpdate({ state: "downloading", percent: Math.round(p.percent || 0) }));
  autoUpdater.on("update-downloaded",    i => pushUpdate({ state: "ready", version: i.version }));
  autoUpdater.on("error",                e => pushUpdate({ state: "error", error: String((e && e.message) || e).slice(0, 300) }));

  if (!app.isPackaged) { pushUpdate({ state: "dev" }); return; }
  // give the window a moment to attach its listener, then check
  setTimeout(() => { autoUpdater.checkForUpdates().catch(e => pushUpdate({ state: "error", error: String(e.message || e).slice(0, 300) })); }, 2500);
}

ipcMain.handle("update:check",   async () => { try { if (!autoUpdater) throw new Error("updater unavailable"); await autoUpdater.checkForUpdates(); return { ok: true }; } catch (e) { return { ok: false, error: e.message }; } });
ipcMain.handle("update:download", async () => { try { await autoUpdater.downloadUpdate(); return { ok: true }; } catch (e) { pushUpdate({ state: "error", error: e.message }); return { ok: false, error: e.message }; } });
ipcMain.handle("update:install", () => { try { setImmediate(() => autoUpdater.quitAndInstall(false, true)); return { ok: true }; } catch (e) { return { ok: false, error: e.message }; } });
ipcMain.handle("update:version", () => app.getVersion());
ipcMain.handle("update:openReleases", () => { shell.openExternal("https://github.com/goodguyllc773-oss/alfred-releases/releases/latest"); });

/* ===================== credentials + token storage ===================== */
function readCreds() {
  try {
    const c = JSON.parse(fs.readFileSync(CREDS_FILE(), "utf8"));
    if (c && c.clientId) return c;
  } catch {}
  if (process.env.GOOGLE_CLIENT_ID) {
    return { clientId: process.env.GOOGLE_CLIENT_ID, clientSecret: process.env.GOOGLE_CLIENT_SECRET || "" };
  }
  return null;
}
function writeCreds(c) { fs.writeFileSync(CREDS_FILE(), JSON.stringify(c), "utf8"); }

function saveRefreshToken(rt) {
  if (safeStorage.isEncryptionAvailable()) {
    fs.writeFileSync(TOKEN_FILE(), safeStorage.encryptString(rt));
  } else {
    // still local-only, but not encrypted — flag it with a prefix
    fs.writeFileSync(TOKEN_FILE(), Buffer.from("PLAIN:" + rt, "utf8"));
  }
}
function loadRefreshToken() {
  try {
    const buf = fs.readFileSync(TOKEN_FILE());
    if (buf.subarray(0, 6).toString("utf8") === "PLAIN:") return buf.toString("utf8").slice(6);
    return safeStorage.decryptString(buf);
  } catch { return null; }
}
function clearTokens() {
  try { fs.unlinkSync(TOKEN_FILE()); } catch {}
  accessToken = null; accessTokenExp = 0;
}

/* ===================== OAuth ===================== */
const b64url = buf => Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function startAuth() {
  const creds = readCreds();
  if (!creds || !creds.clientId) { const e = new Error("NO_CREDS"); e.code = "NO_CREDS"; throw e; }

  const verifier = b64url(crypto.randomBytes(32));
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const state = b64url(crypto.randomBytes(16));

  const { code, redirectUri } = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, "http://127.0.0.1");
      if (u.pathname !== "/") { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end('<!doctype html><meta charset="utf-8"><title>Alfred</title>' +
        '<body style="font:16px system-ui;background:#0b1322;color:#dbe6f5;display:grid;place-items:center;height:100vh;margin:0">' +
        '<div style="text-align:center"><h2 style="color:#57c6ff">Alfred is connected.</h2>' +
        '<p>You can close this tab and go back to the app.</p></div>');
      server.close();
      const err = u.searchParams.get("error");
      if (err) return reject(new Error("consent " + err));
      if (u.searchParams.get("state") !== state) return reject(new Error("state mismatch"));
      const c = u.searchParams.get("code");
      if (!c) return reject(new Error("no code returned"));
      resolve({ code: c, redirectUri: `http://127.0.0.1:${server.address().port}` });
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      const params = new URLSearchParams({
        client_id: creds.clientId,
        redirect_uri: `http://127.0.0.1:${port}`,
        response_type: "code",
        scope: SCOPE,
        code_challenge: challenge,
        code_challenge_method: "S256",
        access_type: "offline",
        prompt: "consent",
        state,
      });
      shell.openExternal(`${AUTH_URL}?${params.toString()}`);
    });
    setTimeout(() => { try { server.close(); } catch {} reject(new Error("timed out waiting for Google sign-in")); }, 300000);
  });

  const body = new URLSearchParams({
    client_id: creds.clientId,
    client_secret: creds.clientSecret || "",
    code, code_verifier: verifier,
    grant_type: "authorization_code",
    redirect_uri: redirectUri,
  });
  const r = await fetch(TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error("token exchange failed: " + (j.error_description || j.error || r.status));
  if (!j.refresh_token) throw new Error("Google did not return a renewal token. In the Google Cloud console set the OAuth consent screen to 'In production', then try Connect again.");
  saveRefreshToken(j.refresh_token);
  accessToken = j.access_token;
  accessTokenExp = Date.now() + (j.expires_in - 60) * 1000;
  return { email: await fetchProfileEmail() };
}

async function getAccessToken() {
  if (accessToken && Date.now() < accessTokenExp) return accessToken;
  const rt = loadRefreshToken();
  if (!rt) { const e = new Error("NOT_CONNECTED"); e.code = "NOT_CONNECTED"; throw e; }
  const creds = readCreds() || {};
  const body = new URLSearchParams({
    client_id: creds.clientId || "",
    client_secret: creds.clientSecret || "",
    refresh_token: rt,
    grant_type: "refresh_token",
  });
  const r = await fetch(TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (j.error === "invalid_grant") { clearTokens(); const e = new Error("TOKEN_EXPIRED"); e.code = "TOKEN_EXPIRED"; throw e; }
    throw new Error("could not refresh access: " + (j.error_description || j.error || r.status));
  }
  accessToken = j.access_token;
  accessTokenExp = Date.now() + (j.expires_in - 60) * 1000;
  return accessToken;
}

/* ===================== Gmail REST ===================== */
async function gapi(pathAndQuery, opts = {}) {
  const tok = await getAccessToken();
  const r = await fetch(GBASE + pathAndQuery, {
    method: opts.method || "GET",
    headers: { Authorization: "Bearer " + tok, "Content-Type": "application/json", ...(opts.headers || {}) },
    body: opts.body,
  });
  const txt = await r.text();
  const j = txt ? JSON.parse(txt) : {};
  if (!r.ok) throw new Error("Gmail API " + r.status + ": " + (j.error && j.error.message ? j.error.message : txt.slice(0, 200)));
  return j;
}

async function fetchProfileEmail() { return (await gapi("/profile")).emailAddress; }

const decodeB64 = d => Buffer.from(String(d).replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
function extractBody(payload) {
  if (!payload) return "";
  const walk = p => {
    if (p.mimeType === "text/plain" && p.body && p.body.data) return decodeB64(p.body.data);
    if (p.parts) for (const part of p.parts) { const got = walk(part); if (got) return got; }
    if (p.mimeType === "text/html" && p.body && p.body.data) return decodeB64(p.body.data).replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    return "";
  };
  return walk(payload).trim();
}

async function listBrief({ q = "in:inbox", max = 25 } = {}) {
  const list = await gapi(`/messages?maxResults=${max}&q=${encodeURIComponent(q)}`);
  const out = [];
  for (const { id } of (list.messages || [])) {
    const m = await gapi(`/messages/${id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`);
    const h = {};
    for (const x of (m.payload && m.payload.headers) || []) h[x.name.toLowerCase()] = x.value;
    out.push({
      id: m.id, threadId: m.threadId,
      from: h.from || "", subject: h.subject || "(no subject)", date: h.date || "",
      snippet: (m.snippet || "").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"),
      unread: (m.labelIds || []).includes("UNREAD"),
      labelIds: m.labelIds || [],
    });
  }
  return out;
}
async function getFull(id) {
  const m = await gapi(`/messages/${id}?format=full`);
  const h = {};
  for (const x of (m.payload && m.payload.headers) || []) h[x.name.toLowerCase()] = x.value;
  return { id: m.id, threadId: m.threadId, from: h.from, to: h.to, subject: h.subject, date: h.date, snippet: m.snippet, body: extractBody(m.payload) };
}

/* ---- writes: every one asks first ---- */
async function confirmWrite(message, detail) {
  const { response } = await dialog.showMessageBox(win, {
    type: "question", buttons: ["Cancel", "Yes, do it"], defaultId: 0, cancelId: 0,
    title: "Confirm — Alfred", message, detail: (detail || "").slice(0, 2000),
    noLink: true,
  });
  return response === 1;
}
const mime = ({ to, subject, body }) =>
  b64url(Buffer.from(`To: ${to}\r\nSubject: ${subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n${body}`, "utf8"));

async function sendMail(a) {
  if (!await confirmWrite("Send this email?", `To: ${a.to}\nSubject: ${a.subject}\n\n${a.body}`)) return { cancelled: true };
  const payload = a.threadId ? { raw: mime(a), threadId: a.threadId } : { raw: mime(a) };
  return gapi("/messages/send", { method: "POST", body: JSON.stringify(payload) });
}
async function createDraft(a) {
  if (!await confirmWrite("Save this as a draft?", `To: ${a.to}\nSubject: ${a.subject}\n\n${a.body}`)) return { cancelled: true };
  const message = a.threadId ? { raw: mime(a), threadId: a.threadId } : { raw: mime(a) };
  return gapi("/drafts", { method: "POST", body: JSON.stringify({ message }) });
}
async function modify(a) {
  const desc = a.describe || "Change labels on this email?";
  if (!await confirmWrite(desc, `Add: ${(a.add || []).join(", ") || "—"}\nRemove: ${(a.remove || []).join(", ") || "—"}`)) return { cancelled: true };
  return gapi(`/messages/${a.id}/modify`, { method: "POST", body: JSON.stringify({ addLabelIds: a.add || [], removeLabelIds: a.remove || [] }) });
}
async function trashMsg(a) {
  if (!await confirmWrite("Move this email to Trash?", a.subject ? `"${a.subject}"` : `Message ${a.id}`)) return { cancelled: true };
  return gapi(`/messages/${a.id}/trash`, { method: "POST" });
}

/* ===================== IMAP + SMTP (app-password path) =====================
   Browsers can't do IMAP, but the Electron main process (Node) can. When the
   user saves IMAP credentials this becomes the active path — read over IMAP,
   send over SMTP. Password is a Google App Password, encrypted at rest.        */
const IMAP_FILE = () => dataFile("imap-creds.bin");
let _ImapFlow = null, _simpleParser = null, _nodemailer = null;
function loadImapDeps() {
  if (_ImapFlow) return;
  try {
    _ImapFlow = require("imapflow").ImapFlow;
    _simpleParser = require("mailparser").simpleParser;
    _nodemailer = require("nodemailer");
  } catch (e) {
    const err = new Error("IMAP support isn't installed. Run `npm install` in the alfred folder.");
    err.code = "IMAP_DEPS"; throw err;
  }
}
function saveImap(c) {
  const s = JSON.stringify(c);
  fs.writeFileSync(IMAP_FILE(), safeStorage.isEncryptionAvailable()
    ? safeStorage.encryptString(s) : Buffer.from("PLAIN:" + s, "utf8"));
}
function loadImap() {
  try {
    const buf = fs.readFileSync(IMAP_FILE());
    const s = buf.subarray(0, 6).toString("utf8") === "PLAIN:" ? buf.toString("utf8").slice(6) : safeStorage.decryptString(buf);
    return JSON.parse(s);
  } catch { return null; }
}
const clearImap = () => { try { fs.unlinkSync(IMAP_FILE()); } catch {} };
const imapActive = () => !!loadImap();

async function imapConnect(raw) {
  loadImapDeps();
  const c = {
    user: (raw.user || "").trim(),
    pass: (raw.pass || "").replace(/\s+/g, ""), // app passwords are shown with spaces
    host: (raw.host || "imap.gmail.com").trim(),
    port: Number(raw.port) || 993,
    smtpHost: (raw.smtpHost || "smtp.gmail.com").trim(),
    smtpPort: Number(raw.smtpPort) || 465,
  };
  if (!c.user || !c.pass) { const e = new Error("Email and app password are both required."); e.code = "BAD_INPUT"; throw e; }
  const client = new _ImapFlow({ host: c.host, port: c.port, secure: true, auth: { user: c.user, pass: c.pass }, logger: false, emitLogs: false });
  try { await client.connect(); await client.logout(); }
  catch (e) {
    const err = new Error(/auth/i.test(e.message) || e.authenticationFailed
      ? "Gmail rejected that sign-in. Use a 16-character App Password (Google Account → Security → App passwords), not your normal password, and make sure IMAP is enabled in Gmail settings."
      : "Couldn't reach the mail server: " + e.message);
    err.code = "IMAP_AUTH"; throw err;
  }
  saveImap(c);
  return { email: c.user };
}

async function withImap(fn) {
  loadImapDeps();
  const c = loadImap();
  if (!c) { const e = new Error("NOT_CONNECTED"); e.code = "NOT_CONNECTED"; throw e; }
  const client = new _ImapFlow({ host: c.host, port: c.port, secure: true, auth: { user: c.user, pass: c.pass }, logger: false, emitLogs: false });
  await client.connect();
  try { return await fn(client, c); }
  finally { try { await client.logout(); } catch {} }
}
async function boxFor(client, useFlag, fallback) {
  try { for (const b of await client.list()) if (b.specialUse === useFlag) return b.path; } catch {}
  return fallback;
}
const addr = a => a && a[0] ? { name: a[0].name || "", address: a[0].address || "" } : { name: "", address: "" };

async function imapList({ mailbox = "INBOX", max = 25 } = {}) {
  return withImap(async client => {
    const lock = await client.getMailboxLock(mailbox);
    try {
      const total = client.mailbox.exists;
      if (!total) return [];
      const out = [];
      for await (const m of client.fetch(`${Math.max(1, total - max + 1)}:*`, { envelope: true, flags: true })) {
        const f = addr(m.envelope.from);
        out.push({
          id: String(m.uid), uid: m.uid,
          from: f.name || f.address, fromRaw: f.address,
          subject: m.envelope.subject || "(no subject)",
          date: m.envelope.date ? new Date(m.envelope.date).toISOString() : "",
          unread: !(m.flags && m.flags.has("\\Seen")),
        });
      }
      return out.reverse();
    } finally { lock.release(); }
  });
}
async function imapGet({ id, mailbox = "INBOX" }) {
  return withImap(async client => {
    const lock = await client.getMailboxLock(mailbox);
    try {
      const m = await client.fetchOne(String(id), { source: true }, { uid: true });
      const p = await _simpleParser(m.source);
      const body = (p.text || (p.html ? String(p.html).replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<[^>]+>/g, " ") : "")).replace(/\s+\n/g, "\n").trim();
      return { id, from: p.from && p.from.text, fromRaw: p.from && p.from.value && p.from.value[0] && p.from.value[0].address, to: p.to && p.to.text, subject: p.subject, date: p.date && p.date.toISOString(), body };
    } finally { lock.release(); }
  });
}
async function imapMoveTo(a, useFlag, fallback, verb) {
  if (!await confirmWrite(`${verb} "${a.subject || ("message " + a.id)}"?`, "")) return { cancelled: true };
  return withImap(async client => {
    const dest = await boxFor(client, useFlag, fallback);
    const lock = await client.getMailboxLock(a.mailbox || "INBOX");
    try { await client.messageMove(String(a.id), dest, { uid: true }); return { movedTo: dest }; }
    finally { lock.release(); }
  });
}
async function imapMarkRead(a) {
  return withImap(async client => {
    const lock = await client.getMailboxLock(a.mailbox || "INBOX");
    try { await client.messageFlagsAdd(String(a.id), ["\\Seen"], { uid: true }); return { ok: true }; }
    finally { lock.release(); }
  });
}
async function smtpSend(a) {
  if (!await confirmWrite("Send this email?", `To: ${a.to}\nSubject: ${a.subject}\n\n${a.body}`)) return { cancelled: true };
  const c = loadImap();
  const t = _nodemailer.createTransport({ host: c.smtpHost, port: c.smtpPort, secure: true, auth: { user: c.user, pass: c.pass } });
  const info = await t.sendMail({ from: c.user, to: a.to, subject: a.subject, text: a.body, inReplyTo: a.inReplyTo, references: a.references });
  return { id: info.messageId };
}
async function imapAppendDraft(a) {
  if (!await confirmWrite("Save this as a draft?", `To: ${a.to}\nSubject: ${a.subject}\n\n${a.body}`)) return { cancelled: true };
  return withImap(async client => {
    const box = await boxFor(client, "\\Drafts", "[Gmail]/Drafts");
    const raw = `From: ${loadImap().user}\r\nTo: ${a.to}\r\nSubject: ${a.subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n${a.body}`;
    await client.append(box, raw, ["\\Draft"]);
    return { box };
  });
}

/* ===================== unified IPC ===================== */
const guard = fn => async (...args) => {
  try { return { ok: true, data: await fn(...args) }; }
  catch (e) { return { ok: false, error: e.message, code: e.code || null }; }
};

ipcMain.handle("gmail:status", async () => {
  if (imapActive()) {
    const c = loadImap();
    return { method: "imap", connected: true, email: c.user };
  }
  const creds = !!(readCreds() && readCreds().clientId);
  if (!loadRefreshToken()) return { method: "oauth", creds, connected: false };
  try { return { method: "oauth", creds, connected: true, email: await fetchProfileEmail() }; }
  catch (e) { return { method: "oauth", creds, connected: false, expired: e.code === "TOKEN_EXPIRED", error: e.message }; }
});

// OAuth setup
ipcMain.handle("gmail:setCreds", (_e, c) => { writeCreds({ clientId: (c.clientId || "").trim(), clientSecret: (c.clientSecret || "").trim() }); return { ok: true }; });
ipcMain.handle("gmail:connect", guard(startAuth));
// IMAP setup
ipcMain.handle("gmail:imapConnect", guard(imapConnect));
// shared
ipcMain.handle("gmail:disconnect", () => {
  clearTokens(); clearImap();
  try { fs.unlinkSync(CREDS_FILE()); } catch {}
  return { ok: true };
});
ipcMain.handle("gmail:list",   guard(a => imapActive() ? imapList(a || {}) : listBrief(a || {})));
ipcMain.handle("gmail:get",    guard(id => imapActive() ? imapGet({ id }) : getFull(id)));
ipcMain.handle("gmail:send",   guard(a => imapActive() ? smtpSend(a) : sendMail(a)));
ipcMain.handle("gmail:draft",  guard(a => imapActive() ? imapAppendDraft(a) : createDraft(a)));
ipcMain.handle("gmail:modify", guard(a => {
  if (!imapActive()) return modify(a);
  // renderer sends {remove:["INBOX"]} for archive; anything else we treat as mark-read
  if ((a.remove || []).includes("INBOX")) return imapMoveTo(a, "\\All", "[Gmail]/All Mail", "Archive");
  return imapMarkRead(a);
}));
ipcMain.handle("gmail:trash",  guard(a => imapActive() ? imapMoveTo(a, "\\Trash", "[Gmail]/Trash", "Trash") : trashMsg(a)));
