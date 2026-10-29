import { useState } from "react";
import { api } from "../api";
import type { Job } from "../types";
import { errText, useData } from "../util";

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const jobs = useData(() => api.jobs({ status: status || undefined, limit: 200 }), [status], 2000);
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row between">
        <h1>Jobs</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option>
          <option value="queued,running">active</option>
          <option value="failed">failed</option>
          <option value="completed">completed</option>
          <option value="cancelled">cancelled</option>
        </select>
      </div>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Kind</th><th>Status</th><th>Stage</th><th>Progress</th><th>Attempts</th><th>Created</th><th /></tr></thead>
          <tbody>
            {(jobs.data ?? []).map((j) => (
              <JobRow key={j.id} j={j} open={open === j.id} toggle={() => setOpen(open === j.id ? null : j.id)}
                      onChange={jobs.refresh} />
            ))}
          </tbody>
        </table>
        {jobs.data?.length === 0 && <div className="empty">No jobs.</div>}
      </div>
    </div>
  );
}

function JobRow({ j, open, toggle, onChange }: { j: Job; open: boolean; toggle: () => void; onChange: () => void }) {
  const detail = useData(() => (open ? api.job(j.id) : Promise.resolve(undefined)), [open, j.updated_at]);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); onChange(); } catch (e) { setErr(errText(e)); }
  };
  const link = j.media_id ? (j.kind === "render" ? `#/m/${j.media_id}` : `#/p/${j.project_id}`) : undefined;
  return (
    <>
      <tr className="clickable" onClick={toggle}>
        <td>{link ? <a href={link} onClick={(e) => e.stopPropagation()}>{j.kind}</a> : j.kind}</td>
        <td><span className={`chip ${j.status}`}>{j.status}</span></td>
        <td className="small">{j.stage}{j.error_code && <div className="err">{j.error_code}</div>}</td>
        <td style={{ width: 160 }}><div className="bar"><div style={{ width: `${Math.round(j.progress * 100)}%` }} /></div></td>
        <td>{j.attempts}/{j.max_attempts}</td>
        <td className="small muted">{new Date(j.created_at).toLocaleString()}</td>
        <td onClick={(e) => e.stopPropagation()}>
          {(j.status === "queued" || j.status === "running") && <button className="tiny bad" onClick={() => act(() => api.cancelJob(j.id))}>Cancel</button>}
          {(j.status === "failed" || j.status === "cancelled") && <button className="tiny" onClick={() => act(() => api.retryJob(j.id))}>Retry</button>}
        </td>
      </tr>
      {open && (
        <tr><td colSpan={7}>
          {j.error && <div className="err small" style={{ whiteSpace: "pre-wrap", marginBottom: 8 }}>{j.error}</div>}
          {err && <div className="err small">{err}</div>}
          <div className="col small" style={{ gap: 2 }}>
            {(detail.data?.events ?? []).map((e, i) => (
              <div key={i} className={e.level === "error" ? "err" : e.level === "warning" ? "warn" : ""}>
                <span className="muted mono">{new Date(e.ts).toLocaleTimeString()}</span> {e.message}
              </div>
            ))}
          </div>
          {j.result && <pre className="mono small muted" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(j.result, null, 2)}</pre>}
        </td></tr>
      )}
    </>
  );
}
