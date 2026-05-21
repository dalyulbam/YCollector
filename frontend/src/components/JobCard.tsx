import { FolderOpen, X, RotateCcw, Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { humanBytes, humanDuration } from "@/lib/utils";
import { ipc } from "@/lib/ipc";
import type { Job, Settings } from "@/lib/types";
import { useJobs } from "@/store/jobs";

interface Props {
  job: Job;
  settings: Settings;
}

function statusBadge(status: Job["status"]) {
  switch (status) {
    case "queued":
      return <Badge variant="outline">대기</Badge>;
    case "probing":
      return <Badge variant="secondary">메타 조회 중</Badge>;
    case "downloading":
      return <Badge variant="default">다운로드 중</Badge>;
    case "postprocess":
      return <Badge variant="warning">후처리</Badge>;
    case "done":
      return <Badge variant="success">완료</Badge>;
    case "failed":
      return <Badge variant="destructive">실패</Badge>;
    case "cancelled":
      return <Badge variant="outline">취소됨</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

export function JobCard({ job, settings }: Props) {
  const removeJob = useJobs((s) => s.removeJob);
  const addJob = useJobs((s) => s.addJob);

  const active = job.status === "downloading" || job.status === "postprocess" || job.status === "queued" || job.status === "probing";
  const percent = typeof job.percent === "number" ? Math.max(0, Math.min(100, job.percent)) : null;

  const onCancel = async () => {
    try {
      await ipc.cancel(job.id);
    } catch (e) {
      console.error("cancel 실패:", e);
    }
  };

  const onOpenFolder = async () => {
    if (!job.filepath) return;
    // 폴더만 열기 위해 파일 경로 → 디렉토리 부분만 추출.
    const sep = job.filepath.includes("\\") ? "\\" : "/";
    const dir = job.filepath.substring(0, job.filepath.lastIndexOf(sep)) || job.filepath;
    try {
      await ipc.openPath(dir);
    } catch (e) {
      console.error("openPath 실패:", e);
    }
  };

  const onRetry = async () => {
    try {
      const newId = await ipc.startJob(job.url, settings);
      addJob(newId, job.url);
      removeJob(job.id);
    } catch (e) {
      console.error("retry 실패:", e);
    }
  };

  return (
    <Card>
      <CardContent className="p-3">
        <div className="flex gap-3">
          {job.thumbnail ? (
            <img
              src={job.thumbnail}
              alt=""
              className="h-16 w-28 rounded-md object-cover border bg-muted shrink-0"
              loading="lazy"
            />
          ) : (
            <div className="h-16 w-28 rounded-md border bg-muted shrink-0 flex items-center justify-center text-xs text-muted-foreground">
              {active ? <Loader2 className="h-5 w-5 animate-spin" /> : "—"}
            </div>
          )}

          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 min-w-0">
              {statusBadge(job.status)}
              <div className="font-medium truncate" title={job.title || job.url}>
                {job.title || job.url}
              </div>
            </div>
            <div className="text-xs text-muted-foreground truncate">
              {job.channel ? `${job.channel} · ` : ""}
              {job.duration || ""}
            </div>

            {active && (
              <div className="space-y-1 pt-1">
                <Progress value={percent ?? 0} />
                <div className="flex justify-between text-[11px] font-mono text-muted-foreground">
                  <span>
                    {percent != null ? `${percent.toFixed(1)}%` : "—"}{" "}
                    {job.total != null && (
                      <>
                        · {humanBytes(job.downloaded ?? 0)} / {humanBytes(job.total)}
                      </>
                    )}
                  </span>
                  <span>
                    {job.speed != null && <>@ {humanBytes(job.speed)}/s</>}{" "}
                    {job.eta != null && <>· ETA {humanDuration(job.eta)}</>}
                  </span>
                </div>
              </div>
            )}

            {job.status === "done" && job.filepath && (
              <p className="text-[11px] font-mono text-muted-foreground truncate" title={job.filepath}>
                → {job.filepath}
              </p>
            )}
            {job.status === "failed" && job.error && (
              <p className="text-xs text-destructive">
                [{job.error.category}] {job.error.message}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1 shrink-0">
            {active ? (
              <Button variant="ghost" size="icon" onClick={onCancel} title="취소">
                <X className="h-4 w-4" />
              </Button>
            ) : (
              <>
                {job.status === "done" && (
                  <Button variant="ghost" size="icon" onClick={onOpenFolder} title="폴더 열기">
                    <FolderOpen className="h-4 w-4" />
                  </Button>
                )}
                {(job.status === "failed" || job.status === "cancelled") && (
                  <Button variant="ghost" size="icon" onClick={onRetry} title="재시도">
                    <RotateCcw className="h-4 w-4" />
                  </Button>
                )}
                <Button variant="ghost" size="icon" onClick={() => removeJob(job.id)} title="목록에서 제거">
                  <X className="h-4 w-4" />
                </Button>
              </>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
