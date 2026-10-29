// Mirrors backend/clipforge/api/serialize.py.

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type ReviewStatus = "pending" | "approved" | "rejected";

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  default_profile_key: string;
  export_dir: string | null;
  media_count: number | null;
  created_at: string;
  updated_at: string;
  media?: Media[];
}

export interface Job {
  id: string;
  kind: "transcribe" | "analyze" | "render" | string;
  lane: string;
  status: JobStatus;
  stage: string;
  progress: number;
  attempts: number;
  max_attempts: number;
  error_code: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  payload: Record<string, unknown>;
  cancel_requested: boolean;
  project_id: string | null;
  media_id: string | null;
  candidate_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  events?: { ts: string; level: string; message: string; data: unknown }[];
}

export interface TranscriptInfo {
  id: string;
  engine: string;
  model: string;
  device: string;
  language: string | null;
  status: "partial" | "complete";
  chunks_done: number;
  chunks_total: number;
  word_timestamps: boolean;
}

export interface Media {
  id: string;
  project_id: string;
  file_name: string;
  path: string;
  status: "ready" | "missing" | "invalid";
  file_size: number;
  duration_s: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  container: string;
  has_audio: boolean;
  audio_codec: string | null;
  audio_channels: number | null;
  sample_rate: number | null;
  rotation: number;
  profile_key: string;
  transcript_path: string | null;
  browser_playable: boolean;
  candidate_counts: Partial<Record<ReviewStatus, number>>;
  transcript: TranscriptInfo | null;
  active_jobs: Job[];
  created_at: string;
}

export interface Reason {
  feature: string;
  text: string;
}

export interface Feature {
  value: number;
  weight: number | null;
  evidence: string | null;
}

export interface ExportRec {
  id: string;
  dest_path: string;
  metadata_path: string | null;
  post_metadata: Record<string, unknown>;
  created_at?: string;
}

export interface Render {
  id: string;
  candidate_id: string;
  version: number;
  status: "rendering" | "completed" | "failed" | "cancelled";
  file: string | null;
  start_s: number;
  end_s: number;
  profile_key: string;
  caption_preset_key: string;
  encoder: string;
  width: number;
  height: number;
  duration_s: number | null;
  file_size: number | null;
  error: string | null;
  validation: Record<string, unknown>;
  settings: Record<string, unknown>;
  job_id: string | null;
  exports: ExportRec[];
  created_at: string;
}

export interface Candidate {
  id: string;
  media_id: string;
  rank: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  original_start_s: number;
  original_end_s: number;
  transcript_text: string;
  score: number;
  score_breakdown: Record<string, Feature>;
  reasons: Reason[];
  warnings: string[];
  profile_key: string;
  caption_preset_key: string;
  layout_overrides: Record<string, unknown>;
  caption_overrides: Record<string, unknown>;
  review_status: ReviewStatus;
  reviewed_at: string | null;
  post_title: string;
  post_hashtags: string[];
  renders: Render[];
  latest_render_id: string | null;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface LayoutConfig {
  kind: "crop" | "fit_blur" | "split_stack" | "content_facecam";
  crop_center_x: number;
  zoom: number;
  top_rect: Rect;
  bottom_rect: Rect;
  facecam_rect: Rect;
  content_rect: Rect;
  facecam_position: "top" | "bottom";
  facecam_share: number;
  content_mode: "fit" | "crop";
}

export interface Profile {
  key: string;
  name: string;
  builtin: boolean;
  config: {
    description: string;
    content_type: string;
    duration: { min_s: number; target_s: number; max_s: number; hard_max_s: number };
    layout: LayoutConfig;
    captions: { preset_key: string; position: string; avoid_faces: boolean };
    scoring: { weights: Record<string, number> };
  };
}

export interface CaptionStyle {
  font_family: string;
  bold: boolean;
  font_size: number;
  uppercase: boolean;
  primary_color: string;
  highlight_color: string;
  outline_color: string;
  outline_px: number;
  box: boolean;
  highlight_mode: "word" | "progressive";
  animation: "pop" | "color" | "none";
  position_y: number | null;
  [k: string]: unknown;
}

export interface CaptionPreset {
  key: string;
  name: string;
  builtin: boolean;
  style: CaptionStyle;
}

export interface FsEntry {
  name: string;
  path: string;
  kind: "dir" | "video" | "transcript";
  size?: number;
}

export interface Prefs {
  transcription_engine: string | null;
  whisper_model: string;
  whisper_device: "auto" | "cuda" | "cpu";
  whisper_compute_type: string;
  whisper_language: string | null;
  encoder_preference: string;
  output_width: number;
  output_height: number;
  default_export_dir: string;
  deep_validate: boolean;
}
