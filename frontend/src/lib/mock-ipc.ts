// 브라우저(Tauri 없음)에서 React UI를 테스트하기 위한 mock IPC.
//
// Tauri 런타임이 주입한 `window.__TAURI_INTERNALS__` 가 없으면
// ipc.ts 가 이 mock으로 fallback. UI 흐름(URL 붙여넣기 → 진행률 → 완료)을
// 실제 yt-dlp 없이도 시각적으로 확인할 수 있다.
//
// 운영 빌드(Tauri webview)에선 이 모듈도 import 되지만 분기에서 사용되지 않음.

import { DEFAULT_SETTINGS, type Library, type LibraryItem, type Settings } from "./types";

export const IN_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

// ── 가짜 데이터 풀 ─────────────────────────────────────────────────────────
const FAKE_METAS = [
  {
    title: "Lo-fi hip hop radio — beats to relax/study to",
    channel: "Lofi Girl",
    duration: "1:23:45",
    video_id: "demo01",
  },
  {
    title: "How NDJSON works in 90 seconds",
    channel: "DevExplains",
    duration: "1:32",
    video_id: "demo02",
  },
  {
    title: "Tauri 2.0 vs Electron — 실측 비교",
    channel: "Rust Korea",
    duration: "12:08",
    video_id: "demo03",
  },
  {
    title: "한국어 자막 임베드 데모 — 자막 언어 ko,en",
    channel: "데모 채널",
    duration: "0:42",
    video_id: "demo04",
  },
];

