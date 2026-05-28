import * as React from "react";
import { ClipboardPaste, Download, Loader2, ListVideo } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ipc } from "@/lib/ipc";
import { useJobs } from "@/store/jobs";
import type { Settings } from "@/lib/types";

interface Props {
  settings: Settings;
}

const URL_RE = /^https?:\/\/(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)\//i;

export function UrlPasteBar({ settings }: Props) {
  const [value, setValue] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [playlistAll, setPlaylistAll] = React.useState(false);
  const addJob = useJobs((s) => s.addJob);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const submit = React.useCallback(async () => {
    const url = value.trim();
    if (!url) return;
    if (!URL_RE.test(url)) {
      // 부드러운 검증 — 비 YouTube URL도 yt-dlp가 지원할 수 있으니 막진 않고 경고만.
      // (yt-dlp는 1000+ 사이트 지원)
    }
    const effective: Settings = playlistAll
      ? { ...settings, playlist_mode: "expand" }
      : settings;
    setBusy(true);
    try {
      const jobId = await ipc.startJob(url, effective);
      addJob(jobId, url);
      setValue("");
      inputRef.current?.focus();
    } catch (e) {
      console.error("startJob 실패:", e);
    } finally {
      setBusy(false);
    }
  }, [value, settings, playlistAll, addJob]);

  const pasteFromClipboard = React.useCallback(async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setValue(text.trim());
        inputRef.current?.focus();
      }
    } catch (e) {
      console.warn("clipboard 읽기 실패:", e);
    }
  }, []);

  return (
    <div className="flex gap-2 w-full">
      <Input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
        placeholder="YouTube URL 붙여넣기 (Enter = 추가)"
        className="font-mono text-sm"
        autoFocus
      />
      <Button
        type="button"
        variant={playlistAll ? "default" : "outline"}
        size="icon"
        onClick={() => setPlaylistAll((v) => !v)}
        title={playlistAll ? "재생목록 전체 받기 (ON)" : "단일 영상만 (OFF)"}
        aria-pressed={playlistAll}
      >
        <ListVideo className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="outline"
        size="icon"
        onClick={pasteFromClipboard}
        title="클립보드에서 붙여넣기"
      >
        <ClipboardPaste className="h-4 w-4" />
      </Button>
      <Button type="button" onClick={submit} disabled={busy || !value.trim()}>
        {busy ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            추가 중…
          </>
        ) : (
          <>
            <Download className="h-4 w-4" />
            다운로드
          </>
        )}
      </Button>
    </div>
  );
}
