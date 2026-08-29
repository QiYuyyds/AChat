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

// ── 数据集 (change ③ /api/eval/datasets) ─────────────────────────────────────

export interface DatasetSummary {
  id: string;
  name: string;
  description: string;
  version: string;
  tags: string[];
  capability_map: Record<string, number>;
  item_count: number;
  created_at: number;
  updated_at: number;
}

export interface DatasetItem {
  id: string;
  prompt: string;
  description: string;
  graders: GraderConfig[];
  env: Record<string, unknown>;
  metadata: Record<string, unknown>;
  source_type: string;
  source_ref: string;
  created_at: number;
}

export interface DatasetChangeLogEntry {
  version: string;
  change_type: string;
  note: string;
  at: number;
  item_count: number;
}

export interface DatasetDetail extends DatasetSummary {
  items: DatasetItem[];
  metadata: Record<string, unknown>;
  change_log: DatasetChangeLogEntry[];
}

export interface QualityIssue {
  code: string;
  severity: string;
  item_id: string;
  message: string;
}

export interface QualityReport {
  total_items: number;
  ok: boolean;
  error_count: number;
  warning_count: number;
  errors: QualityIssue[];
  warnings: QualityIssue[];
}

export interface CoverageReport {
  total_items: number;
  untagged_items: number;
  coverage: Record<string, number>;
  insufficient: Array<{ capability: string; item_count: number; coverage: number }>;
}

export interface SkippedEntry {
  trace_id?: string;
  reason?: string;
  index?: number;
  error?: string;
  [key: string]: unknown;
}

/** POST /datasets/{ref}/from-trace 响应 */
export interface FromTraceResponse {
  mining: {
    strategy: string;
    candidates: number;
    inspected: number;
    mined: number;
    skipped_count: number;
    skipped: SkippedEntry[];
    item_ids: string[];
  };
  merged: number;
  merged_skipped: SkippedEntry[];
  dataset_id: string;
  item_count: number;
}

/** POST /datasets/{ref}/from-llm 响应 */
export interface FromLLMResponse {
  generation: {
    scenario: string;
    requested: number;
    generated: number;
    invalid_count: number;
    invalid: SkippedEntry[];
    item_ids: string[];
  };
  dataset_id: string;
  item_count: number;
}

/** POST /datasets/{ref}/regression-extract 响应 */
export interface RegressionExtractResponse {
  extraction: { extracted: number; skipped: SkippedEntry[]; [key: string]: unknown };
  merge: { merged: number; merged_skipped: SkippedEntry[]; [key: string]: unknown };
  version: string;
  bumped: boolean;
  dataset_id: string;
  item_count: number;
}

// ── Task 库 (跨 suite) ───────────────────────────────────────────────────────

export interface TaskListItem {
  id: string;
  description: string;
  suite_name: string;
  max_trials: number;
  grader_count: number;
}

export interface TaskFullDef extends SuiteTask {
  score_strategy: string;
  score_threshold: number;
  tracked_metrics: string[];
}

export interface TaskDetailResponse {
  task: TaskFullDef;
  suite_name: string;
}

/** GET /tasks/{id}/history 单条 — 该 task 在一次 run 中的聚合结果 */
export interface TaskHistoryEntry {
  run_id: string;
  suite_name: string;
  started_at: number;
  trials_passed: number;
  trials_total: number;
  avg_score: number;
  graders: Record<string, number>;
}

export interface TaskHistoryResponse {
  task_id: string;
  suite_name: string;
  history: TaskHistoryEntry[];
}
