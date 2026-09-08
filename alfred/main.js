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
const { spawn } = require("child_process");

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

app.whenReady().then(() => { createWindow(); setupUpdates(); ensureOllamaRunning().catch(() => {}); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

/* ===================== Alfred's local brain (Ollama) =======================
   Alfred thinks on this machine — no API key, no account, offline-capable.
   Ollama runs a local server at 127.0.0.1:11434. First-time setup downloads
   the engine (if missing) and the model, streaming progress to the renderer.
========================================================================== */
const OLLAMA = "http://127.0.0.1:11434";
const BRAIN_MODEL = "qwen2.5:3b";           // Apache-2.0, ~1.9 GB, good on modest hardware
let ollamaProc = null;

function ollamaExe() {
  const cands = [
    path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama", "ollama.exe"),
    path.join(process.env["ProgramFiles"] || "", "Ollama", "ollama.exe"),
    path.join(process.env["ProgramFiles(x86)"] || "", "Ollama", "ollama.exe"),
  ];
  for (const c of cands) { try { if (c && fs.existsSync(c)) return c; } catch {} }
  return null;
}
async function ollamaUp() {
  try { const r = await fetch(OLLAMA + "/api/version", { signal: AbortSignal.timeout(2000) }); return r.ok; }
  catch { return false; }
}
async function ensureOllamaRunning() {
  if (await ollamaUp()) return true;
  const exe = ollamaExe();
  if (!exe) return false;
  try { ollamaProc = spawn(exe, ["serve"], { detached: true, stdio: "ignore", windowsHide: true }); ollamaProc.unref(); }
  catch { return false; }
  for (let i = 0; i < 40; i++) { if (await ollamaUp()) return true; await new Promise(r => setTimeout(r, 500)); }
  return false;
}
async function ollamaHasModel(name = BRAIN_MODEL) {
  try {
    const r = await fetch(OLLAMA + "/api/tags");
    if (!r.ok) return false;
    const j = await r.json();
    const stem = name.split(":")[0];
    return (j.models || []).some(m => m.name === name || m.name === stem || m.name.startsWith(stem + ":"));
  } catch { return false; }
}
async function brainStatus() {
  const engineInstalled = !!ollamaExe();
  const running = await ollamaUp();
  const hasModel = running ? await ollamaHasModel() : false;
  return { engineInstalled, running, hasModel, model: BRAIN_MODEL, ready: running && hasModel };
}
function brainProgress(p) { if (win && !win.isDestroyed()) win.webContents.send("brain:progress", p); }

async function downloadFile(url, dest, phase) {
  const r = await fetch(url, { redirect: "follow" });
  if (!r.ok || !r.body) throw new Error("download failed (" + r.status + ")");
  const total = Number(r.headers.get("content-length")) || 0;
  const ws = fs.createWriteStream(dest);
  const reader = r.body.getReader();
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    ws.write(Buffer.from(value)); got += value.length;
    if (total) brainProgress({ phase, state: "downloading", pct: Math.round(got / total * 100) });
  }
  ws.end();
  await new Promise((res, rej) => { ws.on("close", res); ws.on("error", rej); });
}

async function brainSetup() {
  try {
    let exe = ollamaExe();
    if (!exe) {
      const proceed = (await dialog.showMessageBox(win, {
        type: "question", buttons: ["Cancel", "Set up"], defaultId: 1, cancelId: 0, noLink: true,
        title: "Set up Alfred's brain",
        message: "Give Alfred a brain that runs on this computer — no API key, no account, works offline.",
        detail: "This installs the local AI engine (Ollama, ~1 GB from ollama.com) and downloads Alfred's model (~1.9 GB). One time. It'll take a few minutes on a normal connection.",
      })).response === 1;
      if (!proceed) return { ok: false, error: "cancelled" };
      brainProgress({ phase: "engine", state: "downloading", pct: 0 });
      const installer = path.join(app.getPath("temp"), "OllamaSetup.exe");
      await downloadFile("https://ollama.com/download/OllamaSetup.exe", installer, "engine");
      brainProgress({ phase: "engine", state: "installing" });
      await new Promise((res, rej) => {
        const p = spawn(installer, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], { windowsHide: true });
        p.on("exit", code => code === 0 ? res() : rej(new Error("the Ollama installer stopped (code " + code + "). Try running it yourself from ollama.com.")));
        p.on("error", rej);
      });
      for (let i = 0; i < 20 && !exe; i++) { await new Promise(r => setTimeout(r, 500)); exe = ollamaExe(); }
      if (!exe) throw new Error("Ollama installed but Alfred can't find it — restart Alfred and try again.");
    }
    brainProgress({ phase: "engine", state: "starting" });
    if (!await ensureOllamaRunning()) throw new Error("Ollama is installed but wouldn't start. Restart the computer and try again.");
    if (!await ollamaHasModel()) {
      brainProgress({ phase: "model", state: "downloading", pct: 0, note: "starting" });
      const r = await fetch(OLLAMA + "/api/pull", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: BRAIN_MODEL, stream: true }),
      });
      if (!r.ok || !r.body) throw new Error("couldn't start the model download (" + r.status + ")");
      const reader = r.body.getReader();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += Buffer.from(value).toString("utf8");
        const lines = buf.split("\n"); buf = lines.pop();
        for (const ln of lines) {
          if (!ln.trim()) continue;
          let j; try { j = JSON.parse(ln); } catch { continue; }
          if (j.error) throw new Error(j.error);
          if (j.total && j.completed) brainProgress({ phase: "model", state: "downloading", pct: Math.round(j.completed / j.total * 100), note: j.status });
          else if (j.status) brainProgress({ phase: "model", state: "downloading", note: j.status });
        }
      }
    }
    brainProgress({ phase: "done", state: "ready" });
    return { ok: true, status: await brainStatus() };
  } catch (e) {
    brainProgress({ phase: "error", state: "error", error: String(e.message || e) });
    return { ok: false, error: String(e.message || e) };
  }
}

