// Keeps this machine's copy of ClipForge in step with GitHub, so work done on
// another machine (Windows PC / MacBook / a cloud session) is never missing
// and work done here is never stranded. Runs automatically at the start of
// every Claude session (a SessionStart hook that the setup scripts install via
// scripts/install-sync-hook.mjs), and by hand:
//
//   npm run sync
//
// It only does the two moves that can't lose anything:
//   - behind, nothing local pending  → fast-forward pull (gets the other machine's work)
//   - ahead (committed, not pushed)   → push (sends this machine's work up)
// Anything else (uncommitted edits, or both machines having new commits) is
// reported for Claude/the user to resolve, never forced.
//
// Deliberately NOT named sync-check.mjs: MCC's installer recognises its own
// hook by that name and would overwrite this one.
//
// Output: SessionStart hook JSON (context for Claude + a one-line message for
// the user). Always exits 0 so a session is never blocked.

import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
const gitRaw = (...args) =>
  execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 20_000 });
const git = (...args) => gitRaw(...args).trim();

const lines = [];
let headline;

try {
  const branch = git("rev-parse", "--abbrev-ref", "HEAD");
  const upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}");
  let online = true;
  try {
    git("fetch", "--quiet");
  } catch {
    online = false;
  }
  const [behind, ahead] = git("rev-list", "--left-right", "--count", `${upstream}...HEAD`).split(/\s+/).map(Number);
  // Untrimmed: each porcelain line starts with a 2-char status (often " M").
  const dirty = gitRaw("status", "--porcelain").split(/\r?\n/).filter(Boolean);
  const before = git("rev-parse", "HEAD");

  if (!online) {
    headline = "ClipForge sync: couldn't reach GitHub — working on this machine's copy, may be out of date";
    lines.push("GitHub unreachable (offline?). Run `npm run sync` in the ClipForge repo once online, before relying on this copy being current.");
  } else if (behind > 0 && ahead > 0) {
    headline = `ClipForge sync: this machine and GitHub BOTH have new commits (${ahead} here, ${behind} there) — needs merging`;
    lines.push(
      `Diverged on ${branch}: ${ahead} local commit(s) not on GitHub and ${behind} GitHub commit(s) not here. Nothing was changed.`,
      "Before other work: `git pull --no-rebase` in the ClipForge repo, resolve any conflicts with the user, run the tests, then push.",
    );
  } else if (behind > 0 && dirty.length > 0) {
    headline = `ClipForge sync: ${behind} new commit(s) on GitHub, but this machine has uncommitted changes — not pulled`;
    lines.push(
      `${behind} commit(s) from another machine are waiting, and ${dirty.length} file(s) here have uncommitted changes. Nothing was changed.`,
      "Before other work: ask the user whether to commit or set aside those local changes, then `git pull`.",
    );
  } else if (behind > 0) {
    git("pull", "--ff-only", "--quiet");
    const changed = git("diff", "--name-only", before, "HEAD").split("\n").filter(Boolean);
    headline = `ClipForge sync: pulled ${behind} new commit(s) from GitHub (another machine's work)`;
    lines.push(`Fast-forwarded ${branch} by ${behind} commit(s): ${git("log", "--oneline", `${before}..HEAD`).split("\n").join(" | ")}`);
    if (changed.some((f) => /^backend\/(pyproject\.toml|requirements[^/]*\.txt)$/.test(f))) {
      lines.push("Python dependencies changed → re-run setup (setup.cmd on Windows, scripts/setup-mac.sh on macOS).");
    }
    if (changed.some((f) => f === "frontend/package.json" || f === "frontend/package-lock.json")) {
      lines.push("UI dependencies changed → re-run setup (it runs `npm ci` + build in frontend/).");
    }
    if (changed.some((f) => f.startsWith("backend/clipforge/db/migrations/"))) {
      lines.push("New database migration → restart ClipForge if it's running (migrations apply on start).");
    }
    lines.push("Re-read HANDOFF.md — it was likely updated on the other machine.");
  } else if (ahead > 0) {
    git("push", "--quiet");
    headline = `ClipForge sync: pushed ${ahead} commit(s) from this machine that hadn't reached GitHub`;
    lines.push(`Pushed ${ahead} earlier commit(s) on ${branch}, so the other machines can pull them.`);
  } else {
    headline = "ClipForge sync: up to date with GitHub";
    lines.push(`${branch} matches GitHub.`);
  }
  if (dirty.length > 0 && !(behind > 0)) {
    lines.push(`Note: ${dirty.length} uncommitted file(s) here from earlier work (${dirty.slice(0, 5).map((l) => l.slice(3)).join(", ")}${dirty.length > 5 ? ", …" : ""}) — commit + push at closeout so the other machines get them.`);
  }
} catch (err) {
  headline = "ClipForge sync: check failed — run `npm run sync` in the ClipForge repo";
  lines.push(`Sync check error: ${String(err?.stderr || err?.message || err).split("\n")[0]}`);
}

const context = [`[ClipForge repo sync — ${repo}]`, headline, ...lines].join("\n");
if (process.argv.includes("--plain")) {
  console.log(context);
} else {
  console.log(JSON.stringify({ systemMessage: headline, hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: context } }));
}
