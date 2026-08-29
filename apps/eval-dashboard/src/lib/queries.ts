/** React Query hooks — 服务端状态 (suite/run/trial/compare)。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type {
  ComparisonResponse,
  RunDetail,
  RunListItem,
  RunSummaryData,
  SuiteDetail,
  SuiteListItem,
  TrialFull,
} from "@/lib/types";

export function useSuites() {
  return useQuery({
    queryKey: ["suites"],
    queryFn: () => apiFetch<{ suites: SuiteListItem[] }>("/api/eval/suites"),
  });
}

export function useSuite(name: string | null) {
  return useQuery({
    queryKey: ["suite", name],
    queryFn: () => apiFetch<SuiteDetail>(`/api/eval/suites/${encodeURIComponent(name!)}`),
    enabled: !!name,
  });
}

export function useRuns(limit = 50) {
  return useQuery({
    queryKey: ["runs", limit],
    queryFn: () => apiFetch<{ runs: RunListItem[] }>(`/api/eval/runs?limit=${limit}`),
  });
}

export function useRun(runId: string | null, opts?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiFetch<RunDetail>(`/api/eval/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: opts?.refetchInterval,
  });
}

export function useRunTrials(runId: string | null) {
  return useQuery({
    queryKey: ["run-trials", runId],
    queryFn: () =>
      apiFetch<{ trials: Record<string, TrialFull[]> }>(
        `/api/eval/runs/${runId}/trials`
      ),
    enabled: !!runId,
  });
}

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suiteName: string) =>
      apiFetch<{ run_id: string; status: string }>("/api/eval/runs", {
        method: "POST",
        json: { suite_name: suiteName },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useCreateSuite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suite: SuiteDetail) =>
      apiFetch<{ name: string; task_count: number }>("/api/eval/suites", {
        method: "POST",
        json: suite,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suites"] });
    },
  });
}

export function useDeleteSuite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiFetch<{ deleted: boolean }>(`/api/eval/suites/${encodeURIComponent(name)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suites"] });
    },
  });
}

export function useCompare() {
  return useMutation({
    mutationFn: (vars: { runIdA: string; runIdB: string }) =>
      apiFetch<ComparisonResponse>("/api/eval/compare", {
        method: "POST",
        json: { run_id_a: vars.runIdA, run_id_b: vars.runIdB },
      }),
  });
}

export type OverviewStats = {
  suites: number;
  tasks: number;
  runs: number;
  avgScore: number;
  passAt3: number;
};

export function useOverviewStats(): {
  stats: OverviewStats | null;
  loading: boolean;
  runs: RunListItem[];
} {
  const suites = useSuites();
  const runs = useRuns(50);
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () =>
      apiFetch<{ tasks: { id: string }[]; total: number }>("/api/eval/tasks"),
  });

  const loading = suites.isLoading || runs.isLoading || tasks.isLoading;
  if (loading) return { stats: null, loading: true, runs: runs.data?.runs ?? [] };

  const runItems = runs.data?.runs ?? [];
  const completed = runItems.filter((r) => r.summary != null);
  const avgScore =
    completed.length > 0
      ? completed.reduce((acc, r) => acc + (r.summary?.avg_score ?? 0), 0) /
        completed.length
      : 0;
  const passAt3 =
    completed.length > 0
      ? completed.reduce((acc, r) => acc + (r.summary?.pass_at_k?.["3"] ?? r.summary?.pass_at_k?.["1"] ?? 0), 0) /
        completed.length
      : 0;

  return {
    stats: {
      suites: suites.data?.suites.length ?? 0,
      tasks: tasks.data?.total ?? 0,
      runs: runItems.length,
      avgScore,
      passAt3,
    },
    loading: false,
    runs: runItems,
  };
}

export type TrendPoint = { time: number; score: number; runId: string };

export function buildScoreTrend(runs: RunListItem[]): TrendPoint[] {
  return runs
    .filter((r) => r.summary != null && r.completed_at != null)
    .map((r) => ({
      time: r.completed_at as number,
      score: Number((r.summary?.avg_score ?? 0).toFixed(4)),
      runId: r.run_id,
    }))
    .sort((a, b) => a.time - b.time)
    .slice(-20);
}

export function summarizeRunSummary(s: RunSummaryData | null): string {
  if (!s) return "—";
  return `pass@1 ${(s.pass_at_k?.["1"] ?? 0).toFixed(2)} / avg ${s.avg_score.toFixed(3)}`;
}
