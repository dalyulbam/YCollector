import { Inbox } from "lucide-react";
import { JobCard } from "./JobCard";
import { useJobs } from "@/store/jobs";
import type { Settings } from "@/lib/types";

interface Props {
  settings: Settings;
}

export function JobList({ settings }: Props) {
  const jobs = useJobs((s) => s.jobs);
  const order = useJobs((s) => s.order);

  if (order.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-2">
        <Inbox className="h-8 w-8 opacity-50" />
        <p className="text-sm">URL을 붙여넣고 다운로드를 시작하세요.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {order
        .map((id) => jobs[id])
        .filter((j): j is NonNullable<typeof j> => Boolean(j))
        .map((job) => (
          <JobCard key={job.id} job={job} settings={settings} />
        ))}
    </div>
  );
}
