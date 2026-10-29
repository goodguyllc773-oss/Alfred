import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import JobBar from "../components/JobBar";
import type { Candidate, CaptionPreset, LayoutConfig, Media, Profile, Rect, ReviewStatus } from "../types";
import { copyText, errText, fmtTime, useData } from "../util";

type Filter = "all" | ReviewStatus;

export default function ReviewPage({ mediaId }: { mediaId: string }) {
  const media = useData(() => api.media(mediaId), [mediaId], 3000);
  const cands = useData(() => api.candidates(mediaId), [mediaId], 2500);
  const jobs = useData(() => api.jobs({ media_id: mediaId, status: "queued,running" }), [mediaId], 1500);
  const profiles = useData(() => api.profiles(), []);
  const presets = useData(() => api.captionPresets(), []);
  const [filter, setFilter] = useState<Filter>("pending");
  const [selId, setSelId] = useState<string | null>(null);

  const list = useMemo(() => (cands.data ?? []).filter((c) => filter === "all" || c.review_status === filter),
    [cands.data, filter]);
  const sel = (cands.data ?? []).find((c) => c.id === selId) ?? list[0] ?? null;
  const refreshAll = () => { cands.refresh(); jobs.refresh(); media.refresh(); };

  // Keyboard: J/K move, A approve, X reject.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      const i = sel ? list.findIndex((c) => c.id === sel.id) : -1;
      if (e.key === "j" && i < list.length - 1) setSelId(list[i + 1].id);
      if (e.key === "k" && i > 0) setSelId(list[i - 1].id);
      if (sel && e.key === "a") api.review(sel.id, "approve").then(refreshAll);
      if (sel && e.key === "x") api.review(sel.id, "reject").then(refreshAll);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  if (media.error) return <div className="panel err">{media.error}</div>;
  if (!media.data) return <div className="muted">Loading…</div>;
  const m = media.data;
  const counts = (s: Filter) => (cands.data ?? []).filter((c) => s === "all" || c.review_status === s).length;
  const mediaJobs = (jobs.data ?? []).filter((j) => j.kind !== "render");

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row between">
        <div>
          <div className="muted small"><a href={`#/p/${m.project_id}`}>← Project</a></div>
          <h1 style={{ marginBottom: 0 }}>{m.file_name}</h1>
          <div className="muted small">{fmtTime(m.duration_s)} · {m.width}×{m.height} · keys: J/K next/prev · A approve · X reject</div>
        </div>
        <div className="tabs">
          {(["pending", "approved", "rejected", "all"] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f} ({counts(f)})</button>
          ))}
        </div>
      </div>
      {mediaJobs.map((j) => <div key={j.id} className="panel"><JobBar job={j} onChange={refreshAll} /></div>)}
      {m.status === "missing" && <div className="panel err">Source file is missing — relink it from the project page before rendering.</div>}

      <div className="review">
        <div className="clist">
          {list.length === 0 && <div className="panel empty small">Nothing {filter === "all" ? "" : filter} here.</div>}
          {list.map((c) => (
            <div key={c.id} className={`citem ${sel?.id === c.id ? "sel" : ""}`} onClick={() => setSelId(c.id)}>
              <div className="row between small">
                <span><span className="score">{c.score.toFixed(0)}</span> <span className="muted">#{c.rank}</span></span>
                <span className="muted mono">{fmtTime(c.start_s)}–{fmtTime(c.end_s)} · {c.duration_s.toFixed(0)}s</span>
                <span className={`chip ${c.review_status}`}>{c.review_status}</span>
              </div>
              <div className="t">{c.post_title || c.transcript_text || <i className="muted">no speech</i>}</div>
              {c.warnings.length > 0 && <div className="warn small">⚠ {c.warnings.length} warning(s)</div>}
            </div>
          ))}
        </div>
        {sel ? (
          <ClipDetail key={sel.id} c={sel} m={m} profiles={profiles.data ?? []} presets={presets.data ?? []}
                      renderJob={(jobs.data ?? []).find((j) => j.kind === "render" && j.candidate_id === sel.id)}
                      onChange={refreshAll} />
        ) : <div className="panel empty">Select a clip.</div>}
      </div>
    </div>
  );
}

interface DetailProps {
  c: Candidate;
  m: Media;
  profiles: Profile[];
  presets: CaptionPreset[];
  renderJob?: import("../types").Job;
  onChange: () => void;
}

