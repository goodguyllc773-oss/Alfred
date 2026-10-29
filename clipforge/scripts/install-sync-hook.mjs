// Installs the Claude Code SessionStart hook that runs scripts/repo-sync.mjs,
// so every Claude session on this machine starts with ClipForge synced with
// GitHub. Merged into the user's Claude settings (~/.claude/settings.json, or
// $CLAUDE_CONFIG_DIR); other hooks (e.g. MCC's) are left untouched.
// Called by the setup scripts; set CLIPFORGE_NO_SYNC_HOOK=1 to skip.
//
//   npm run install-hook

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const say = (msg) => console.log(`[sync-hook] ${msg}`);

if (process.env.CLIPFORGE_NO_SYNC_HOOK === "1") {
  say("CLIPFORGE_NO_SYNC_HOOK=1 — skipped");
  process.exit(0);
}

const dir = process.env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude");
const file = join(dir, "settings.json");
const command = `node "${join(root, "scripts", "repo-sync.mjs").replace(/\\/g, "/")}"`;
let settings = {};
if (existsSync(file)) {
  try {
    settings = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    say(`couldn't read ${file} (not valid JSON) — left it alone; add the hook by hand (see CLAUDE.md)`);
    process.exit(0);
  }
}
settings.hooks ??= {};
settings.hooks.SessionStart ??= [];
const isOurs = (h) => /repo-sync\.mjs/.test(String(h.command ?? "")) && /clipforge/i.test(String(h.command ?? ""));
const existing = settings.hooks.SessionStart.flatMap((g) => g.hooks ?? []).find(isOurs);
if (existing?.command === command) {
  say("already installed");
  process.exit(0);
}
if (existing) existing.command = command; // repo moved — point at this copy
else {
  settings.hooks.SessionStart.push({
    matcher: "startup|resume",
    hooks: [{ type: "command", command, timeout: 45, statusMessage: "Syncing ClipForge with GitHub…" }],
  });
}
mkdirSync(dir, { recursive: true });
writeFileSync(file, JSON.stringify(settings, null, 2) + "\n");
say(`installed in ${file} (each Claude session on this machine starts by syncing ClipForge with GitHub)`);