async function brainChat({ system, messages }) {
  if (!await ensureOllamaRunning()) return { ok: false, error: "NOT_READY" };
  if (!await ollamaHasModel()) return { ok: false, error: "NOT_READY" };
  const msgs = [];
  if (system) msgs.push({ role: "system", content: String(system) });
  for (const m of messages || []) msgs.push({ role: m.role === "assistant" ? "assistant" : "user", content: String(m.content) });
  try {
    const r = await fetch(OLLAMA + "/api/chat", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: BRAIN_MODEL, messages: msgs, stream: false, keep_alive: "30m", options: { temperature: 0.5, num_ctx: 8192 } }),
      signal: AbortSignal.timeout(180000),
    });
    if (!r.ok) return { ok: false, error: "brain returned " + r.status };
    const j = await r.json();
    return { ok: true, text: ((j.message && j.message.content) || "").trim() };
  } catch (e) {
    return { ok: false, error: String(e.name === "TimeoutError" ? "the brain took too long — the model may still be loading, try again" : (e.message || e)) };
  }
}

ipcMain.handle("brain:status", () => brainStatus());
ipcMain.handle("brain:setup",  () => brainSetup());
ipcMain.handle("brain:chat",   (_e, a) => brainChat(a || {}));

/* ===================== license / access codes ============================
   The app is locked until a code is activated against the license Worker.
   Admin (the owner) enters an admin key instead and gets full access.
   Everything is stored encrypted in userData so it survives updates.
   `check` on every launch only LOCKS on an explicit revoked/expired.
========================================================================== */
const LICENSE_SERVER = "https://alfred-license.goodguyllc773.workers.dev";

const LICENSE_FILE = () => dataFile("license.bin");
const DEVICE_FILE = () => dataFile("device.json");
const CONFIG_FILE = () => dataFile("config.bin");

function encWrite(file, obj) {
  const s = JSON.stringify(obj);
  fs.writeFileSync(file, safeStorage.isEncryptionAvailable() ? safeStorage.encryptString(s) : Buffer.from("PLAIN:" + s, "utf8"));
}
function encRead(file) {
  try {
    const buf = fs.readFileSync(file);
    const s = buf.subarray(0, 6).toString("utf8") === "PLAIN:" ? buf.toString("utf8").slice(6) : safeStorage.decryptString(buf);
    return JSON.parse(s);
  } catch { return null; }
}

function deviceInfo() {
  let d = null;
  try { d = JSON.parse(fs.readFileSync(DEVICE_FILE(), "utf8")); } catch {}
  if (!d || !d.id) {
    d = { id: crypto.randomUUID(), name: require("os").hostname() };
    try { fs.writeFileSync(DEVICE_FILE(), JSON.stringify(d)); } catch {}
  }
  return d;
}
const readLicense = () => encRead(LICENSE_FILE()) || {};
const writeLicense = obj => encWrite(LICENSE_FILE(), obj);
function serverUrl() { return (readLicense().serverUrl || LICENSE_SERVER || "").replace(/\/+$/, ""); }

async function post(pathname, body) {
  const base = serverUrl();
  if (!base) return { ok: false, reason: "no_server" };
  try {
    const r = await fetch(base + pathname, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
    });
    const j = await r.json().catch(() => ({}));
    return j && typeof j === "object" ? j : { ok: false, reason: "bad_response" };
  } catch (e) {
    return { ok: false, reason: "unreachable", detail: String(e.message || e) };
  }
}

