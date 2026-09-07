"use strict";
/* ---------------------------------------------------------------------------
   Alfred — Electron main process.

   Responsibilities:
   - open the window that loads index.html (the existing UI, unchanged in spirit)
   - Google OAuth 2.0 for Gmail using the loopback + PKCE flow for "Desktop app"
     clients: opens the system browser, catches the redirect on 127.0.0.1, trades
     the code for an access token + refresh token
   - store the refresh token encrypted at rest via Electron safeStorage (Windows
     DPAPI); the access token is kept in memory and silently refreshed
   - expose Gmail read/write over IPC. EVERY write (send / draft / modify / trash)
     pops a native confirm dialog first — the user chose "confirm everything that
     writes".
   Nothing secret is ever written into the repo: Google client id/secret live in
   the OS user-data dir (or an env var for dev), the refresh token is encrypted.
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
  win = new BrowserWindow({
    width: 1200, height: 820, minWidth: 900, minHeight: 600,
    backgroundColor: "#0b1322",
    title: "Alfred",
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

app.whenReady().then(createWindow);
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

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

/* ===================== IPC ===================== */
const guard = fn => async (...args) => {
  try { return { ok: true, data: await fn(...args) }; }
  catch (e) { return { ok: false, error: e.message, code: e.code || null }; }
};

ipcMain.handle("gmail:status", async () => {
  const creds = !!(readCreds() && readCreds().clientId);
  if (!loadRefreshToken()) return { creds, connected: false };
  try { return { creds, connected: true, email: await fetchProfileEmail() }; }
  catch (e) { return { creds, connected: false, expired: e.code === "TOKEN_EXPIRED", error: e.message }; }
});
ipcMain.handle("gmail:setCreds", (_e, c) => { writeCreds({ clientId: (c.clientId || "").trim(), clientSecret: (c.clientSecret || "").trim() }); return { ok: true }; });
ipcMain.handle("gmail:connect", guard(startAuth));
ipcMain.handle("gmail:disconnect", () => { clearTokens(); try { fs.unlinkSync(CREDS_FILE()); } catch {} return { ok: true }; });
ipcMain.handle("gmail:list", guard(a => listBrief(a || {})));
ipcMain.handle("gmail:get", guard(id => getFull(id)));
ipcMain.handle("gmail:send", guard(sendMail));
ipcMain.handle("gmail:draft", guard(createDraft));
ipcMain.handle("gmail:modify", guard(modify));
ipcMain.handle("gmail:trash", guard(trashMsg));
