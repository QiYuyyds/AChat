/** React Query hooks — 服务端状态 (suite/run/trial/compare)。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type {
  ComparisonResponse,
  CoverageReport,
  DatasetDetail,
  DatasetItem,
  DatasetSummary,
  FromLLMResponse,
  FromTraceResponse,
  QualityReport,
  RegressionExtractResponse,
  RunDetail,
  RunListItem,
  RunSummaryData,
  SuiteDetail,
  SuiteListItem,
  TaskDetailResponse,
  TaskHistoryResponse,
  TaskListItem,
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

// ── 数据集 (change ③ 端点封装) ───────────────────────────────────────────────

export function useDatasets(tags?: string | null) {
  const query = tags?.trim() ? `?tags=${encodeURIComponent(tags.trim())}` : "";
  return useQuery({
    queryKey: ["datasets", tags ?? null],
    queryFn: () => apiFetch<{ datasets: DatasetSummary[] }>(`/api/eval/datasets${query}`),
  });
}

export function useDataset(ref: string | null) {
  return useQuery({
    queryKey: ["dataset", ref],
    queryFn: () => apiFetch<DatasetDetail>(`/api/eval/datasets/${encodeURIComponent(ref!)}`),
    enabled: !!ref,
  });
}

export function useDatasetCoverage(ref: string | null) {
  return useQuery({
    queryKey: ["dataset-coverage", ref],
    queryFn: () =>
      apiFetch<CoverageReport>(`/api/eval/datasets/${encodeURIComponent(ref!)}/coverage`),
    enabled: !!ref,
  });
}

function useInvalidateDatasets() {
  const qc = useQueryClient();
  return (ref?: string) => {
    void qc.invalidateQueries({ queryKey: ["datasets"] });
    if (ref) {
      void qc.invalidateQueries({ queryKey: ["dataset", ref] });
      void qc.invalidateQueries({ queryKey: ["dataset-coverage", ref] });
    }
  };
}

export function useCreateDataset() {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (body: { name: string; description?: string; tags?: string[] }) =>
      apiFetch<{ id: string; name: string; version: string; item_count: number }>(
        "/api/eval/datasets",
        { method: "POST", json: body },
      ),
    onSuccess: () => invalidate(),
  });
}

export function useImportDataset() {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (body: { content: string; format: string; source_type?: string }) =>
      apiFetch<{ id: string; name: string; item_count: number }>("/api/eval/datasets/import", {
        method: "POST",
        json: body,
      }),
    onSuccess: () => invalidate(),
  });
}

export function useDeleteDataset() {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (ref: string) =>
      apiFetch<{ deleted: boolean }>(`/api/eval/datasets/${encodeURIComponent(ref)}`, {
        method: "DELETE",
      }),
    onSuccess: () => invalidate(),
  });
}

export function useAddDatasetItem(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (item: DatasetItem) =>
      apiFetch<{ added: boolean; item_id: string }>(
        `/api/eval/datasets/${encodeURIComponent(ref)}/items`,
        { method: "POST", json: item },
      ),
    onSuccess: () => invalidate(ref),
  });
}

export function useUpdateDatasetItem(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (vars: { itemId: string; item: DatasetItem }) =>
      apiFetch<{ updated: boolean }>(
        `/api/eval/datasets/${encodeURIComponent(ref)}/items/${encodeURIComponent(vars.itemId)}`,
        { method: "PUT", json: vars.item },
      ),
    onSuccess: () => invalidate(ref),
  });
}

export function useDeleteDatasetItem(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (itemId: string) =>
      apiFetch<{ deleted: boolean }>(
        `/api/eval/datasets/${encodeURIComponent(ref)}/items/${encodeURIComponent(itemId)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => invalidate(ref),
  });
}

/** 质量检查 (GET 端点但由按钮触发 — 用 mutation 语义) */
export function useQualityCheck(ref: string) {
  return useMutation({
    mutationFn: () =>
      apiFetch<QualityReport>(`/api/eval/datasets/${encodeURIComponent(ref)}/quality-check`),
  });
}

export function useToSuite(ref: string) {
  return useMutation({
    mutationFn: (name?: string) =>
      apiFetch<{
        suite_name: string;
        task_count: number;
        dataset_version: string;
        saved: boolean;
      }>(`/api/eval/datasets/${encodeURIComponent(ref)}/to-suite`, {
        method: "POST",
        json: { name: name || null, save: true },
      }),
  });
}

export function useBumpDatasetVersion(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (vars: { change_type: "major" | "minor" | "patch"; note: string }) =>
      apiFetch<{ id: string; version: string; item_count: number }>(
        `/api/eval/datasets/${encodeURIComponent(ref)}/version`,
        { method: "POST", json: vars },
      ),
    onSuccess: () => invalidate(ref),
  });
}

export function useRegressionExtract(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (vars: { run_id: string; max_items?: number; bump_version?: string | null }) =>
      apiFetch<RegressionExtractResponse>(
        `/api/eval/datasets/${encodeURIComponent(ref)}/regression-extract`,
        { method: "POST", json: vars },
      ),
    onSuccess: () => invalidate(ref),
  });
}

export function useFromTrace(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (vars: { strategy: string; limit: number; candidate_limit: number }) =>
      apiFetch<FromTraceResponse>(`/api/eval/datasets/${encodeURIComponent(ref)}/from-trace`, {
        method: "POST",
        json: vars,
      }),
    onSuccess: () => invalidate(ref),
  });
}

export function useFromLLM(ref: string) {
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (vars: { scenario: string; capabilities: string[]; count: number }) =>
      apiFetch<FromLLMResponse>(`/api/eval/datasets/${encodeURIComponent(ref)}/from-llm`, {
        method: "POST",
        json: vars,
      }),
    onSuccess: () => invalidate(ref),
  });
}

// ── Task 库 ─────────────────────────────────────────────────────────────────

export function useTasks() {
  return useQuery({
    queryKey: ["tasks"],
    queryFn: () => apiFetch<{ tasks: TaskListItem[]; total: number }>("/api/eval/tasks"),
  });
}

export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: ["task", taskId],
    queryFn: () => apiFetch<TaskDetailResponse>(`/api/eval/tasks/${encodeURIComponent(taskId!)}`),
    enabled: !!taskId,
  });
}

export function useTaskHistory(taskId: string | null) {
  return useQuery({
    queryKey: ["task-history", taskId],
    queryFn: () =>
      apiFetch<TaskHistoryResponse>(`/api/eval/tasks/${encodeURIComponent(taskId!)}/history`),
    enabled: !!taskId,
  });
}
