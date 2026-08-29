/** Aeval REST/SSE API 类型 — 与 backend/app/eval_harness/api/routes 返回契约对齐。 */

export type RunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface GraderResultLite {
  grader_name: string;
  score: number;
  passed: boolean;
  explanation: string;
}

export interface TrialLite {
  trial_index: number;
  trace_id: string;
  success: boolean;
  score: number;
  duration_ms: number;
  error: string | null;
  grader_results: GraderResultLite[];
}

export interface TaskSummary {
  task_id: string;
  task_description: string;
  total_trials: number;
  pass_at_k: Record<string, number>;
  pass_power_k: Record<string, number>;
  avg_score: number;
  avg_metrics: Record<string, number>;
  failures: number[];
  pending_trials: number[];
  consistent: boolean;
  score_std_dev: number;
}

export interface RunSummaryData {
  total_tasks: number;
  total_trials: number;
  pass_at_k: Record<string, number>;
  pass_power_k: Record<string, number>;
  avg_score: number;
  avg_metrics: Record<string, number>;
  task_summaries: TaskSummary[];
  failures: string[];
  saturation: Record<string, unknown>;
}

export interface RunListItem {
  run_id: string;
  suite_name: string;
  status: RunStatus;
  started_at: number;
  completed_at: number | null;
  duration_ms: number | null;
  task_count: number;
  summary: RunSummaryData | null;
}

export interface RunDetail {
  run_id: string;
  suite_name: string;
  status: RunStatus;
  started_at: number;
  completed_at: number | null;
  duration_ms: number | null;
  error: string | null;
  trials: Record<string, TrialLite[]>;
  summary: RunSummaryData | null;
}

export interface SuiteListItem {
  name: string;
  description: string;
  task_count: number;
  metadata: Record<string, unknown>;
}

export interface GraderConfig {
  type: string;
  name: string;
  weight: number;
  required: boolean;
  config: Record<string, unknown>;
}

export interface SuiteTask {
  id: string;
  description: string;
  prompt: string;
  graders: GraderConfig[];
  env: Record<string, unknown>;
  max_trials: number;
}

export interface SuiteDetail {
  name: string;
  description: string;
  version: string;
  tasks: SuiteTask[];
  metadata: Record<string, unknown>;
}

/** GET /runs/{id}/trials 的完整 TrialResult (含 transcript/outcome/metrics) */
export interface TrialFull {
  trial_index: number;
  trace_id: string;
  success: boolean;
  metrics: Record<string, number>;
  transcript: TranscriptEntry[];
  outcome: {
    conversation_id?: string;
    run_ids?: string[];
    files?: Record<string, string>;
    artifacts?: Array<Record<string, unknown>>;
    seed_files?: string[];
    trace_id_unavailable?: string;
  } & Record<string, unknown>;
  duration_ms: number;
  error: string | null;
  grader_results: GraderResultFull[];
}

export interface GraderResultFull extends GraderResultLite {
  grader_type: string;
  details: Record<string, unknown>;
  confidence: number;
  uncertainty: number;
  sample_count: number;
  duration_ms: number;
}

export interface TranscriptEntry {
  id: string;
  role: string;
  agent_id: string | null;
  content: string;
  status: string;
  run_id: string | null;
  created_at: number;
  parts?: Array<Record<string, unknown>>;
}

/** SSE 事件载荷 (GET /runs/{id}/stream) */
export interface RunEvent {
  type:
    | "task_start"
    | "trial_start"
    | "trial_complete"
    | "task_complete"
    | "run_complete"
    | "error";
  run_id: string;
  timestamp: number;
  task_id?: string;
  trial_index?: number;
  status?: RunStatus;
  error?: string | null;
  summary?: RunSummaryData | null;
  [key: string]: unknown;
}

export interface ComparisonResponse {
  run_a: { run_id: string; suite_name: string; started_at: number };
  run_b: { run_id: string; suite_name: string; started_at: number };
  comparison: {
    pass_at_k: Record<string, { a: number; b: number; delta: number }>;
    pass_power_k: Record<string, { a: number; b: number; delta: number }>;
    avg_score: { a: number; b: number; delta: number };
    regressions: TaskDelta[];
    improvements: TaskDelta[];
    tasks: Record<string, { a: number; b: number; delta: number }>;
  };
}

export interface TaskDelta {
  task_id: string;
  a: number;
  b: number;
  delta: number;
}
