import type { Battle, CausalAnalysis, CounterfactualResult, Health, Snapshot, LlmConfig, MissionDefinition,
  CommandView, CommandRevision, OrderDraft, MapPoint, CommandProposal, UnitDecision, ImportPreview } from "./types";

declare global {
  interface Window { gentactix?: { backendUrl: string; onMenuAction?: (callback: (action: 'open-llm-settings' | 'new-scenario') => void) => () => void } }
}

const BASE = window.gentactix?.backendUrl ?? "http://127.0.0.1:8700";

export class ApiError extends Error {
  constructor(public readonly status: number, detail: string) { super(detail); this.name = 'ApiError'; }
}

async function request<T>(method: string, path: string, payload?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* 保留原文 */ }
    throw new ApiError(res.status, String(detail));
  }
  return JSON.parse(text) as T;
}

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  llmConfig: () => request<LlmConfig>('GET', '/api/llm/config'),
  saveLlmConfig: (value: { base_url: string; model: string; api_key: string | null; max_tokens: number; context_window: number; timeout_seconds: number; clear_api_key?: boolean; role_policies?: LlmConfig['role_policies']; max_steps?: number; max_tool_calls?: number }) => request<LlmConfig>('PUT', '/api/llm/config', value),
  testLlm: () => request<{ ok: boolean; model: string }>('POST', '/api/llm/test'),
  planScenario: (messages: { role: string; content: string }[]) => request<{ reply: string; ready: boolean; scenario: string; battle: string; time_step: string; sources: { source: string; section: string }[] }>('POST', '/api/scenarios/plan', { messages }),
  missions: () => request<{ missions: MissionDefinition[] }>("GET", "/api/missions"),
  battles: () => request<{ battles: Battle[] }>("GET", "/api/battles"),
  corpus: () => request<{ files: number; chunks: number; chars: number; dir: string }>("GET", "/api/corpus"),
  sessions: () => request<{ sessions: { session_id: string; turn_count: number; scenario: string; initialized: boolean }[] }>('GET', '/api/sessions'),
  previewImport: (payload: Record<string, unknown>) => request<ImportPreview>('POST', '/api/sessions/import/preview', payload),
  importSession: (payload: Record<string, unknown>) => request<{ session_id: string; turn_count: number }>('POST', '/api/sessions/import', payload),
  deleteSession: (sid: string) => request<{ ok: boolean; session_id: string; audit_retained: boolean }>('DELETE', `/api/sessions/${sid}`),
  createSession: (opts: {
    scenario: string; time_step: string; use_adjudicator: boolean;
    battle: string | null; use_rag: boolean; use_live_intel: boolean;
    use_agent_tools: boolean;
    persist_roles?: boolean;
    agent_mode?: string; simulation_mode?: string; mission_id?: string | null;
  }) => request<{ session_id: string }>("POST", "/api/sessions", opts),
  init: (sid: string) => request<Snapshot>("POST", `/api/sessions/${sid}/init`),
  next: (sid: string, instruction = "", batch?: CommandRevision & { batch_id: string }) => request<Snapshot>("POST", `/api/sessions/${sid}/next`, { instruction, ...batch }),
  commands: (sid: string) => request<CommandView>('GET', `/api/sessions/${sid}/commands`),
  saveCommands: (sid: string, value: CommandRevision & { orders: OrderDraft[]; control_team: string; director: boolean; no_fire: boolean; intent: string; unit_agents: string[]; reset_agents?: string[]; proposal_id?: string }) => request<CommandView>('PUT', `/api/sessions/${sid}/commands`, value),
  cancelCommand: (sid: string, id: string, value: CommandRevision) => request<CommandView>('DELETE', `/api/sessions/${sid}/commands/${id}`, value),
  resolveCommands: (sid: string, value: CommandRevision & { text: string; selected_units: string[]; map_target: MapPoint | null }) => request<CommandProposal>('POST', `/api/sessions/${sid}/commands/resolve`, value),
  unitDecisions: (sid: string, id: string, offset = 0, turn?: number) => request<{ total: number; items: UnitDecision[] }>('GET', `/api/sessions/${sid}/unit-decisions?unit_id=${encodeURIComponent(id)}&offset=${offset}&limit=25${turn === undefined ? '' : `&start_turn=${turn}&end_turn=${turn}`}`),
  openBranch: (sid: string, revision: CommandRevision) => request<Snapshot>('POST', `/api/sessions/${sid}/commands/open-branch`, revision),
  compareCommands: (sid: string, value: CommandRevision & { turn: number; orders: OrderDraft[] }) => request<CounterfactualResult>('POST', `/api/sessions/${sid}/commands/compare`, value),
  review: (sid: string) => request<Snapshot>("POST", `/api/sessions/${sid}/review`),
  chat: (sid: string, message: string) =>
    request<Snapshot>("POST", `/api/sessions/${sid}/chat`, { message }),
  get: (sid: string) => request<Snapshot>("GET", `/api/sessions/${sid}`),
  cancel: (sid: string) => request<{ ok: boolean; note: string }>("POST", `/api/sessions/${sid}/cancel`),
  progress: (sid: string) => request<{ active: boolean; progress: { turns?: number; steps?: number; tokens?: number } }>('GET', `/api/sessions/${sid}/progress`),
  analysis: (sid: string) => request<CausalAnalysis>("GET", `/api/sessions/${sid}/analysis`),
  explain: (sid: string, explain = true) => request<CausalAnalysis>("POST", `/api/sessions/${sid}/analysis`, { explain }),
  counterfactual: (sid: string, payload: { turn: number; mode: string; undo?: { team: string; name: string }[]; instruction?: string; samples?: number; orders?: Record<string, unknown>[] }) =>
    request<CounterfactualResult>("POST", `/api/sessions/${sid}/counterfactual`, payload),
  trace: (sid: string, turn: number) => request<{ persistence: { session_id: string; status: string; found_events?: number; expected_events?: number }[] }>("GET", `/api/sessions/${sid}/traces/${turn}`),
  exportUrl: (sid: string) => `${BASE}/api/sessions/${sid}/export`,
  exportSession: (sid: string) => request<Record<string, unknown>>('GET', `/api/sessions/${sid}/export`),
};