function ClipDetail({ c, m, profiles, presets, renderJob, onChange }: DetailProps) {
  const latest = c.renders.filter((r) => r.status === "completed").at(-1);
  const failed = c.renders.at(-1)?.status === "failed" ? c.renders.at(-1) : undefined;
  const [view, setView] = useState<"render" | "source">(latest ? "render" : "source");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (latest) setView("render"); }, [latest?.id]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setErr(null);
    try { await fn(); if (ok) setNote(ok); onChange(); } catch (e) { setErr(errText(e)); }
  };

  return (
    <>
      <div className="col">
        <div className="row between">
          <div className="tabs">
            <button className={view === "render" ? "on" : ""} disabled={!latest} onClick={() => setView("render")}>
              Rendered {latest ? `v${latest.version}` : ""}
            </button>
            <button className={view === "source" ? "on" : ""} onClick={() => setView("source")}>Source range</button>
          </div>
          <div className="row">
            <button className="good" onClick={() => act(() => api.review(c.id, "approve"),
              latest ? "Approved." : "Approved — rendering it now.")}>✓ Approve</button>
            <button className="bad" onClick={() => act(() => api.review(c.id, "reject"))}>✕ Reject</button>
            {c.review_status !== "pending" && <button className="ghost" onClick={() => act(() => api.review(c.id, "reset"))}>Reset</button>}
          </div>
        </div>
        {view === "render" && latest ? (
          <div className="player"><video key={latest.id} src={api.renderUrl(latest.id)} controls autoPlay playsInline /></div>
        ) : (
          <SourcePreview m={m} start={c.start_s} end={c.end_s} />
        )}
        {renderJob && <JobBar job={renderJob} onChange={onChange} />}
        {failed && !renderJob && <div className="err small">Last render failed: {failed.error}</div>}
        <div className="row">
          <button className="primary" disabled={!!renderJob || m.status !== "ready"}
                  onClick={() => act(() => api.render(c.id))}>{latest ? "Render again" : "Render"}</button>
          {latest && <span className="muted small">{latest.width}×{latest.height} · {latest.encoder} · {latest.duration_s?.toFixed(1)}s</span>}
        </div>
        <Boundaries c={c} m={m} onSaved={onChange} />
        {latest && <ExportBox renderId={latest.id} c={c} />}
        {err && <div className="err small">{err}</div>}
        {note && <div className="ok small">{note}</div>}
      </div>
      <div className="side col">
        <WhyPanel c={c} />
        <EditPanel c={c} m={m} profiles={profiles} presets={presets} onSaved={onChange} />
      </div>
    </>
  );
}

function SourcePreview({ m, start, end }: { m: Media; start: number; end: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  if (!m.browser_playable || m.status !== "ready") {
    return (
      <div className="col">
        <div className="muted small">This format can't play in the browser — render the clip to preview it. Frames from the range:</div>
        <div className="row">
          {[0.05, 0.5, 0.95].map((f) => (
            <img key={f} src={api.frameUrl(m.id, start + (end - start) * f, 320)} style={{ width: "32%", borderRadius: 6 }} alt="" />
          ))}
        </div>
      </div>
    );
  }
  const landscape = m.width > m.height;
  return (
    <div className={`player ${landscape ? "landscape" : ""}`}>
      <video ref={ref} src={`${api.sourceUrl(m.id)}#t=${start.toFixed(2)},${end.toFixed(2)}`} controls playsInline
             onTimeUpdate={(e) => {
               const v = e.currentTarget;
               if (v.currentTime > end + 0.05) { v.pause(); v.currentTime = start; }
               if (v.currentTime < start - 0.5) v.currentTime = start;
             }} />
    </div>
  );
}

function Boundaries({ c, m, onSaved }: { c: Candidate; m: Media; onSaved: () => void }) {
  const [start, setStart] = useState(c.start_s);
  const [end, setEnd] = useState(c.end_s);
  const [snap, setSnap] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setStart(c.start_s); setEnd(c.end_s); }, [c.start_s, c.end_s]);
  const dirty = Math.abs(start - c.start_s) > 1e-3 || Math.abs(end - c.end_s) > 1e-3;
  const clamp = (v: number) => Math.max(0, Math.min(m.duration_s, Math.round(v * 100) / 100));
  const save = async (s: number, e: number, doSnap: boolean) => {
    setErr(null);
    try { await api.patchCandidate(c.id, { start_s: s, end_s: e, snap: doSnap }); onSaved(); }
    catch (x) { setErr(errText(x)); }
  };
  const nudge = (which: "s" | "e", d: number) => (which === "s" ? setStart(clamp(start + d)) : setEnd(clamp(end + d)));

  return (
    <div className="panel col">
      <h3>Clip boundaries (source time)</h3>
      <div className="row">
        <label>Start
          <div className="row">
            <button className="tiny" onClick={() => nudge("s", -0.5)}>−½s</button>
            <input type="number" step={0.1} value={start} onChange={(e) => setStart(clamp(+e.target.value))} />
            <button className="tiny" onClick={() => nudge("s", 0.5)}>+½s</button>
          </div>
        </label>
        <label>End
          <div className="row">
            <button className="tiny" onClick={() => nudge("e", -0.5)}>−½s</button>
            <input type="number" step={0.1} value={end} onChange={(e) => setEnd(clamp(+e.target.value))} />
            <button className="tiny" onClick={() => nudge("e", 0.5)}>+½s</button>
          </div>
        </label>
        <div className="col" style={{ gap: 2 }}>
          <span className="small muted">Length</span>
          <span className="mono">{(end - start).toFixed(1)}s</span>
        </div>
      </div>
      <div className="row small">
        <span className="mono muted">{fmtTime(start, true)} → {fmtTime(end, true)}</span>
        <label className="inline"><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> snap to word edges</label>
        <button className="tiny primary" disabled={!dirty} onClick={() => save(start, end, snap)}>Save boundaries</button>
        {(Math.abs(c.start_s - c.original_start_s) > 1e-3 || Math.abs(c.end_s - c.original_end_s) > 1e-3) && (
          <button className="tiny ghost" onClick={() => save(c.original_start_s, c.original_end_s, false)}>
            Reset to proposed ({fmtTime(c.original_start_s)}–{fmtTime(c.original_end_s)})
          </button>
        )}
      </div>
      {err && <div className="err small">{err}</div>}
      <div className="muted small">Saved boundaries apply on the next render.</div>
    </div>
  );
}

