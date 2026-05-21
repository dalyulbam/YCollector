import { create } from "zustand";
import type { Job, JobStatus, LibraryItem } from "@/lib/types";

interface JobsState {
  jobs: Record<string, Job>;
  order: string[]; // 최근 추가 순 (앞이 새로움)
  library: LibraryItem[];
  logs: { ts: number; level: string; line: string; job_id?: string }[];
  addJob: (id: string, url: string) => void;
  updateJob: (id: string, patch: Partial<Job>) => void;
  setStatus: (id: string, status: JobStatus) => void;
  removeJob: (id: string) => void;
  setLibrary: (items: LibraryItem[]) => void;
  appendLog: (line: string, level?: string, job_id?: string) => void;
  clearLogs: () => void;
}

export const useJobs = create<JobsState>((set) => ({
  jobs: {},
  order: [],
  library: [],
  logs: [],
  addJob: (id, url) =>
    set((s) => ({
      jobs: {
        ...s.jobs,
        [id]: { id, url, status: "queued", startedAt: Date.now() },
      },
      order: [id, ...s.order.filter((x) => x !== id)],
    })),
  updateJob: (id, patch) =>
    set((s) => {
      const current = s.jobs[id];
      if (!current) return s;
      return { jobs: { ...s.jobs, [id]: { ...current, ...patch } } };
    }),
  setStatus: (id, status) =>
    set((s) => {
      const current = s.jobs[id];
      if (!current) return s;
      const finishedAt =
        status === "done" || status === "failed" || status === "cancelled"
          ? Date.now()
          : current.finishedAt;
      return {
        jobs: {
          ...s.jobs,
          [id]: { ...current, status, finishedAt },
        },
      };
    }),
  removeJob: (id) =>
    set((s) => {
      const { [id]: _omit, ...rest } = s.jobs;
      return { jobs: rest, order: s.order.filter((x) => x !== id) };
    }),
  setLibrary: (items) => set({ library: items }),
  appendLog: (line, level = "info", job_id) =>
    set((s) => ({
      logs: [
        ...s.logs.slice(-499),
        { ts: Date.now(), level, line, job_id },
      ],
    })),
  clearLogs: () => set({ logs: [] }),
}));
