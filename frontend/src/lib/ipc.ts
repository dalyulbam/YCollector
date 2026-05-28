import type {
  DoneEvent,
  ErrorEvent,
  Library,
  LogEvent,
  MetaEvent,
  ProgressEvent,
  ReadyEvent,
  Settings,
} from "./types";

// Tauri 네이티브 기능(폴더 선택, 파일 열기)만 Tauri IPC 사용.
// 나머지는 모두 FastAPI HTTP API.
const IN_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let _base = "";

export function setApiBase(url: string) {
  _base = url.replace(/\/$/, "");
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${_base}${path}`, init);
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}

// ── 이벤트 버스 ───────────────────────────────────────────────────────────
type Listener<T> = (e: T) => void;
const _bus = new Map<string, Set<Listener<unknown>>>();

function dispatch(event: string, payload: unknown) {
  _bus.get(event)?.forEach((cb) => cb(payload));
}

function on<T>(event: string, cb: Listener<T>): Promise<() => void> {
  let set = _bus.get(event);
  if (!set) {
    set = new Set();
    _bus.set(event, set);
  }
  set.add(cb as Listener<unknown>);
  return Promise.resolve(() => {
    set!.delete(cb as Listener<unknown>);
  });
}

// ── SSE: 잡별 이벤트 스트림 → 글로벌 버스 ─────────────────────────────────
function subscribeJob(jobId: string) {
  const es = new EventSource(`${_base}/api/jobs/${jobId}/events`);
  es.onmessage = (ev) => {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    const e = msg.event as string;

    if (e === "meta") {
      dispatch("job:meta", {
        event: "meta",
        job_id: jobId,
        title: String(msg.title ?? ""),
        channel: String(msg.channel ?? ""),
        duration: String(msg.duration ?? ""),
        video_id: String(msg.video_id ?? ""),
        thumbnail: String(msg.thumbnail ?? ""),
      } satisfies MetaEvent);
    } else if (e === "progress") {
      const progress = msg.progress as number | null;
      dispatch("job:progress", {
        event: "progress",
        job_id: jobId,
        phase:
          msg.status === "downloading"
            ? "download"
            : msg.status === "postprocess"
              ? "postprocess"
              : String(msg.status ?? "download"),
        percent: typeof progress === "number" ? progress * 100 : null,
        downloaded: (msg.downloaded as number) ?? null,
        total: (msg.total as number) ?? null,
        speed: (msg.speed as number) ?? null,
        eta: (msg.eta as number) ?? null,
        filename: (msg.filename as string) ?? null,
      } satisfies ProgressEvent);
    } else if (e === "status") {
      dispatch("job:progress", {
        event: "progress",
        job_id: jobId,
        phase: String(msg.status ?? "download"),
        percent: null,
        downloaded: null,
        total: null,
        speed: null,
        eta: null,
        filename: null,
      } satisfies ProgressEvent);
    } else if (e === "done") {
      dispatch("job:done", {
        event: "done",
        job_id: jobId,
        filepath: String(msg.out_path ?? ""),
      } satisfies DoneEvent);
      es.close();
    } else if (e === "error") {
      dispatch("job:error", {
        event: "error",
        job_id: jobId,
        category: String(msg.category ?? "unknown"),
        message: String(msg.message ?? ""),
      } satisfies ErrorEvent);
      es.close();
    } else if (e === "cancelled") {
      dispatch("job:error", {
        event: "error",
        job_id: jobId,
        category: "cancelled",
        message: "사용자가 취소함",
      } satisfies ErrorEvent);
      es.close();
    }
  };
  es.onerror = () => {
    es.close();
  };
}

// ── 커맨드 ────────────────────────────────────────────────────────────────
function settingsPayload(s: Settings): Record<string, unknown> {
  const { _source, ...rest } = s;
  return rest;
}

export const ipc = {
  probe: (_url: string) => Promise.resolve(""),

  startJob: async (url: string, settings: Settings): Promise<string> => {
    const r = await api<{ job_id: string }>("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, settings: settingsPayload(settings) }),
    });
    subscribeJob(r.job_id);
    return r.job_id;
  },

  cancel: async (jobId: string): Promise<void> => {
    await api<{ ok: boolean }>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  },

  loadSettings: async (): Promise<Settings> => {
    return api<Settings>("/api/settings");
  },

  saveSettings: async (settings: Settings): Promise<void> => {
    await api<{ ok: boolean }>("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settingsPayload(settings)),
    });
  },

  pickFolder: async (start?: string): Promise<string | null> => {
    if (IN_TAURI) {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<string | null>("pick_folder", { start: start ?? null });
    }
    const picked = window.prompt("출력 폴더:", start || "downloads");
    return picked || null;
  },

  openPath: async (path: string): Promise<void> => {
    if (IN_TAURI) {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<void>("open_path", { path });
    }
    console.info("[ipc] open_path:", path);
  },

  listLibrary: async (): Promise<Library> => {
    return api<Library>("/api/library");
  },
};

// ── 이벤트 구독 헬퍼 ──────────────────────────────────────────────────────
export type Unsub = () => void;

export function onReady(cb: (e: ReadyEvent) => void): Promise<Unsub> {
  return on("job:ready", cb);
}
export function onMeta(cb: (e: MetaEvent) => void): Promise<Unsub> {
  return on("job:meta", cb);
}
export function onProgress(cb: (e: ProgressEvent) => void): Promise<Unsub> {
  return on("job:progress", cb);
}
export function onDone(cb: (e: DoneEvent) => void): Promise<Unsub> {
  return on("job:done", cb);
}
export function onError(cb: (e: ErrorEvent) => void): Promise<Unsub> {
  return on("job:error", cb);
}
export function onLog(cb: (e: LogEvent) => void): Promise<Unsub> {
  return on("job:log", cb);
}

// 초기화: Tauri면 서버 URL 취득 후 설정 로드. 아니면 same-origin으로.
async function _init() {
  if (IN_TAURI) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const base = await invoke<string>("get_api_base");
      setApiBase(base);
    } catch {
      // fallback: same-origin
    }
  }

  // 서버 준비 대기 (최대 ~5초).
  for (let i = 0; i < 10; i++) {
    try {
      const settings = await ipc.loadSettings();
      dispatch("job:ready", {
        event: "ready",
        yt_dlp: "",
        version: "",
        deno_dir: null,
        defaults: settings,
      } satisfies ReadyEvent);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}

if (typeof window !== "undefined") {
  setTimeout(_init, 100);
}
