export interface Unit {
  name?: string;
  type?: string;
  hp?: number;
  lat?: number;
  lng?: number;
  supply?: string;
  morale?: string;
  [key: string]: unknown;
}

export interface Engagement {
  lat?: number;
  lng?: number;
  description?: string;
  attacker?: string;
  attacker_pos?: [number, number];
  radius?: number;
  intensity?: number;
  [key: string]: unknown; // 中文键兜底（描述/战况/攻击方坐标…），与 viz.py 别名链同口径
}

export interface WarState {
  current_time?: string;
  current_terrain?: string;
  situation_report?: string;
  source_note?: string;
  red_units?: Unit[];
  blue_units?: Unit[];
  engagements?: Engagement[];
  eliminated?: string[];
  [key: string]: unknown;
}

export interface AdjUnit {
  name: string;
  team: "red" | "blue";
  type: string;
  lat: number;
  lng: number;
  speed_kmh: number;
  reach_km: number;
  nearest_enemy?: string | null;
  nearest_enemy_km?: number | null;
  can_contact: boolean;
  hours_to_contact?: number | null;
  supply: string;
  cut_by?: string | null;
  sealed_ratio: number;
  encirclement: string;
  retreat: string;
  morale_state: string;
}

export interface Adjudication {
  version: string;
  turn: number;
  time_step: string;
  hours: number;
  terrain: string;
  terrain_factor: number;
  terrain_source?: string; // 模型地形标签 / 战役参数库标定
  contact_km: number;
  control_radius_km: number;
  units: AdjUnit[];
  skipped_units: unknown[];
  facts: string[];
  summary: Record<string, number>;
  deterministic: boolean;
  digest: string;
  fingerprint: string;
}

export interface Battle {
  name: string;
  time_step: string; // 战役文件建议的回合跨度；"" = 无建议
}

export interface BattleSpec {
  name: string;
  summary: string;
  source: string | null; // 参数文件路径；null = 默认几何
  source_kind: string;   // library / corpus / default
  time_step_hint: string;
  calibrated: boolean;
  errors: string[];
  warnings: string[];
  table: unknown;
}

export interface RagMeta {
  phase: string;
  query: string;
  enabled: boolean;
  hits: { doc_title: string; section: string; source: string; score: number; preview: string }[];
  injected: number;
  note: string;
}

export interface RosterEntry {
  name: string;
  hp?: number;
  why?: string;
}

export interface RosterReport {
  recovered: RosterEntry[];
  eliminated: RosterEntry[];
  suspected: RosterEntry[];
  structure_missing?: string[];
  unnamed: number;
  duplicate: string[];
  prev_total: number;
  new_total: number;
  [key: string]: unknown;
}

export interface NumericFlag {
  kind: "hp_gain" | "hp_zero_alive" | "hp_negative";
  name: string;
  prev?: number;
  new?: number;
  delta?: number;
  [key: string]: unknown;
}

export interface Notice {
  level: string;
  message: string;
}

// 最近一次模型调用的观测元数据（finish_reason / 轮次 / 步数 / token / 用时）
export interface EngineMeta {
  label: string;
  finish_reason: string | null;
  turns: number;
  steps: number;
  tokens: number;
  elapsed_ms: number;
  operation_id?: string;
  trace_runs?: number;
}

export interface Snapshot {
  session_id: string;
  scenario: string;
  turn_count: number;
  time_step: string;
  settings?: { use_agent_tools: boolean; persist_roles: boolean; agent_mode: string; use_live_intel: boolean };
  war_state: WarState | null;
  last_war_state: WarState | null;
  battlefield?: BattlefieldView;
  history: string[];
  review: { text: string | null; reasoning: string | null };
  chat_messages: { role: string; content: string; reasoning?: string }[];
  adjudication: {
    enabled: boolean;
    battle_choice: string | null;
    spec: BattleSpec | null;
    last: Adjudication | null;
    violations: { name: string; team?: string; moved_km?: number; allowed_km?: number; over_km?: number; [key: string]: unknown }[];
  };
  rag: { enabled: boolean; last: RagMeta | null; corpus: { files: number; chunks: number; chars: number; dir: string } };
  integrity: { roster: RosterReport | null; numeric_flags: NumericFlag[] };
  engine_meta?: EngineMeta | null;
  causal?: { record_count: number; latest_trace_id: string | null;
    runtime: { enabled: boolean; connected: boolean; ptc: boolean; mode?: string };
    plugins: { name: string; hooks: string[]; enabled: boolean }[] };
  notices: Notice[];
}

export interface TraceEvent { seq?: number; type: string; time?: number; name?: string; reason?: unknown;
  tools?: string[]; request_config?: { model?: string; maxTokens?: number; reasoningEffort?: string } }
export interface TraceRun { id: string; session_id: string; first_seq: number | null; last_seq: number | null;
  finish_reason: string; event_count: number; events: TraceEvent[]; evidence: string }