// 인라인 SVG 썸네일 (외부 네트워크 요청 회피).
function placeholderThumb(label: string, hue: number): string {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'>
    <defs>
      <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
        <stop offset='0%' stop-color='hsl(${hue} 70% 55%)'/>
        <stop offset='100%' stop-color='hsl(${(hue + 60) % 360} 70% 35%)'/>
      </linearGradient>
    </defs>
    <rect width='320' height='180' fill='url(#g)'/>
    <text x='160' y='100' text-anchor='middle' fill='white' font-family='Inter,sans-serif' font-weight='700' font-size='18'>${label}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

const FAKE_THUMBS = [
  placeholderThumb("Lo-fi", 240),
  placeholderThumb("NDJSON", 180),
  placeholderThumb("Tauri vs Electron", 30),
  placeholderThumb("ko subs demo", 320),
];

// 라이브러리 초기 데이터(2개 — 라이브러리 탭이 비어보이지 않게).
const INITIAL_LIBRARY: LibraryItem[] = [
  {
    job_id: "lib-seed-1",
    url: "https://www.youtube.com/watch?v=seed1",
    title: "데모 — 이미 받아둔 영상 1",
    channel: "Seed Channel",
    duration: "4:21",
    video_id: "seed1",
    thumbnail: placeholderThumb("seed 1", 140),
    filepath: "C:\\Users\\Demo\\Videos\\seed1.mp4",
    finished_at: Date.now() - 86400_000,
  },
  {
    job_id: "lib-seed-2",
    url: "https://www.youtube.com/watch?v=seed2",
    title: "데모 — 이미 받아둔 영상 2 (한국어 제목)",
    channel: "다른 채널",
    duration: "11:07",
    video_id: "seed2",
    thumbnail: placeholderThumb("seed 2", 280),
    filepath: "C:\\Users\\Demo\\Videos\\seed2.mp4",
    finished_at: Date.now() - 3600_000,
  },
];

// ── 가짜 settings (in-memory) ─────────────────────────────────────────────
let mockSettings: Settings = {
  ...DEFAULT_SETTINGS,
  _source: "(mock — 브라우저 미리보기)",
};
const mockLibrary: LibraryItem[] = [...INITIAL_LIBRARY];

// ── 이벤트 버스 ───────────────────────────────────────────────────────────
type AnyCb = (payload: unknown) => void;
const bus = new Map<string, Set<AnyCb>>();

function emit(event: string, payload: unknown) {
  bus.get(event)?.forEach((cb) => cb(payload));
}

export function mockListen(
  event: string,
  cb: (ev: { payload: unknown }) => void,
): Promise<() => void> {
  let set = bus.get(event);
  if (!set) {
    set = new Set();
    bus.set(event, set);
  }
  const wrapped: AnyCb = (payload) => cb({ payload });
  set.add(wrapped);
  return Promise.resolve(() => {
    set!.delete(wrapped);
  });
}

// ── ready 이벤트 1회 (앱 첫 listen 시점에) ─────────────────────────────────
let readyEmitted = false;
function maybeEmitReady() {
  if (readyEmitted) return;
  // 다음 tick에 — App 컴포넌트가 listen()을 다 걸어둘 시간을 줌.
  setTimeout(() => {
    readyEmitted = true;
    emit("job:ready", {
      event: "ready",
      yt_dlp: "0.0.0-mock",
      version: "0.1.0-mock",
      deno_dir: null,
      defaults: mockSettings,
    });
  }, 150);
}

// ── 가짜 다운로드 시뮬레이터 ──────────────────────────────────────────────
let jobCounter = 0;
function nextJobId(): string {
  jobCounter += 1;
  return `mock_${Date.now()}_${jobCounter}`;
}

function fakeMetaFor(seed: number) {
  return FAKE_METAS[seed % FAKE_METAS.length];
}
function fakeThumbFor(seed: number) {
  return FAKE_THUMBS[seed % FAKE_THUMBS.length];
}

function simulateDownload(jobId: string, url: string) {
  const seed = url.length + jobCounter;
  const meta = fakeMetaFor(seed);

  // 1) meta 발사 (350ms 후)
  setTimeout(() => {
    emit("job:meta", {
      event: "meta",
      job_id: jobId,
      ...meta,
      thumbnail: fakeThumbFor(seed),
    });
  }, 350);

  // 2) 진행률 step 6개 (총 ~3.5s).
  const steps = [
    { p: 8, after: 700 },
    { p: 27, after: 1200 },
    { p: 51, after: 1800 },
    { p: 74, after: 2400 },
    { p: 92, after: 3000 },
    { p: 100, after: 3400 },
  ];
  for (const s of steps) {
    setTimeout(() => {
      if (cancelled.has(jobId)) return;
      emit("job:progress", {
        event: "progress",
        job_id: jobId,
        phase: s.p < 100 ? "download" : "postprocess",
        percent: s.p,
        downloaded: Math.floor((s.p / 100) * 42_000_000),
        total: 42_000_000,
        speed: 3_300_000 + (s.p % 7) * 100_000,
        eta: Math.max(0, Math.round((100 - s.p) / 4)),
        filename: null,
      });
    }, s.after);
  }

  // 3) done (3.6s 후)
  setTimeout(() => {
    if (cancelled.has(jobId)) {
      emit("job:error", {
        event: "error",
        job_id: jobId,
        category: "cancelled",
        message: "사용자가 취소함 (mock)",
      });
      cancelled.delete(jobId);
      return;
    }
    const filepath = `C:\\Users\\Demo\\Videos\\${meta.video_id}.${mockSettings.container}`;
    emit("job:done", {
      event: "done",
      job_id: jobId,
      filepath,
    });
    // 라이브러리 누적.
    mockLibrary.unshift({
      job_id: jobId,
      url,
      title: meta.title,
      channel: meta.channel,
      duration: meta.duration,
      video_id: meta.video_id,
      thumbnail: fakeThumbFor(seed),
      filepath,
      finished_at: Date.now(),
    });
  }, 3600);
}

const cancelled = new Set<string>();

// ── invoke handler ────────────────────────────────────────────────────────
export async function mockInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  maybeEmitReady();
  switch (cmd) {
    case "probe": {
      const jobId = nextJobId();
      const url = (args?.url as string) || "";
      const seed = url.length;
      setTimeout(() => {
        emit("job:meta", {
          event: "meta",
          job_id: jobId,
          ...fakeMetaFor(seed),
          thumbnail: fakeThumbFor(seed),
        });
      }, 250);
      return jobId as unknown as T;
    }
    case "start_job": {
      const jobId = nextJobId();
      const url = (args?.url as string) || "";
      simulateDownload(jobId, url);
      return jobId as unknown as T;
    }
    case "cancel": {
      cancelled.add((args?.jobId as string) || "");
      return undefined as unknown as T;
    }
    case "load_settings": {
      return mockSettings as unknown as T;
    }
    case "save_settings": {
      mockSettings = { ...mockSettings, ...(args?.settings as Settings) };
      return undefined as unknown as T;
    }
    case "pick_folder": {
      // 브라우저엔 네이티브 폴더 다이얼로그가 없음 — prompt로 대체.
      const picked = window.prompt(
        "[Mock] 출력 폴더 (브라우저 미리보기에선 입력만 가능):",
        (args?.start as string) || "C:\\Users\\Demo\\Videos",
      );
      return (picked || null) as unknown as T;
    }
    case "open_path": {
      // 브라우저에선 파일 시스템 접근 불가 — 콘솔로.
      console.info("[mock] open_path:", args?.path);
      return undefined as unknown as T;
    }
    case "list_library": {
      const lib: Library = { version: 1, items: mockLibrary };
      return lib as unknown as T;
    }
    default:
      console.warn("[mock] unknown invoke:", cmd, args);
      throw new Error(`unknown command: ${cmd}`);
  }
}
