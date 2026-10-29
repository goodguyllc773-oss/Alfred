import { useState } from "react";
import { api } from "../api";
import { errText, go, useData } from "../util";

export default function Library() {
  const projects = useData(() => api.projects(), []);
  const profiles = useData(() => api.profiles(), []);
  const [name, setName] = useState("");
  const [profile, setProfile] = useState("conversation");
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    setErr(null);
    try {
      const p = await api.createProject({ name, default_profile_key: profile });
      go(`/p/${p.id}`);
    } catch (e) {
      setErr(errText(e));
    }
  };

  return (
    <div className="col" style={{ gap: 20 }}>
      <div className="row between">
        <h1>Projects</h1>
      </div>
      <div className="panel row">
        <input placeholder="New project name (e.g. a creator or show)" value={name}
               onChange={(e) => setName(e.target.value)} style={{ flex: 1, minWidth: 220 }}
               onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} />
        <label className="inline">Default profile
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            {(profiles.data ?? []).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
        </label>
        <button className="primary" disabled={!name.trim()} onClick={create}>Create project</button>
        {err && <div className="err small" style={{ width: "100%" }}>{err}</div>}
      </div>
      {projects.error && <div className="panel err">{projects.error}</div>}
      {projects.data && projects.data.length === 0 && (
        <div className="panel empty">No projects yet. Create one, then import a long video into it.</div>
      )}
      <div className="grid">
        {(projects.data ?? []).map((p) => (
          <div key={p.id} className="panel card col" onClick={() => go(`/p/${p.id}`)}>
            <h2>{p.name}</h2>
            <div className="muted small">{p.media_count ?? 0} video(s) · default profile {p.default_profile_key}</div>
            <div className="muted small">updated {new Date(p.updated_at).toLocaleString()}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