export interface DecisionTrace { id: string; turn: number; report: string; time_step: string;
  clock: { status: string; note?: string }; geometry_enabled: boolean; runs: TraceRun[];
  changes: { team: string; name: string; moved_km: number | null; status: string }[];
  decisions: { id: string; team: string; unit_name: string; action: string; rationale: string; expected_effect: string }[] }
export interface MetricContrast { metric: string; factual: number; alternative: number; delta: number }
export interface StatusContrast { team: string; name: string; field: string; factual: unknown; alternative: unknown }
export interface GeometryEffect { turn: number; team: string; name: string; moved_km: number; status_change_count: number;
  source_trace_id: string; metrics: MetricContrast[]; status_changes: StatusContrast[] }
export interface CounterfactualResult { id?: string; mode: string; read_only: boolean; turn: number;
  metrics?: MetricContrast[]; status_changes?: StatusContrast[]; instruction?: string; scope: string;
  samples?: { metrics: MetricContrast[]; admissible: boolean; control: { clock: { status: string } };
    intervention: { clock: { status: string } } }[]; limitations?: string[] }
export interface CausalAnalysis { scope: string; timeline: DecisionTrace[];
  turning_points: { trace_id: string; turn: number; label: string; status_changes: StatusContrast[] }[];
  attributions: { trace_id: string; turn: number; effects: GeometryEffect[]; evaluated_movers: number;
    total_movers: number; interactions: { units: string[]; nonadditive: boolean; joint_status_changes: number; single_counts_sum: number }[] }[];
  explanation: { summary: string; basis: string; hypotheses: { claim: string; evidence_ids: string[]; alternative: string; limitation: string }[] } | null;
  limits: string[]; counterfactuals: CounterfactualResult[] }

export type Position = [number, number];
export interface CountMetric {
  committed: number | null;
  current?: number | null;
  casualties?: number | null;
  noncombat_losses?: number | null;
  operational?: number | null;
  damaged?: number | null;
  destroyed?: number | null;
  name?: string;
  unit?: string;
  valid: boolean;
  note: string;
  source: string;
  carried_forward?: boolean;
  loss_rate: number | null;
  secondary_rate: number | null;
  readiness_rate: number | null;
}
export interface FireRange {
  name: string;
  team: "red" | "blue";
  position: Position;
  max_range_m: number;
  min_range_m: number;
  estimated: boolean;
  source: string;
}
export interface BattlefieldView {
  version: string;
  terrain: {
    label: string;
    factor: number | null;
    source: string | null;
    features: { name: string; kind: string; geometry: string; coordinates: Position[]; elevation_m: number | null; source: string }[];
  };
  fire_ranges: FireRange[];
  fields_of_fire: (FireRange & { bearing_deg: number; arc_deg: number; coordinates: Position[] })[];
  supply_nodes: { id: string; name: string; team: "red" | "blue"; position: Position; source: string }[];
  supply_lines: { name: string; team: "red" | "blue"; coordinates: Position[]; estimated: boolean; status: string; source: string; cut_by: string | null }[];
  losses: {
    units: { name: string; team: "red" | "blue"; status: string; personnel: CountMetric | null; equipment: CountMetric[] }[];
    teams: { team: "red" | "blue"; total_units: number; recorded_units: number; archived_units: number; active_current: number | null; personnel: CountMetric | null; equipment: CountMetric[] }[];
  };
  operational?: {
    note: string;
    units: { team: 'red' | 'blue'; name: string; active: boolean; status: string;
      baseline_hp: number | null; baseline_time: string | null; current_hp: number | null;
      previous_hp: number | null; hp_change: number | null; hp_decline: number | null;
      hp_decline_rate: number | null; morale: number | null; supply: number | null }[];
    teams: { team: 'red' | 'blue'; active_units: number; disabled_units: number;
      hp_recorded_units: number; comparable_units: number; changed_units: number;
      current_hp: number | null; hp_decline: number | null; hp_decline_rate: number | null;
      hp_change: number | null; mean_morale: number | null; mean_supply: number | null }[];
  };
  data_quality?: { key: string; label: string; recorded: number; total: number | null;
    status: 'available' | 'partial' | 'missing'; note: string }[];
  warnings: string[];
  notes: string[];
}

export interface Health {
  build?: { loaded_revision: string; disk_revision: string; restart_required: boolean; started_at: string; pid: number };
  status: string;
  engine: string;
  profile: string;
  model: string;
  base_url: string;
  dsh_home: string;
  harness_started: boolean;
  adjudicator_version?: string;
  runtime?: { enabled: boolean; connected: boolean; ptc: boolean; mode?: string };
}

export interface LlmConfig {
  base_url: string; model: string; max_tokens: number; context_window: number; timeout_seconds: number;
  provider: string; api_key_configured: boolean;
  role_policies: Record<string, { max_tokens: number; reasoning_effort: string | null }>;
  max_steps: number; max_tool_calls: number;
  storage: { path: string; encrypted: boolean; format: string; key_location: string; error: string };
  presets: { id: string; label: string; base_url: string; model: string }[];
}
