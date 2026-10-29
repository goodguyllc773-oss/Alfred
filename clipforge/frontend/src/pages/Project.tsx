import { useState } from "react";
import { api } from "../api";
import ImportDialog from "../components/ImportDialog";
import JobBar from "../components/JobBar";
import type { Job, Media } from "../types";
import { errText, fmtBytes, fmtTime, go, useData } from "../util";

export default function ProjectPage({ projectId }: { projectId: string }) {
  // Poll steadily: it's a local API, and new work can be queued from other tabs.
  const project = useData(() => api.project(projectId), [projectId], 2000);
  const profiles = useData(() => api.profiles(), []);
  const recent = useData(() => api.jobs({ project_id: projectId, status: "failed", limit: 5 }), [projectId], 4000);
  const [importing, setImporting] = useState(false);
  const p = project.data;

  if (project.error) return <div className="panel err">{project.error}</div>;
  if (!p) return <div className="muted">Loading…</div>;

  const kick = () => { project.refresh(); recent.refresh(); };

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="row between">
        <div>
          <div className="muted small"><a href="#/">Projects</a> /</div>
          <h1>{p.name}</h1>
        </div>
        <button className="primary" onClick={() => setImporting(true)}>+ Import video</button>
      </div>

      {(recent.data ?? []).length > 0 && (
        <div className="panel col">
          <h3>Recent failures</h3>
          {recent.data!.map((j) => <FailedJob key={j.id} job={j} onRetry={kick} />)}
        </div>
      )}

      {p.media!.length === 0 && (
        <div className="panel empty">
          No videos yet. Import a long video — ClipForge transcribes it locally, finds 30–60 s moments,
          and puts them in a review queue.
        </div>
      )}
      {p.media!.map((m) => (
        <MediaRow key={m.id} m={m} profiles={profiles.data?.map((x) => ({ key: x.key, name: x.name })) ?? []}
                  onChange={kick} />
      ))}

      {importing && profiles.data && (
        <ImportDialog projectId={projectId} defaultProfile={p.default_profile_key} profiles={profiles.data}
                      onClose={() => setImporting(false)} onImported={kick} />
      )}
    </div>
  );
}

function FailedJob({ job, onRetry }: { job: Job; onRetry: () => void }) {
  return (
    <div className="row between small" style={{ alignItems: "flex-start" }}>
      <div style={{ flex: 1 }}>
        <span className="chip failed">{job.kind}</span> <code>{job.error_code}</code>{" "}
        <span style={{ whiteSpace: "pre-wrap" }}>{job.error}</span>
      </div>
      <button className="tiny" onClick={async () => { await api.retryJob(job.id); onRetry(); }}>Retry</button>
    </div>
  );
}

function MediaRow({ m, profiles, onChange }: { m: Media; profiles: { key: string; name: string }[]; onChange: () => void }) {
  const [profile, setProfile] = useState(m.profile_key);
  const [relink, setRelink] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const counts = m.candidate_counts;
  const total = (counts.pending ?? 0) + (counts.approved ?? 0) + (counts.rejected ?? 0);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); onChange(); } catch (e) { setErr(errText(e)); }
  };

  return (
    <div className="panel col">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="col" style={{ gap: 4 }}>
          <h2 style={{ margin: 0 }}>{m.file_name} <span className={`chip ${m.status}`}>{m.status}</span></h2>
          <div className="muted small">
            {fmtTime(m.duration_s)} · {m.width}×{m.height} · {m.fps} fps · {m.video_codec}
            {" · "}{m.has_audio ? `audio ${m.audio_codec} ${m.audio_channels}ch` : <span className="warn">no audio</span>}
            {" · "}{fmtBytes(m.file_size)}
          </div>
          <div className="mono small muted" title={m.path}>{m.path}</div>
          <div className="small">
            Transcript:{" "}
            {m.transcript ? (
              <span>
                <span className={`chip ${m.transcript.status === "complete" ? "completed" : "running"}`}>{m.transcript.status}</span>{" "}
                {m.transcript.engine === "sidecar"
                  ? `imported subtitles (${m.transcript.model.split(/[\\/]/).pop()})`
                  : `${m.transcript.engine} ${m.transcript.model} on ${m.transcript.device || "?"}`}
                {m.transcript.language && ` · ${m.transcript.language}`}
                {m.transcript.status === "partial" && ` · ${m.transcript.chunks_done}/${m.transcript.chunks_total} parts`}
                {!m.transcript.word_timestamps && <span className="warn"> · word timings estimated</span>}
              </span>
            ) : m.has_audio ? <span className="muted">not yet</span> : <span className="muted">skipped (no audio)</span>}
          </div>
        </div>
        <div className="col" style={{ alignItems: "flex-end" }}>
          <button className="primary" disabled={!total} onClick={() => go(`/m/${m.id}`)}>
            Review clips ({counts.pending ?? 0} to review)
          </button>
          <div className="small muted">{counts.approved ?? 0} approved · {counts.rejected ?? 0} rejected</div>
        </div>
      </div>

      {m.active_jobs.map((j) => <JobBar key={j.id} job={j} onChange={onChange} />)}

      {m.status === "missing" && (
        <div className="col" style={{ border: "1px solid var(--danger)", borderRadius: 8, padding: 10 }}>
          <div className="err small">The source file isn't where it was. Point ClipForge at its new location:</div>
          <div className="row">
            <input className="mono" style={{ flex: 1 }} value={relink} onChange={(e) => setRelink(e.target.value)}
                   placeholder="new full path to the same file" />
            <button onClick={() => act(() => api.relink(m.id, relink))} disabled={!relink.trim()}>Relink</button>
          </div>
        </div>
      )}

      <div className="row small">
        <label className="inline">Profile
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            {profiles.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
        </label>
        <button className="tiny" onClick={() => act(() => api.analyze(m.id, profile))}
                disabled={m.has_audio && m.transcript?.status !== "complete"}>
          Find clips again
        </button>
        {m.has_audio && (
          <button className="tiny ghost" onClick={() => act(() => api.transcribe(m.id, true))}>Re-transcribe</button>
        )}
        <span className="muted">Re-analysis keeps clips you approved, rejected or edited.</span>
      </div>
      {err && <div className="err small">{err}</div>}
    </div>
  );
}
