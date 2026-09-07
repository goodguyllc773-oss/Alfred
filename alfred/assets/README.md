# assets

| File | Used by | Notes |
|---|---|---|
| `alfred.png` | sidebar mark, greeting portrait, chat avatar, browser favicon, Electron window + installer icon | Square, ≥256×256 (the source art ~1250×1250 is ideal). Everything falls back to an "A" monogram if this file is missing. |
| `alfred.ico` *(optional)* | — | If present you can point `package.json` → `build.win.icon` at it for a crisper Windows installer icon; the PNG works fine otherwise. |