const FEATURE_LABEL: Record<string, string> = {
  hook: "Hook", completeness: "Finished thought", self_contained: "Self-contained", energy: "Energy",
  dynamics: "Dynamics", pace: "Pace", emphasis: "Emphasis", qa: "Q & A", focus: "Topic focus",
  duration_fit: "Length fit", scene_alignment: "Scene cuts", visual_activity: "Visual activity",
  semantic: "Semantic",
};

function WhyPanel({ c }: { c: Candidate }) {
  const feats = Object.entries(c.score_breakdown).filter(([, f]) => (f.weight ?? 1) > 0)
    .sort((a, b) => (b[1].weight ?? 0) * b[1].value - (a[1].weight ?? 0) * a[1].value);
  return (
    <div className="panel col">
      <div className="row between">
        <h3>Why this clip</h3>
        <span className="score" title="heuristic-v1 baseline: structural signals, not semantic understanding">
          {c.score.toFixed(1)}<span className="muted small"> / 100</span>
        </span>
      </div>
      {c.reasons.map((r, i) => <div key={i} className="reason small">{r.text}</div>)}
      {c.warnings.map((w, i) => <div key={i} className="warn small">⚠ {w}</div>)}
      <details>
        <summary className="small muted">Score breakdown (baseline heuristic — it doesn't understand meaning)</summary>
        <div className="col" style={{ gap: 4, marginTop: 8 }}>
          {feats.map(([k, f]) => (
            <div key={k} className="feat" title={f.evidence ?? ""}>
              <span>{FEATURE_LABEL[k] ?? k}</span>
              <div className="bar"><div style={{ width: `${Math.round(f.value * 100)}%`, opacity: 0.35 + 0.65 * Math.min(1, (f.weight ?? 1) / 1.6) }} /></div>
              <span className="muted mono">×{f.weight ?? "–"}</span>
            </div>
          ))}
        </div>
      </details>
      <h3>Transcript</h3>
      <div className="transcript small">{c.transcript_text || "—"}</div>
      <div className="muted small mono">source {fmtTime(c.start_s, true)} – {fmtTime(c.end_s, true)}</div>
    </div>
  );
}

function RectEditor({ label, value, onChange }: { label: string; value: Rect; onChange: (r: Rect) => void }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      <span className="small muted">{label} (fractions of the source frame)</span>
      <div className="row">
        {(["x", "y", "w", "h"] as const).map((k) => (
          <label key={k} className="inline small">{k}
            <input type="number" min={0} max={1} step={0.01} value={value[k]} style={{ width: 70 }}
                   onChange={(e) => onChange({ ...value, [k]: Math.max(0, Math.min(1, +e.target.value)) })} />
          </label>
        ))}
      </div>
    </div>
  );
}

