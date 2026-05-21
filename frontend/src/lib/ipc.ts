import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen, type UnlistenFn } from "@tauri-apps/api/event";
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
import { IN_TAURI, mockInvoke, mockListen } from "./mock-ipc";

// Tauri 환경이면 진짜 IPC, 아니면 mock. Vite dev 서버를 평범한 브라우저로 띄울 때
// 자동으로 mock으로 떨어진다 (UI 흐름 시각 검증용).
async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (IN_TAURI) return tauriInvoke<T>(cmd, args);
  return mockInvoke<T>(cmd, args);
}

function listen<T>(
  event: string,
  cb: (ev: { payload: T }) => void,
): Promise<UnlistenFn> {
  if (IN_TAURI) return tauriListen<T>(event, cb);
  return mockListen(event, cb as (e: { payload: unknown }) => void) as Promise<UnlistenFn>;
}

if (!IN_TAURI && typeof window !== "undefined") {
  // 한 번만 콘솔에 안내.
  // eslint-disable-next-line no-console
  console.info(
    "%c[YCollector] Tauri 런타임 미감지 — mock IPC로 동작합니다 (UI 미리보기용).",
    "color:#06b6d4;font-weight:600",
  );
}

// ── 커맨드 ────────────────────────────────────────────────────────────────
export const ipc = {
  probe: (url: string) => invoke<string>("probe", { url }),
  startJob: (url: string, settings: Settings) =>
    invoke<string>("start_job", { url, settings }),
  cancel: (jobId: string) => invoke<void>("cancel", { jobId }),
  loadSettings: () => invoke<Settings>("load_settings"),
  saveSettings: (settings: Settings) => invoke<void>("save_settings", { settings }),
  pickFolder: (start?: string) =>
    invoke<string | null>("pick_folder", { start: start ?? null }),
  openPath: (path: string) => invoke<void>("open_path", { path }),
  listLibrary: () => invoke<Library>("list_library"),
};

// ── 이벤트 구독 헬퍼 (UnlistenFn 모음) ────────────────────────────────────
export type Unsub = UnlistenFn;

export function onReady(cb: (e: ReadyEvent) => void): Promise<Unsub> {
  return listen<ReadyEvent>("job:ready", (ev) => cb(ev.payload));
}
export function onMeta(cb: (e: MetaEvent) => void): Promise<Unsub> {
  return listen<MetaEvent>("job:meta", (ev) => cb(ev.payload));
}
export function onProgress(cb: (e: ProgressEvent) => void): Promise<Unsub> {
  return listen<ProgressEvent>("job:progress", (ev) => cb(ev.payload));
}
export function onDone(cb: (e: DoneEvent) => void): Promise<Unsub> {
  return listen<DoneEvent>("job:done", (ev) => cb(ev.payload));
}
export function onError(cb: (e: ErrorEvent) => void): Promise<Unsub> {
  return listen<ErrorEvent>("job:error", (ev) => cb(ev.payload));
}
export function onLog(cb: (e: LogEvent) => void): Promise<Unsub> {
  return listen<LogEvent>("job:log", (ev) => cb(ev.payload));
}
