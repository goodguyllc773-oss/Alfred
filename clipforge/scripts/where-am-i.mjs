// SessionStart guard: is this Claude session running on the user's own computer?
//
// ClipForge is developed locally (Windows PC, or the Mac): the GPU, FFmpeg/NVENC,
// the user's videos and their own `gh` login all live there. A cloud session
// (a temporary Linux container) can't reach any of that. On 2026-10-29 a whole
// session ran in the cloud by accident, and the user didn't find out until the
// work needed their PC. This hook makes that impossible to miss: it ships in
// the repo (.claude/settings.json), so it runs in cloud sessions too, unlike the
// sync hook, which is installed in the PC's own Claude settings.
//
// Output: SessionStart hook JSON. Always exits 0.

import os from "node:os";

const env = process.env;
const cloud = Boolean(env.CLAUDE_CODE_REMOTE_SESSION_ID || env.CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE ||
  env.CLAUDE_CODE_REMOTE === "true");
const where = `${os.platform()} ${os.release()}, host "${os.hostname()}"`;

let headline, context;
if (cloud) {
  headline = "⚠ ClipForge: this session is running in the CLOUD, not on your PC. Start a new session on a folder on your computer.";
  context = [
    `[ClipForge location check] CLOUD SESSION (${env.CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE || "remote"}; ${where}).`,
    "ClipForge is developed on the user's own computer. STOP: before doing ANY other work, tell the user plainly,",
    "in your first reply, that this session runs in a cloud container and cannot use their PC, GPU, files or gh login.",
    "Tell them how to fix it: in the desktop app, start a new Claude Code session and choose the folder",
    "C:\\Users\\xalex\\clipforge on their computer (not a GitHub repo, and not the \"Default\" environment).",
    "Only continue here if the user explicitly says this task should be done in the cloud.",
  ].join("\n");
} else if (os.platform() !== "win32" && os.platform() !== "darwin") {
  headline = `ClipForge: running on ${os.platform()}, not the Windows PC or the Mac. Check that's intended.`;
  context = `[ClipForge location check] Local session on ${where}. ClipForge's main machine is the Windows PC (RTX 2060 Super); mention this to the user before machine-specific work.`;
} else {
  headline = `ClipForge: local session on ${os.platform() === "win32" ? "Windows" : "macOS"} (${os.hostname()}).`;
  context = `[ClipForge location check] Local session on ${where}. OK to work.`;
}
console.log(JSON.stringify({ systemMessage: headline, hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: context } }));