function codesBackupFile() {
  const dir = path.join(app.getPath("documents"), "Alfred");
  try { fs.mkdirSync(dir, { recursive: true }); } catch {}
  return path.join(dir, "access-codes.csv");
}
function appendCodeBackup(rec) {
  const f = codesBackupFile();
  const header = "code,name,created,expires\n";
  const line = [rec.code, (rec.name || "").replace(/[",\n]/g, " "), rec.createdAt || new Date().toISOString(), rec.expiresAt || ""].join(",") + "\n";
  try {
    if (!fs.existsSync(f)) fs.writeFileSync(f, header);
    fs.appendFileSync(f, line);
  } catch {}
  return f;
}

ipcMain.handle("license:status", async () => {
  const lic = readLicense();
  const dev = deviceInfo();
  const base = serverUrl();
  const out = {
    activated: !!lic.activated, isAdmin: !!lic.isAdmin,
    code: lic.code || null, name: lic.name || "", expiresAt: lic.expiresAt || null,
    hasMaster: !!lic.masterHash, serverConfigured: !!base, deviceName: dev.name,
  };
  // launch re-check — only LOCK on an explicit revoked/expired
  if (lic.activated && !lic.isAdmin && lic.code && base) {
    const r = await post("/check", { code: lic.code, deviceId: dev.id });
    if (r && r.ok === false && (r.reason === "revoked" || r.reason === "expired" || r.reason === "device_mismatch")) {
      writeLicense({ ...lic, activated: false, lockedReason: r.reason });
      out.activated = false; out.lockedReason = r.reason;
    } else if (r && r.ok) {
      out.name = r.name || out.name;
    }
  }
  return out;
});

ipcMain.handle("license:activate", async (_e, { serverUrl: su, code }) => {
  const lic = readLicense();
  if (su) lic.serverUrl = String(su).replace(/\/+$/, "");
  const dev = deviceInfo();
  const r = await post("/activate", { code: String(code || "").trim().toUpperCase(), deviceId: dev.id, deviceName: dev.name });
  if (r.ok) {
    writeLicense({ ...lic, activated: true, isAdmin: false, code: String(code).trim().toUpperCase(), name: r.name || "", expiresAt: r.expiresAt || null, lockedReason: null });
  } else if (r.reason === "no_server" || r.reason === "unreachable") {
    // keep whatever serverUrl we were given so the owner can retry
    writeLicense(lic);
  }
  return r;
});

ipcMain.handle("license:setAdmin", async (_e, { serverUrl: su, adminKey, masterPassword }) => {
  const lic = readLicense();
  lic.serverUrl = String(su || lic.serverUrl || "").replace(/\/+$/, "");
  const r = await post("/admin/verify", { adminSecret: adminKey });
  if (!r.ok) return r;
  const bcrypt = null; // keep it dependency-free: salted sha-256
  const salt = crypto.randomBytes(16).toString("hex");
  const hash = crypto.createHash("sha256").update(salt + ":" + String(masterPassword || "")).digest("hex");
  writeLicense({ ...lic, activated: true, isAdmin: true, adminKey, masterSalt: salt, masterHash: hash });
  return { ok: true };
});

ipcMain.handle("license:checkMaster", (_e, { password }) => {
  const lic = readLicense();
  if (!lic.masterHash) return { ok: false, reason: "not_set" };
  const hash = crypto.createHash("sha256").update((lic.masterSalt || "") + ":" + String(password || "")).digest("hex");
  return { ok: hash === lic.masterHash };
});

ipcMain.handle("license:adminCall", async (_e, { path: pathname, body }) => {
  const lic = readLicense();
  if (!lic.isAdmin || !lic.adminKey) return { ok: false, reason: "not_admin" };
  const r = await post(pathname, { ...(body || {}), adminSecret: lic.adminKey });
  if (r && r.ok && pathname === "/admin/generate" && r.code) {
    r.backupFile = appendCodeBackup({ code: r.code, name: r.name, createdAt: new Date().toISOString(), expiresAt: r.expiresAt });
  }
  return r;
});

ipcMain.handle("license:setServer", (_e, { serverUrl: su }) => {
  const lic = readLicense();
  writeLicense({ ...lic, serverUrl: String(su || "").replace(/\/+$/, "") });
  return { ok: true };
});

ipcMain.handle("license:deactivate", () => {
  const lic = readLicense();
  writeLicense({ serverUrl: lic.serverUrl });   // keep only the server URL
  return { ok: true };
});

ipcMain.handle("license:openCodesFile", () => { shell.showItemInFolder(codesBackupFile()); });
ipcMain.handle("app:openDataFolder", () => { shell.openPath(app.getPath("userData")); });

/* ---- config mirror: keys/identity survive even a full storage wipe ---- */
ipcMain.handle("config:load", () => encRead(CONFIG_FILE()));
ipcMain.handle("config:save", (_e, data) => { try { encWrite(CONFIG_FILE(), data || {}); return { ok: true }; } catch (e) { return { ok: false, error: String(e.message || e) }; } });

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
