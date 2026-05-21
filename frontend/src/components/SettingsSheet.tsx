import * as React from "react";
import { FolderOpen, Save, RotateCcw } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  SheetContent,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Switch } from "@/components/ui/switch";
import { ipc } from "@/lib/ipc";
import {
  DEFAULT_SETTINGS,
  type Settings,
  type Quality,
  type Container,
} from "@/lib/types";

const QUALITIES: Quality[] = [
  "144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p", "best", "audio",
];
const CONTAINERS: Container[] = ["mp4", "mkv", "webm"];

interface Props {
  settings: Settings;
  onChange: (next: Settings) => void;
  /** SettingsSheet가 자체 트리거 버튼을 렌더링 — 외부에서 useState로 토글하기보다 단순. */
  trigger: React.ReactNode;
}

export function SettingsSheet({ settings, onChange, trigger }: Props) {
  const [draft, setDraft] = React.useState<Settings>(settings);
  const [open, setOpen] = React.useState(false);
  const [advancedOpen, setAdvancedOpen] = React.useState(false);

  // sheet가 열릴 때마다 외부 settings로 draft 초기화.
  React.useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) =>
    setDraft((s) => ({ ...s, [k]: v }));

  const handleSave = async () => {
    try {
      await ipc.saveSettings(draft);
      onChange(draft);
      setOpen(false);
    } catch (e) {
      console.error("save_settings 실패:", e);
    }
  };

  const handleReset = () => setDraft(DEFAULT_SETTINGS);

  const handlePickFolder = async () => {
    try {
      const picked = await ipc.pickFolder(draft.output_dir);
      if (picked) set("output_dir", picked);
    } catch (e) {
      console.error("pickFolder 실패:", e);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <SheetContent>
        <DialogHeader>
          <DialogTitle>설정</DialogTitle>
          <DialogDescription>
            settings.ini 와 동기화됨. 저장 시 사용자 config 폴더에 기록.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto pr-2 space-y-6">
          {/* 화질 */}
          <div className="space-y-2">
            <Label>화질</Label>
            <ToggleGroup
              type="single"
              value={draft.quality}
              onValueChange={(v) => v && set("quality", v as Quality)}
              className="flex flex-wrap gap-1"
            >
              {QUALITIES.map((q) => (
                <ToggleGroupItem key={q} value={q} className="text-xs">
                  {q}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* 컨테이너 */}
          <div className="space-y-2">
            <Label>컨테이너</Label>
            <ToggleGroup
              type="single"
              value={draft.container}
              onValueChange={(v) => v && set("container", v as Container)}
            >
              {CONTAINERS.map((c) => (
                <ToggleGroupItem key={c} value={c}>{c}</ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* 코덱 / 오디오 (2열) */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>비디오 코덱</Label>
              <Select value={draft.codec} onValueChange={(v) => set("codec", v as Settings["codec"])}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">auto</SelectItem>
                  <SelectItem value="h264">h264</SelectItem>
                  <SelectItem value="vp9">vp9</SelectItem>
                  <SelectItem value="av1">av1</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>오디오</Label>
              <Select value={draft.audio} onValueChange={(v) => set("audio", v as Settings["audio"])}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="best">best</SelectItem>
                  <SelectItem value="m4a">m4a</SelectItem>
                  <SelectItem value="opus">opus</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* 출력 폴더 */}
          <div className="space-y-2">
            <Label>출력 폴더</Label>
            <div className="flex gap-2">
              <Input
                value={draft.output_dir}
                onChange={(e) => set("output_dir", e.target.value)}
                className="font-mono text-xs"
              />
              <Button variant="outline" size="icon" onClick={handlePickFolder} title="찾아보기">
                <FolderOpen className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* 자막 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="embed-subs">자막 임베드</Label>
              <Switch
                id="embed-subs"
                checked={draft.embed_subs}
                onCheckedChange={(v) => set("embed_subs", v)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">자막 언어 (CSV)</Label>
              <Input
                value={draft.sub_langs}
                onChange={(e) => set("sub_langs", e.target.value)}
                placeholder="ko,en,ja"
                className="font-mono text-xs"
                disabled={!draft.embed_subs}
              />
            </div>
          </div>

          {/* 쿠키 브라우저 */}
          <div className="space-y-2">
            <Label>쿠키 브라우저</Label>
            <Select
              value={draft.cookies_from_browser || "none"}
              onValueChange={(v) => set("cookies_from_browser", v === "none" ? "" : v)}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">(사용 안 함)</SelectItem>
                <SelectItem value="chrome">Chrome</SelectItem>
                <SelectItem value="firefox">Firefox</SelectItem>
                <SelectItem value="edge">Edge</SelectItem>
                <SelectItem value="brave">Brave</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              비공개/연령제한/멤버십 영상에 필요. 사용 시 해당 브라우저는 완전히 종료해야 함.
            </p>
          </div>

          {/* 재생목록 */}
          <div className="space-y-3">
            <div className="space-y-2">
              <Label>재생목록 처리</Label>
              <Select
                value={draft.playlist_mode}
                onValueChange={(v) => set("playlist_mode", v as Settings["playlist_mode"])}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">auto — 단일 영상 + ?list= 는 단일로</SelectItem>
                  <SelectItem value="expand">expand — 항상 펼침</SelectItem>
                  <SelectItem value="single">single — 항상 단일</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">최대 개수</Label>
                <Input
                  value={draft.max_downloads}
                  onChange={(e) => set("max_downloads", e.target.value)}
                  placeholder="제한 없음"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">항목 (예: 1-10)</Label>
                <Input
                  value={draft.playlist_items}
                  onChange={(e) => set("playlist_items", e.target.value)}
                  placeholder="(전부)"
                  className="font-mono text-xs"
                />
              </div>
            </div>
          </div>

          {/* 고급 (Collapsible) */}
          <div className="border-t pt-3">
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="text-sm font-medium text-muted-foreground hover:text-foreground"
            >
              {advancedOpen ? "▼ 고급 (네트워크 / 멈춤 대응)" : "▶ 고급 (네트워크 / 멈춤 대응)"}
            </button>
            {advancedOpen && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">socket_timeout (s)</Label>
                  <Input
                    type="number"
                    value={draft.socket_timeout}
                    onChange={(e) => set("socket_timeout", Number(e.target.value) || 30)}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">retries</Label>
                  <Input
                    type="number"
                    value={draft.retries}
                    onChange={(e) => set("retries", Number(e.target.value) || 10)}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">fragment_retries</Label>
                  <Input
                    type="number"
                    value={draft.fragment_retries}
                    onChange={(e) => set("fragment_retries", Number(e.target.value) || 10)}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">throttled_rate</Label>
                  <Input
                    value={draft.throttled_rate}
                    onChange={(e) => set("throttled_rate", e.target.value)}
                    placeholder="100K (빈 값=off)"
                    className="font-mono text-xs"
                  />
                </div>
              </div>
            )}
          </div>

          {draft._source && (
            <p className="text-[11px] text-muted-foreground font-mono break-all">
              source: {draft._source}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={handleReset} title="기본값으로">
            <RotateCcw className="h-4 w-4" />
            기본값
          </Button>
          <DialogClose asChild>
            <Button variant="outline">취소</Button>
          </DialogClose>
          <Button onClick={handleSave}>
            <Save className="h-4 w-4" />
            저장
          </Button>
        </DialogFooter>
      </SheetContent>
    </Dialog>
  );
}
