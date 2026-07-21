export type Role = "admin" | "editor" | "viewer";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
}

export interface UserAccount {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  created_at: string | null;
}

export interface UserCreateInput {
  username: string;
  password: string;
  display_name: string;
  role: Role;
}

export interface RegisterInput {
  username: string;
  password: string;
  display_name: string;
}

export interface UserUpdateInput {
  display_name: string;
  username: string;
  role: Role;
  is_active: boolean;
}

export interface RunSummary {
  tables_total?: number;
  pass?: number;
  fail?: number;
  error?: number;
  cancelled?: number;
}

export interface RunListItem {
  id: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  mode: string;
  config_name: string;
  started_at: string | null;
  finished_at: string | null;
  summary: RunSummary;
}

export interface TrendPoint {
  id: number;
  rate: number;
  dominant: "pass" | "fail" | "error";
}

export interface ProblemTable {
  source_table: string;
  target_table: string;
  bad_count: number;
  pct: number;
}

export interface DashboardData {
  recent_runs: RunListItem[];
  last_run: RunListItem | null;
  running_runs: RunListItem[];
  summary: RunSummary;
  pass_rate: string;
  trend: TrendPoint[];
  problem_tables: ProblemTable[];
}

// Not a closed union on purpose -- the real list comes from the backend's
// SUPPORTED_ENGINES (validation_core/connectors/registry.py) via
// GET /api/connections/engines, which grows independently of the frontend.
export type Engine = string;

export interface Connection {
  id: number;
  name: string;
  engine: Engine;
  host: string;
  port: number;
  database: string;
  username: string;
  use_tunnel: boolean;
  status: "unknown" | "ok" | "failed";
  last_tested_at: string | null;
  last_test_message: string;
}

export interface ConnectionInput {
  name: string;
  engine: Engine;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  use_tunnel: boolean;
}

export interface TestConnectionResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

export type ValidationMode = "tiered" | "aggregate" | "rowlevel_missing" | "rowlevel_full";

export interface ConfigListItem {
  id: number;
  name: string;
  source_connection_name: string;
  target_connection_name: string;
  table_count: number;
  default_mode: ValidationMode;
  last_run: RunListItem | null;
}

export interface ConnectionBrief {
  id: number;
  name: string;
  engine: Engine;
}

export interface ConfigTableRow {
  id: number;
  source_table: string;
  target_table: string;
  key_columns: string[];
  chunk_column: string | null;
  date_column: string | null;
  exclude_columns: string[];
  mode_override: ValidationMode | "" | null;
  enabled: boolean;
  note: string;
}

export interface ConfigTableRowInput {
  source_table: string;
  target_table: string;
  key_columns: string[];
  chunk_column: string | null;
  date_column: string | null;
  exclude_columns: string[];
  mode_override: string | null;
  enabled: boolean;
}

export interface ConfigForCopy {
  id: number;
  name: string;
  table_count: number;
}

export interface ConfigDetail {
  id: number;
  name: string;
  description: string;
  source_connection: ConnectionBrief;
  target_connection: ConnectionBrief;
  default_mode: ValidationMode;
  tables: ConfigTableRow[];
  runs: RunListItem[];
  configs_for_copy: ConfigForCopy[];
  table_columns: Record<string, string[]>;
}

export interface MappingSuggestion {
  source_table: string;
  target_table: string;
  match_rule: string;
  key_columns: string[];
  key_source: string;
  chunk_column: string;
  date_column: string;
  exclude_columns: string[];
  mode_override: string;
}

export interface SuggestResult {
  suggestions: MappingSuggestion[];
  table_columns: Record<string, string[]>;
}

export interface ConfigCreateInput {
  name: string;
  description: string;
  source_connection_id: number;
  target_connection_id: number;
  default_mode: ValidationMode;
}

export interface ConfigStatusRunTable {
  id: number;
  run_id: number;
  status: string;
  finished_at: string | null;
}

export interface ConfigStatusRow {
  source_table: string;
  target_table: string;
  enabled: boolean;
  removed: boolean;
  latest: ConfigStatusRunTable | null;
  history: ConfigStatusRunTable[];
}

export interface ConfigStatusData {
  config: { id: number; name: string };
  rows: ConfigStatusRow[];
  counts: Record<string, number>;
  history_limit: number;
}

export interface RunEvent {
  kind: string;
  message: string;
}

export interface RunTableSummary {
  id: number;
  source_table: string;
  target_table: string;
  status: string;
  tier_reached: number | null;
  source_rows: number | null;
  target_rows: number | null;
  row_diff: number | null;
  agg_stat_mismatch: number | null;
  missing_count: number | null;
  differing_values: number | null;
}

export interface RunDetail {
  id: number;
  status: string;
  mode: string;
  trigger_type: string;
  config_id: number;
  config_name: string;
  started_at: string | null;
  finished_at: string | null;
  tables: RunTableSummary[];
  events: RunEvent[];
}

export interface FindingAggregateItem {
  category: string;
  column_name: string | null;
  metric: string | null;
  period: string | null;
  source_value: string | null;
  target_value: string | null;
  difference: number | null;
}

export interface RlMetrics {
  mode?: string;
  missing_in_source: number;
  missing_in_target: number;
  differing_values: number;
  value_columns: string[];
  key_columns: string[];
  truncated?: boolean;
}

export interface ColumnTypeDetail {
  column: string;
  source_type: string | null;
  target_type: string | null;
  category_match: boolean | null;
}

export interface EventLogEntry {
  ts: string;
  kind: string;
  message: string;
}

export interface RunTableDrilldown {
  id: number;
  run_id: number;
  config_id: number;
  source_table: string;
  target_table: string;
  status: string;
  tier_reached: number | null;
  mode: string;
  source_rows: number | null;
  target_rows: number | null;
  row_diff: number | null;
  source_cols: number | null;
  target_cols: number | null;
  extra_source_columns: string[];
  extra_target_columns: string[];
  rl_metrics: RlMetrics | null;
  chunks_total: number;
  investigate_query: string | null;
  error: string | null;
  agg_findings: FindingAggregateItem[];
  period_findings: FindingAggregateItem[];
  period_count: number;
  column_type_details: ColumnTypeDetail[];
  type_mismatch_count: number;
  missing_count: number;
  total_diff_count: number;
  diff_columns: string[];
  diff_column_counts: Record<string, number>;
  queries: Record<string, string>;
  event_log: EventLogEntry[];
}

export interface MissingFindingRow {
  finding_type: "missing_in_source" | "missing_in_target";
  row_key: string;
}

export interface DiffFindingRow {
  row_key: string;
  column_name: string;
  source_value: string | null;
  target_value: string | null;
}

export interface RowlevelResult<T> {
  rows: T[];
  page: number;
  total_pages: number;
  page_size: number;
}
