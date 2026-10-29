import { api } from "../api";
import type { Job } from "../types";

const LABEL: Record<string, string> = { transcribe: "Transcribing", analyze: "Finding clips", render: "Rendering" };

export default function JobBar({ job, onChange }: { job: Job; onChange?: () => void }) {
  const pct = Math.round(job.progress * 100);
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="row between small">
        <span>
          <span className={`chip ${job.status}`}>{job.status}</span>{" "}
          {LABEL[job.kind] ?? job.kind} — <span className="muted">{job.stage}</span>
          {job.attempts > 1 && <span className="muted"> (attempt {job.attempts}/{job.max_attempts})</span>}
        </span>
        <span className="row">
          <span className="muted">{pct}%</span>
          {(job.status === "queued" || job.status === "running") && (
            <button className="tiny bad" disabled={job.cancel_requested}
                    onClick={async () => { await api.cancelJob(job.id); onChange?.(); }}>
              {job.cancel_requested ? "Cancelling…" : "Cancel"}
            </button>
          )}
        </span>
      </div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
