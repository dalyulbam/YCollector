import * as React from "react";
import { FolderOpen, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ipc } from "@/lib/ipc";
import { useJobs } from "@/store/jobs";

type SortKey = "recent" | "title" | "channel";

export function LibraryTab() {
  const library = useJobs((s) => s.library);
  const setLibrary = useJobs((s) => s.setLibrary);
  const [q, setQ] = React.useState("");
  const [sort, setSort] = React.useState<SortKey>("recent");

  // 탭이 처음 보일 때 한 번 fetch (Phase 1 manifest.json 기반).
  React.useEffect(() => {
    void ipc
      .listLibrary()
      .then((lib) => setLibrary(lib.items || []))
      .catch((e) => console.warn("list_library 실패:", e));
  }, [setLibrary]);

  const filtered = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    const items = needle
      ? library.filter(
          (it) =>
            it.title.toLowerCase().includes(needle) ||
            it.channel.toLowerCase().includes(needle),
        )
      : library.slice();
    items.sort((a, b) => {
      switch (sort) {
        case "title":
          return a.title.localeCompare(b.title);
        case "channel":
          return a.channel.localeCompare(b.channel);
        case "recent":
        default:
          return b.finished_at - a.finished_at;
      }
    });
    return items;
  }, [library, q, sort]);

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="제목 / 채널로 검색"
            className="pl-9"
          />
        </div>
        <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="recent">최근</SelectItem>
            <SelectItem value="title">제목</SelectItem>
            <SelectItem value="channel">채널</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground py-6 text-center">
          {library.length === 0
            ? "라이브러리가 비어 있습니다. 영상을 다운로드하면 여기에 모입니다."
            : "검색 결과 없음."}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((it) => (
            <Card key={`${it.job_id}-${it.finished_at}`}>
              <CardContent className="p-3 flex gap-3 items-center">
                {it.thumbnail ? (
                  <img
                    src={it.thumbnail}
                    alt=""
                    className="h-14 w-24 rounded-md object-cover border bg-muted shrink-0"
                    loading="lazy"
                  />
                ) : (
                  <div className="h-14 w-24 rounded-md border bg-muted shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate" title={it.title}>{it.title}</div>
                  <div className="text-xs text-muted-foreground truncate">
                    {it.channel} · {it.duration}
                  </div>
                  <div className="text-[11px] font-mono text-muted-foreground truncate" title={it.filepath}>
                    {it.filepath}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  title="폴더 열기"
                  onClick={() => {
                    const sep = it.filepath.includes("\\") ? "\\" : "/";
                    const dir = it.filepath.substring(0, it.filepath.lastIndexOf(sep)) || it.filepath;
                    void ipc.openPath(dir);
                  }}
                >
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
