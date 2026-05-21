// Python sidecar / Rust 핸들러와 미러링되는 타입.
// 변경 시 sidecar.py / settings.rs도 함께 갱신.

export type Quality =
  | "144p" | "240p" | "360p" | "480p" | "720p"
  | "1080p" | "1440p" | "2160p" | "best" | "audio";

export type CodecPref = "auto" | "h264" | "vp9" | "av1";
export type AudioPref = "best" | "m4a" | "opus";
export type Container = "mp4" | "mkv" | "webm";
export type PlaylistMode = "auto" | "expand" | "single";

export interface Settings {
  quality: Quality;
  codec: CodecPref;
  audio: AudioPref;
  container: Container;
  output_dir: string;
  embed_subs: boolean;
  sub_langs: string; // CSV
  cookies_from_browser: string; // "" | "chrome" | "firefox" | ...
  playlist_mode: PlaylistMode;
  max_downloads: string; // 빈 문자열 = 제한 없음
  playlist_items: string;
  socket_timeout: number;
  retries: number;
  fragment_retries: number;
  throttled_rate: string; // 빈 문자열 = 비활성
  _source?: string | null;
}

export const DEFAULT_SETTINGS: Settings = {
  quality: "1080p",
  codec: "auto",
  audio: "best",
  container: "mp4",
  output_dir: "downloads",
  embed_subs: true,
  sub_langs: "ko,en",
  cookies_from_browser: "",
  playlist_mode: "auto",
  max_downloads: "",
  playlist_items: "",
  socket_timeout: 30,
  retries: 10,
  fragment_retries: 10,
  throttled_rate: "100K",
};

// ── 이벤트 (Python → Rust → Webview) ──────────────────────────────────────
export interface MetaEvent {
  event: "meta";
  job_id: string;
  title: string;
  channel: string;
  duration: string;
  video_id: string;
  thumbnail: string;
}

export interface ProgressEvent {
  event: "progress";
  job_id: string;
  phase: "download" | "postprocess" | "finished" | string;
  percent: number | null;
  downloaded: number | null;
  total: number | null;
  speed: number | null;
  eta: number | null;
  filename: string | null;
}

export interface DoneEvent {
  event: "done";
  job_id: string;
  filepath: string;
}

export interface ErrorEvent {
  event: "error";
  job_id?: string;
  category: string;
  message: string;
}

export interface LogEvent {
  event: "log";
  job_id?: string;
  level: "info" | "warn" | "error" | "stderr" | "raw" | string;
  line: string;
}

export interface ReadyEvent {
  event: "ready";
  yt_dlp: string;
  version: string;
  deno_dir: string | null;
  defaults: Settings;
}

export type JobStatus =
  | "queued"
  | "probing"
  | "downloading"
  | "postprocess"
  | "done"
  | "failed"
  | "cancelled";

export interface Job {
  id: string;
  url: string;
  status: JobStatus;
  title?: string;
  channel?: string;
  duration?: string;
  video_id?: string;
  thumbnail?: string;
  percent?: number | null;
  speed?: number | null;
  eta?: number | null;
  downloaded?: number | null;
  total?: number | null;
  filepath?: string;
  error?: { category: string; message: string };
  startedAt: number;
  finishedAt?: number;
}

export interface LibraryItem {
  job_id: string;
  url: string;
  title: string;
  channel: string;
  duration: string;
  video_id: string;
  thumbnail: string;
  filepath: string;
  finished_at: number;
}

export interface Library {
  version: number;
  items: LibraryItem[];
}
