import type {
  Candidate, CaptionPreset, ExportRec, FsEntry, Job, Media, Prefs, Profile, Project, Render,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public hint?: string | null) {
    super(message);
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let code = `HTTP_${res.status}`, message = res.statusText, hint: string | null = null;
    try {
      const data = await res.json();
      if (data.error) ({ code, message, hint } = data.error);
      else if (data.detail) message = typeof data.detail === "string" ? data.detail
        : data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, code, message, hint);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const get = <T,>(u: string) => req<T>("GET", u);
const post = <T,>(u: string, b: unknown = {}) => req<T>("POST", u, b);
const enc = encodeURIComponent;

export const api = {
  health: () => get<{ ok: boolean; version: string; worker: string }>("/api/health"),
  diagnostics: (refresh = false) => get<Record<string, any>>(`/api/system/diagnostics?refresh=${refresh}`),
  settings: () => get<Prefs>("/api/settings"),
  saveSettings: (p: Partial<Prefs>) => req<Prefs>("PUT", "/api/settings", p),
  fsList: (path?: string) =>
    get<{ path: string | null; parent: string | null; entries: FsEntry[] }>(
      `/api/fs/list${path ? `?path=${enc(path)}` : ""}`),

  profiles: () => get<Profile[]>("/api/profiles"),
  captionPresets: () => get<CaptionPreset[]>("/api/caption-presets"),

  projects: () => get<Project[]>("/api/projects"),
  project: (id: string) => get<Project>(`/api/projects/${id}`),
  createProject: (b: { name: string; description?: string; default_profile_key?: string }) =>
    post<Project>("/api/projects", b),
  patchProject: (id: string, b: Partial<Project>) => req<Project>("PATCH", `/api/projects/${id}`, b),

  importMedia: (projectId: string, b: { path: string; profile_key?: string; transcript_path?: string | null }) =>
    post<Media & { job_ids: string[] }>(`/api/projects/${projectId}/media`, b),
  media: (id: string) => get<Media>(`/api/media/${id}`),
  candidates: (mediaId: string) => get<Candidate[]>(`/api/media/${mediaId}/candidates`),
  transcribe: (mediaId: string, force = false) => post<Job>(`/api/media/${mediaId}/transcribe?force=${force}`),
  analyze: (mediaId: string, profile_key?: string) => post<Job>(`/api/media/${mediaId}/analyze`, { profile_key }),
  relink: (mediaId: string, path: string, force = false) => post<Media>(`/api/media/${mediaId}/relink`, { path, force }),
  sourceUrl: (mediaId: string) => `/api/media/${mediaId}/source`,
  frameUrl: (mediaId: string, t: number, w = 480) => `/api/media/${mediaId}/frame?t=${t.toFixed(2)}&w=${w}`,

  patchCandidate: (id: string, patch: Record<string, unknown>) => req<Candidate>("PATCH", `/api/candidates/${id}`, patch),
  review: (id: string, action: "approve" | "reject" | "reset") =>
    post<Candidate & { render_job_id: string | null }>(`/api/candidates/${id}/review`, { action }),
  render: (id: string) => post<Job>(`/api/candidates/${id}/render`),
  renderUrl: (id: string) => `/api/renders/${id}/video`,
  exportRender: (id: string, folder?: string) =>
    post<ExportRec>(`/api/renders/${id}/export`, { folder: folder || null }),
  renderInfo: (id: string) => get<Render>(`/api/renders/${id}`),

  jobs: (q: { status?: string; media_id?: string; project_id?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]));
    return get<Job[]>(`/api/jobs?${qs}`);
  },
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: string) => post<Job & { requeued: boolean }>(`/api/jobs/${id}/retry`),
};
