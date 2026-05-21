import * as React from "react";
import { Moon, Settings as SettingsIcon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UrlPasteBar } from "@/components/UrlPasteBar";
import { SettingsSheet } from "@/components/SettingsSheet";
import { JobList } from "@/components/JobList";
import { LibraryTab } from "@/components/LibraryTab";
import {
  ipc,
  onDone,
  onError,
  onLog,
  onMeta,
  onProgress,
  onReady,
  type Unsub,
} from "@/lib/ipc";
import { DEFAULT_SETTINGS, type Settings } from "@/lib/types";
import { useJobs } from "@/store/jobs";

export default function App() {
  const [settings, setSettings] = React.useState<Settings>(DEFAULT_SETTINGS);
  const [ready, setReady] = React.useState<{
    yt_dlp?: string;
    deno_dir?: string | null;
  }>({});
  const [dark, setDark] = React.useState(
    () => document.documentElement.classList.contains("dark"),
  );
  const updateJob = useJobs((s) => s.updateJob);
  const setStatus = useJobs((s) => s.setStatus);
  const appendLog = useJobs((s) => s.appendLog);

  // 초기 설정 로드.
  React.useEffect(() => {
    void ipc
      .loadSettings()
      .then((s) => setSettings({ ...DEFAULT_SETTINGS, ...s }))
      .catch((e) => console.warn("load_settings 실패:", e));
  }, []);

  // sidecar 이벤트 구독.
  React.useEffect(() => {
    const unsubs: Unsub[] = [];
    let cancelled = false;

    (async () => {
      const subs = await Promise.all([
        onReady((e) => {
          setReady({ yt_dlp: e.yt_dlp, deno_dir: e.deno_dir });
          if (e.defaults) setSettings((s) => ({ ...s, ...e.defaults }));
        }),
        onMeta((e) => {
          updateJob(e.job_id, {
            title: e.title,
            channel: e.channel,
            duration: e.duration,
            video_id: e.video_id,
            thumbnail: e.thumbnail || undefined,
          });
        }),
        onProgress((e) => {
          if (e.phase === "download") setStatus(e.job_id, "downloading");
          else if (e.phase === "postprocess") setStatus(e.job_id, "postprocess");
          updateJob(e.job_id, {
            percent: e.percent ?? undefined,
            downloaded: e.downloaded ?? undefined,
            total: e.total ?? undefined,
            speed: e.speed ?? undefined,
            eta: e.eta ?? undefined,
          });
        }),
        onDone((e) => {
          updateJob(e.job_id, { filepath: e.filepath });
          setStatus(e.job_id, "done");
        }),
        onError((e) => {
          if (e.job_id) {
            updateJob(e.job_id, { error: { category: e.category, message: e.message } });
            setStatus(e.job_id, "failed");
          }
        }),
        onLog((e) => appendLog(e.line, e.level, e.job_id)),
      ]);
      if (!cancelled) unsubs.push(...subs);
    })();

    return () => {
      cancelled = true;
      unsubs.forEach((u) => u());
    };
  }, [updateJob, setStatus, appendLog]);

  const toggleDark = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  return (
    <div className="h-full flex flex-col">
      {/* Topbar */}
      <header className="sticky top-0 z-30 bg-background/80 backdrop-blur border-b">
        <div className="max-w-5xl mx-auto px-6 py-3 flex items-center gap-4">
          <div className="font-semibold tracking-tight">YCollector</div>
          {ready.yt_dlp && (
            <span className="text-[11px] font-mono text-muted-foreground">
              yt-dlp {ready.yt_dlp}
            </span>
          )}
          <div className="flex-1" />
          <Button variant="ghost" size="icon" onClick={toggleDark} title="다크 모드 토글">
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
          <SettingsSheet
            settings={settings}
            onChange={setSettings}
            trigger={
              <Button variant="outline" size="sm">
                <SettingsIcon className="h-4 w-4" />
                설정
              </Button>
            }
          />
        </div>
        <div className="max-w-5xl mx-auto px-6 pb-3">
          <UrlPasteBar settings={settings} />
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-4">
          <Tabs defaultValue="queue">
            <TabsList>
              <TabsTrigger value="queue">큐</TabsTrigger>
              <TabsTrigger value="library">라이브러리</TabsTrigger>
            </TabsList>
            <TabsContent value="queue">
              <JobList settings={settings} />
            </TabsContent>
            <TabsContent value="library">
              <LibraryTab />
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  );
}
