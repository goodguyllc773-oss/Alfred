/* ---------------------------------------------------------------------------
   Alfred license service — Cloudflare Worker + KV.

   Public:
     POST /activate   {code, deviceId, deviceName}  -> bind + activate a code
     POST /check      {code, deviceId}              -> is this still valid?
   Admin (all take {adminSecret}):
     POST /admin/verify                             -> is this the admin key?
     POST /admin/list                               -> every code + status
     POST /admin/generate  {name, expiresAt?}       -> mint a new code
     POST /admin/revoke    {code}
     POST /admin/unrevoke  {code}
     POST /admin/release   {code}                   -> unbind the device

   Bindings (wrangler.toml / dashboard):
     KV namespace  CODES
     Secret        ADMIN_SECRET
--------------------------------------------------------------------------- */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", ...CORS } });

// Crockford base32-ish, no I/L/O/U to avoid confusion
const ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789";
function newCode() {
  const buf = crypto.getRandomValues(new Uint8Array(12));
  let s = "";
  for (let i = 0; i < 12; i++) {
    s += ALPHABET[buf[i] % ALPHABET.length];
    if (i % 4 === 3 && i < 11) s += "-";
  }
  return "ALF-" + s;
}

function statusOf(rec) {
  if (rec.revoked) return "revoked";
  if (rec.expiresAt && Date.now() > Date.parse(rec.expiresAt)) return "expired";
  if (!rec.deviceId) return "unused";
  return "active";
}

async function readBody(request) {
  try { return await request.json(); } catch { return {}; }
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    if (request.method !== "POST") return json({ ok: false, reason: "use POST" }, 405);

    const url = new URL(request.url);
    const p = url.pathname.replace(/\/+$/, "") || "/";
    const body = await readBody(request);
    const isAdmin = p.startsWith("/admin/");

    if (isAdmin) {
      if (!env.ADMIN_SECRET || body.adminSecret !== env.ADMIN_SECRET)
        return json({ ok: false, reason: "bad_admin_key" }, 403);
    }

    try {
      // ---- public ----
      if (p === "/activate" || p === "/check") {
        const code = String(body.code || "").trim().toUpperCase();
        const deviceId = String(body.deviceId || "").trim();
        if (!code || !deviceId) return json({ ok: false, reason: "missing_fields" }, 400);

        const raw = await env.CODES.get(code);
        if (!raw) return json({ ok: false, reason: "not_found" }, 404);
        const rec = JSON.parse(raw);
        const st = statusOf(rec);

        if (st === "revoked") return json({ ok: false, reason: "revoked" });
        if (st === "expired") return json({ ok: false, reason: "expired" });

        if (p === "/check") {
          if (rec.deviceId && rec.deviceId !== deviceId) return json({ ok: false, reason: "device_mismatch" });
          rec.lastSeen = new Date().toISOString();
          await env.CODES.put(code, JSON.stringify(rec));
          return json({ ok: true, name: rec.name || "", expiresAt: rec.expiresAt || null });
        }

        // activate
        if (rec.deviceId && rec.deviceId !== deviceId)
          return json({ ok: false, reason: "device_mismatch" });
        rec.deviceId = deviceId;
        rec.deviceName = String(body.deviceName || rec.deviceName || "");
        rec.activatedAt = rec.activatedAt || new Date().toISOString();
        rec.lastSeen = new Date().toISOString();
        await env.CODES.put(code, JSON.stringify(rec));
        return json({ ok: true, name: rec.name || "", expiresAt: rec.expiresAt || null });
      }

      // ---- admin ----
      if (p === "/admin/verify") return json({ ok: true });

      if (p === "/admin/list") {
        const out = [];
        let cursor;
        do {
          const page = await env.CODES.list({ cursor, limit: 1000 });
          for (const k of page.keys) {
            const raw = await env.CODES.get(k.name);
            if (!raw) continue;
            const rec = JSON.parse(raw);
            out.push({
              code: k.name, name: rec.name || "", status: statusOf(rec),
              createdAt: rec.createdAt || null, expiresAt: rec.expiresAt || null,
              activatedAt: rec.activatedAt || null, lastSeen: rec.lastSeen || null,
              deviceName: rec.deviceName || "", revoked: !!rec.revoked,
            });
          }
          cursor = page.list_complete ? null : page.cursor;
        } while (cursor);
        out.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
        return json({ ok: true, codes: out });
      }

      if (p === "/admin/generate") {
        let code;
        for (let i = 0; i < 5; i++) { code = newCode(); if (!(await env.CODES.get(code))) break; }
        const rec = {
          name: String(body.name || "").slice(0, 60),
          createdAt: new Date().toISOString(),
          expiresAt: body.expiresAt ? new Date(body.expiresAt).toISOString() : null,
          deviceId: null, deviceName: "", activatedAt: null, lastSeen: null, revoked: false,
        };
        await env.CODES.put(code, JSON.stringify(rec));
        return json({ ok: true, code, name: rec.name, expiresAt: rec.expiresAt });
      }

      const code = String(body.code || "").trim().toUpperCase();
      if (["/admin/revoke", "/admin/unrevoke", "/admin/release"].includes(p)) {
        const raw = await env.CODES.get(code);
        if (!raw) return json({ ok: false, reason: "not_found" }, 404);
        const rec = JSON.parse(raw);
        if (p === "/admin/revoke") rec.revoked = true;
        if (p === "/admin/unrevoke") rec.revoked = false;
        if (p === "/admin/release") { rec.deviceId = null; rec.deviceName = ""; rec.activatedAt = null; }
        await env.CODES.put(code, JSON.stringify(rec));
        return json({ ok: true, status: statusOf(rec) });
      }

      return json({ ok: false, reason: "unknown_route" }, 404);
    } catch (e) {
      return json({ ok: false, reason: "server_error", detail: String(e && e.message || e) }, 500);
    }
  },
};
