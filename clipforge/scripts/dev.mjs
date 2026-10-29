// `npm run dev`: start everything ClipForge needs for development, in one window.
//
//   api  → http://127.0.0.1:8765   FastAPI + the job worker (backend/.venv)
//   web  → http://127.0.0.1:5173   Vite dev server with hot reload; proxies /api to :8765
//
// Open http://127.0.0.1:5173/ (the Claude app's preview pane does this via
// .claude/launch.json). Ctrl+C stops both. If either one exits, the other is
// stopped too, so you never end up with half the app running.
//
// Needs `setup.cmd` (Windows) / `scripts/setup-mac.sh` (macOS) to have run once.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const win = process.platform === "win32";
const python = process.env.CLIPFORGE_PYTHON ||
  join(root, "backend", ".venv", win ? "Scripts/python.exe" : "bin/python");

const problems = [];
if (!existsSync(python)) problems.push(`Python venv not found at ${python}`);
if (!existsSync(join(root, "frontend", "node_modules"))) problems.push("frontend/node_modules is missing");
if (problems.length) {
  console.error("[dev] ClipForge isn't set up on this machine yet:");
  for (const p of problems) console.error(`[dev]   - ${p}`);
  console.error(`[dev] Run ${win ? "setup.cmd" : "scripts/setup-mac.sh"} from the repo root first.`);
  process.exit(1);
}

const colors = { api: "\x1b[36m", web: "\x1b[32m" };
const children = new Map();
let stopping = false;

function run(name, cmd, args, cwd) {
  // .cmd shims (npm on Windows) need a shell; python.exe doesn't. On POSIX each
  // child leads its own process group, so stopping it reaches grandchildren
  // too (npm → sh → vite); signalling npm alone leaves vite running.
  const child = spawn(cmd, args, { cwd, env: process.env, shell: win && cmd === "npm", detached: !win });
  children.set(name, child);
  const prefix = `${colors[name]}[${name}]\x1b[0m `;
  for (const stream of [child.stdout, child.stderr]) {
    let buf = "";
    stream.on("data", (d) => {
      buf += d.toString();
      const lines = buf.split(/\r?\n/);
      buf = lines.pop();
      for (const line of lines) process.stdout.write(prefix + line + "\n");
    });
  }
  child.on("exit", (code, signal) => {
    children.delete(name);
    if (!stopping) {
      console.log(`[dev] ${name} exited (${signal ?? code}); stopping the rest.`);
      stopAll(code ?? 1);
    } else if (children.size === 0) {
      process.exit(0);
    }
  });
}

function stopAll(code = 0) {
  stopping = true;
  if (children.size === 0) process.exit(code);
  const signalAll = (sig) => {
    for (const child of children.values()) {
      try {
        if (win) spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"]);  // whole tree (npm → node → vite)
        else process.kill(-child.pid, sig);  // negative pid = the child's whole process group
      } catch { /* already gone */ }
    }
  };
  signalAll("SIGINT");
  // Anything still alive after 5 s (e.g. a render's ffmpeg) is forced.
  setTimeout(() => { signalAll("SIGKILL"); process.exit(code); }, 5000).unref();
}

process.on("SIGINT", () => stopAll(0));
process.on("SIGTERM", () => stopAll(0));

console.log("[dev] ClipForge: API + worker on http://127.0.0.1:8765, UI on http://127.0.0.1:5173/");
run("api", python, ["-m", "clipforge", "serve", "--port", "8765"], join(root, "backend"));
run("web", "npm", ["run", "dev"], join(root, "frontend"));
