import { useEffect, useState } from "react";
import { api } from "../api";
import type { FsEntry, Profile } from "../types";
import { errText, fmtBytes, useData } from "../util";

interface Props {
  projectId: string;
  defaultProfile: string;
  profiles: Profile[];
  onClose: () => void;
  onImported: () => void;
}

const LAST_DIR = "clipforge:lastDir";

export default function ImportDialog({ projectId, defaultProfile, profiles, onClose, onImported }: Props) {
  const [dir, setDir] = useState<string | undefined>(() => {
    try { return localStorage.getItem(LAST_DIR) || undefined; } catch { return undefined; }
  });
  const listing = useData(() => api.fsList(dir), [dir]);
  const [path, setPath] = useState("");
  const [transcript, setTranscript] = useState("");
  const [profile, setProfile] = useState(defaultProfile);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (dir) try { localStorage.setItem(LAST_DIR, dir); } catch { /* storage blocked */ }
  }, [dir]);

  const pick = (e: FsEntry) => {
    if (e.kind === "dir") setDir(e.path);
    else if (e.kind === "video") setPath(e.path);
    else setTranscript(e.path);
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.importMedia(projectId, { path, profile_key: profile, transcript_path: transcript || null });
      onImported();
      onClose();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="panel modal col" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2>Import a video</h2>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>
        <p className="muted small" style={{ margin: 0 }}>
          ClipForge reads the file where it is — it never copies, moves or changes your footage.
          Only import footage you have the rights to edit.
        </p>
        <div className="row">
          {listing.data?.parent != null && <button className="tiny" onClick={() => setDir(listing.data!.parent!)}>↑ Up</button>}
          {listing.data?.path && <button className="tiny" onClick={() => setDir(undefined)}>Places</button>}
          <span className="mono small muted">{listing.data?.path ?? "Places"}</span>
        </div>
        <div className="fs-list">
          {listing.error && <div className="err small" style={{ padding: 10 }}>{listing.error}</div>}
          {(listing.data?.entries ?? []).map((e) => (
            <div key={e.path} className={`fs-item ${e.path === path || e.path === transcript ? "sel" : ""}`}
                 onClick={() => pick(e)} onDoubleClick={() => e.kind === "video" && setPath(e.path)}>
              <span>{e.kind === "dir" ? "📁" : e.kind === "video" ? "🎬" : "📝"}</span>
              <span style={{ flex: 1 }}>{e.name}</span>
              {e.size != null && <span className="muted small">{fmtBytes(e.size)}</span>}
            </div>
          ))}
          {listing.data && listing.data.entries.length === 0 && <div className="muted small" style={{ padding: 10 }}>No folders or videos here.</div>}
        </div>
        <label>Video file (pick above or paste a full path)
          <input className="mono" value={path} onChange={(e) => setPath(e.target.value)}
                 placeholder="C:\Videos\stream-2024-06-01.mp4" />
        </label>
        <label>Existing transcript (optional .srt / .vtt / .json — skips Whisper)
          <input className="mono" value={transcript} onChange={(e) => setTranscript(e.target.value)} placeholder="optional" />
        </label>
        <label>Editing profile
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            {profiles.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
        </label>
        <div className="muted small">{profiles.find((p) => p.key === profile)?.config.description}</div>
        {err && <div className="err">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!path.trim() || busy} onClick={submit}>
            {busy ? "Checking file…" : "Import & process"}
          </button>
        </div>
      </div>
    </div>
  );
}