function EditPanel({ c, m, profiles, presets, onSaved }: { c: Candidate; m: Media; profiles: Profile[]; presets: CaptionPreset[]; onSaved: () => void }) {
  const profile = profiles.find((p) => p.key === c.profile_key);
  const base = profile?.config.layout;
  const [profileKey, setProfileKey] = useState(c.profile_key);
  const [presetKey, setPresetKey] = useState(c.caption_preset_key);
  const [layout, setLayout] = useState<Partial<LayoutConfig>>(c.layout_overrides as Partial<LayoutConfig>);
  const [cap, setCap] = useState<Record<string, unknown>>(c.caption_overrides);
  const [title, setTitle] = useState(c.post_title);
  const [tags, setTags] = useState(c.post_hashtags.join(" "));
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const eff = { ...(base ?? {}), ...layout } as LayoutConfig;
  const preset = presets.find((p) => p.key === presetKey)?.style;
  const capEff = { ...(preset ?? {}), ...cap } as Record<string, any>;

  const save = async () => {
    setErr(null);
    setSaved(false);
    try {
      const changedProfile = profileKey !== c.profile_key;
      await api.patchCandidate(c.id, {
        profile_key: profileKey, caption_preset_key: presetKey,
        layout_overrides: changedProfile ? {} : layout, caption_overrides: cap,
        post_title: title, post_hashtags: tags.split(/[\s,]+/).filter(Boolean),
      });
      if (changedProfile) setLayout({});
      setSaved(true);
      onSaved();
    } catch (e) { setErr(errText(e)); }
  };

  return (
    <div className="panel col">
      <h3>Edit</h3>
      <label>Editing profile
        <select value={profileKey} onChange={(e) => setProfileKey(e.target.value)}>
          {profiles.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
        </select>
      </label>
      {profileKey === c.profile_key && base && (
        <>
          <label>Layout
            <select value={eff.kind} onChange={(e) => setLayout({ ...layout, kind: e.target.value as LayoutConfig["kind"] })}>
              <option value="crop">Crop (follows face)</option>
              <option value="fit_blur">Fit whole frame (blurred fill)</option>
              <option value="split_stack">Split: two regions stacked</option>
              <option value="content_facecam">Content + facecam panels</option>
            </select>
          </label>
          {eff.kind === "crop" && (
            <div className="row">
              <label className="inline small">Centre x
                <input type="range" min={0} max={1} step={0.01} value={eff.crop_center_x}
                       onChange={(e) => setLayout({ ...layout, crop_center_x: +e.target.value })} />
              </label>
              <label className="inline small">Zoom
                <input type="number" min={1} max={3} step={0.1} value={eff.zoom} style={{ width: 70 }}
                       onChange={(e) => setLayout({ ...layout, zoom: +e.target.value })} />
              </label>
              <span className="muted small">Face tracking overrides centre x when a face is found.</span>
            </div>
          )}
          {eff.kind === "split_stack" && (
            <>
              <RectEditor label="Top panel region" value={eff.top_rect} onChange={(r) => setLayout({ ...layout, top_rect: r })} />
              <RectEditor label="Bottom panel region" value={eff.bottom_rect} onChange={(r) => setLayout({ ...layout, bottom_rect: r })} />
            </>
          )}
          {eff.kind === "content_facecam" && (
            <>
              <RegionPreview m={m} t={(c.start_s + c.end_s) / 2} rect={eff.facecam_rect} />
              <RectEditor label="Facecam region" value={eff.facecam_rect} onChange={(r) => setLayout({ ...layout, facecam_rect: r })} />
              <div className="row">
                <label className="inline small">Facecam
                  <select value={eff.facecam_position} onChange={(e) => setLayout({ ...layout, facecam_position: e.target.value as "top" | "bottom" })}>
                    <option value="top">top</option><option value="bottom">bottom</option>
                  </select>
                </label>
                <label className="inline small">Content
                  <select value={eff.content_mode} onChange={(e) => setLayout({ ...layout, content_mode: e.target.value as "fit" | "crop" })}>
                    <option value="fit">fit (keep everything)</option><option value="crop">crop (fill)</option>
                  </select>
                </label>
                <label className="inline small">Facecam share
                  <input type="number" min={0.15} max={0.6} step={0.01} value={eff.facecam_share} style={{ width: 70 }}
                         onChange={(e) => setLayout({ ...layout, facecam_share: +e.target.value })} />
                </label>
              </div>
            </>
          )}
        </>
      )}
      <div className="hr" />
      <label>Caption preset
        <select value={presetKey} onChange={(e) => setPresetKey(e.target.value)}>
          {presets.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
        </select>
      </label>
      <div className="row">
        <label className="inline small">Text <input type="color" value={capEff.primary_color ?? "#ffffff"} onChange={(e) => setCap({ ...cap, primary_color: e.target.value.toUpperCase() })} /></label>
        <label className="inline small">Active word <input type="color" value={capEff.highlight_color ?? "#ffe14d"} onChange={(e) => setCap({ ...cap, highlight_color: e.target.value.toUpperCase() })} /></label>
        <label className="inline small">Size <input type="number" min={20} max={220} value={capEff.font_size ?? 84} style={{ width: 70 }} onChange={(e) => setCap({ ...cap, font_size: +e.target.value })} /></label>
        <label className="inline small"><input type="checkbox" checked={!!capEff.uppercase} onChange={(e) => setCap({ ...cap, uppercase: e.target.checked })} /> CAPS</label>
      </div>
      <div className="row">
        <label className="inline small">Font <input value={capEff.font_family ?? "Arial"} style={{ width: 130 }} onChange={(e) => setCap({ ...cap, font_family: e.target.value })} /></label>
        <label className="inline small">Animation
          <select value={capEff.animation ?? "pop"} onChange={(e) => setCap({ ...cap, animation: e.target.value })}>
            <option value="pop">pop</option><option value="color">colour only</option><option value="none">none</option>
          </select>
        </label>
      </div>
      <div className="row">
        <label className="inline small"><input type="checkbox" checked={capEff.position_y != null}
               onChange={(e) => setCap({ ...cap, position_y: e.target.checked ? 0.68 : null })} /> custom height</label>
        {capEff.position_y != null && (
          <input type="range" min={0.2} max={0.75} step={0.01} value={capEff.position_y}
                 onChange={(e) => setCap({ ...cap, position_y: +e.target.value })} />
        )}
        <span className="muted small">{capEff.position_y == null ? `profile default (${profile?.config.captions.position ?? "lower"}), kept inside TikTok/Reels safe zones` : `${Math.round(capEff.position_y * 100)}% from top`}</span>
      </div>
      {Object.keys(cap).length > 0 && <button className="tiny ghost" onClick={() => setCap({})}>Clear caption tweaks</button>}
      <div className="hr" />
      <label>Post title<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Hashtags<input value={tags} onChange={(e) => setTags(e.target.value)} /></label>
      <div className="row">
        <button className="primary" onClick={save}>Save edits</button>
        {saved && <span className="ok small">Saved — click “Render again” to apply.</span>}
      </div>
      {err && <div className="err small">{err}</div>}
    </div>
  );
}

function RegionPreview({ m, t, rect }: { m: Media; t: number; rect: Rect }) {
  if (m.status !== "ready") return null;
  return (
    <div style={{ position: "relative", borderRadius: 6, overflow: "hidden", lineHeight: 0 }}>
      <img src={api.frameUrl(m.id, t, 480)} style={{ width: "100%" }} alt="source frame" />
      <div style={{
        position: "absolute", left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`,
        height: `${rect.h * 100}%`, border: "2px solid var(--accent)", background: "rgba(255,122,69,0.15)",
      }} />
    </div>
  );
}

function ExportBox({ renderId, c }: { renderId: string; c: Candidate }) {
  const settings = useData(() => api.settings(), []);
  const [folder, setFolder] = useState<string>("");
  const [result, setResult] = useState<{ path: string; caption: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (settings.data && !folder) setFolder(settings.data.default_export_dir); }, [settings.data]);

  const run = async () => {
    setErr(null);
    try {
      const r = await api.exportRender(renderId, folder);
      setResult({ path: r.dest_path, caption: String(r.post_metadata.caption ?? "") });
    } catch (e) { setErr(errText(e)); }
  };
  return (
    <div className="panel col">
      <h3>Export</h3>
      <div className="row">
        <input className="mono" style={{ flex: 1 }} value={folder} onChange={(e) => setFolder(e.target.value)} />
        <button className="primary" onClick={run} disabled={c.review_status !== "approved"}
                title={c.review_status !== "approved" ? "Approve the clip first" : ""}>Export MP4 + metadata</button>
      </div>
      {c.review_status !== "approved" && <div className="muted small">Approve the clip to export it.</div>}
      {result && (
        <div className="col small">
          <div className="ok">Saved <span className="mono">{result.path}</span> (+ .json / .txt with the post caption)</div>
          <div className="transcript">{result.caption}</div>
          <button className="tiny" onClick={async () => setCopied(await copyText(result.caption))}>{copied ? "Copied" : "Copy caption"}</button>
        </div>
      )}
      {err && <div className="err small">{err}</div>}
    </div>
  );
}
