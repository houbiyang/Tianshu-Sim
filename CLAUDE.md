# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

GenTactix is a military-history teaching sandbox: a scenario description becomes an order of battle on a real map, advanced round by round by configured language models, with an optional deterministic geometry adjudication kernel. It has two front doors over the same core: the original **Streamlit** app (`app.py`) and a **dsh-harness + Vue3/Electron desktop** variant (`backend/` + `frontend/`, conda env `wargame`). UI text and most comments are Chinese; preserve them. `AGENTS.md` carries the repo's style/commit conventions; this file covers commands and the architecture that spans multiple files.

## Commands

All Python commands use the venv (`.venv/bin/python`, `.venv/bin/streamlit`). Tests need no API key.

```bash
.venv/bin/python verify_all.py                 # every offline regression + probe dry-run
.venv/bin/python verify_all.py --with-worker --with-container   # also Worker typecheck/bundle + Docker build
.venv/bin/python verify_adjudicator.py         # any single check: verify_<feature>.py scripts run standalone
.venv/bin/streamlit run app.py                 # local UI at localhost:8501
npm run typecheck                              # strict TS for the Worker shell
python live_adjudication_probe.py --dry-run    # detector self-test; without --dry-run needs DEEPSEEK_API_KEY
python live_workflow_probe.py                  # real end-to-end run; incurs API usage
```

`verify_all.py` writes logs and `results.json` under `.cache/qa/`, strips inherited `PYTHONPATH` (a local ROS install can poison dependency checks), and never calls a live model or deploys. `Gentactix.py.legacy` is the pre-refactor monolith kept for reference — do not edit it.

The desktop variant runs in **conda env `wargame`** (no Streamlit installed there):

```bash
conda run -n wargame python verify_harness_engine.py    # harness-layer regression (skips in .venv)
conda run -n wargame python verify_desktop_backend.py  # API, rollback, battle selection, display/export
.venv/bin/python verify_anchors.py                     # legacy + nine Korean campaign coordinates
.venv/bin/python verify_battlefield.py                 # derived display, counts and retention
conda run -n wargame python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700
cd frontend && npm run dev                              # Vite + Electron (Electron spawns the backend itself)
cd frontend && npm run typecheck && npm run build       # vue-tsc strict + production bundle
```

## Architecture

### The turn pipeline

`app.py` is deliberately thin (sidebar, button callbacks, rendering); all logic lives in `gentactix/` so it is testable without a Streamlit runtime. Initialization and turn advancement share these stages (anchors apply at initialization):

1. **anchors.py** — known reference coordinates are handed to the model with their names. `korean_anchors.json` supplies 28 sourced points for nine campaigns, selected through the same canonical-name/alias resolver as adjudication. The prompt includes coordinates, sources and working uncertainty. Korean references use explicit `node_name`; mentioning an attack objective in a unit name must not snap attackers onto it. Town references are not surveyed 1950s fortifications.
2. **adjudicator.py** (only when the sidebar toggle is on) — pure-geometry verdicts (movement ceiling, supply cut, encirclement) computed *before* the LLM call and injected into the prompt **above** the task description, so the model treats them as preconditions, not reference material.
3. **prompts.py** — assembles the turn prompt (plus `rag.py` BM25 passages from recursively indexed `corpus/**/*.md` when the RAG toggle is on; retrieved text outranks model memory).
4. **engine.py** — DeepSeek call returning structured JSON. Three model roles (engine/strategist/chat, names in `config.py`) each have a fallback chain; a successful fallback is memoized in `_MODEL_DEGRADED` so later turns reuse it. Client objects are cached per API key.
5. **geo.py** — coordinate audit against a land/sea mask + SRTM elevation. Invalid positions trigger a retry loop with the error fed back to the model (`get_ai_response_with_retry`, max 3 attempts).
6. **integrity.py** — roster reconciliation and numeric flags (see invariant below).
7. **battlefield.py** — carries forward partial counts/static overlays and archives retired-unit records before committing a candidate. Only a successful candidate replaces the live snapshot; failure must leave state, turn, history and adjudication unchanged.
8. **viz.py / battlefield_viz.py** — Folium map and shared derived battlefield overlays; the Vue map consumes the same `build_battlefield_view` DTO. **adjudicator.check_movement** is a post-hoc overrun report.

The two audits have deliberately opposite policies: `geo.py` *corrects* impossible positions (a warship inland) via retry, while `adjudicator.py` only *reports* movement overruns and never writes back — silently dragging a formation back can place it in a river. The kernel never mutates `war_state`.

### Central invariant: state is replaced wholesale

Every turn does `st.session_state.war_state = updated_data` — a full replacement, not a merge. A unit the model forgets to return is gone forever (the next prompt never mentions it). This is why `integrity.reconcile_roster` exists:

- **Missing unit** → restored from the previous snapshot and reported. Restoring undoes an accidental deletion; it introduces no new information (unlike clamping a coordinate, which invents a state that never existed).
- **Eliminated unit** → allowed only if named in the top-level `eliminated` array or if its previous `hp` was already ≤ 0. Without this explicit channel, "restore everything missing" makes units immortal.
- **Numeric anomalies** (hp rising, hp=0 still on roster) → flagged, never corrected; the system cannot tell reinforcement from a missed subtraction.

Any change to state handling must keep these guards working — `verify_roundtrip_guard.py` and `verify_integrity.py` exist to prove it, and their assertions are proven to fail when the guard is removed. Prefer tests that can fail over tests that are merely green.

`battlefield.carry_battlefield_state` handles nested personnel/equipment updates and `loss_records` across retirement/reentry. Cumulative `committed` counts may not decrease: reject the candidate instead of silently resetting the denominator. Active/archive duplicates count once, with active data taking precedence. This tracks abstract formations, not individual soldiers or equipment serial numbers.

### Deterministic adjudication is opt-in and default-off

The sidebar toggle `⚖️ 启用确定性裁决` is off by default; a fresh clone behaves exactly like the plain narrative sandbox. Retain that default-off behavior. The kernel outputs only discrete states (`已切断` / `被围` / `可退`), never scores, and returns a 12-char fingerprint so same position + same turn ⇒ byte-identical verdicts. It never computes casualties — attrition stays in the narrative layer.

`adjudicator.py`, `adjudication_lib.py`, `integrity.py`, and `rag.py` must stay **standard-library-only** (no streamlit/folium/network) so verdicts reproduce in a bare Python shell.

### Per-battle parameters are data, not code

Geometry scales live in `adjudication/<battle>.md` frontmatter (copy `_template.md`; no registration or restart needed). Resolution order: explicit sidebar choice → exact scenario-name match → longest contained name → legacy `corpus/<battle>.md` frontmatter → defaults. **Once one file is selected, only its values are used** — never merge across files; the old last-write-wins merge let a second battle silently rewrite the first one's scales. Invalid keys/values are reported in the panel, not swallowed. `verify_adjudication_lib.py` enforces cross-battle isolation.

Two keys steer movement/pace, not just geometry: `terrain_factor` (when written it **overrides** the model's self-reported `current_terrain` label — models routinely mark river-valley hills as "plains" ×1.0; the kernel reports `terrain_source` so the panel shows which won) and the meta key `time_step` (a recommended turn span; `/api/battles` returns it and the Vue door pre-fills the span when a battle is picked — a 30-min turn leaves ancient armies crawling 1–4 km and they can never make contact).

Flat frontmatter `aliases` is a `|`-delimited list; aliases resolve to one canonical file and do not create duplicate catalog entries. Both front doors pass the selected battle into anchors and retrieval. In geo-3, `can_contact` requires distance <= per-turn movement reach + `contact_km`; this is a contact band, not ranged-fire eligibility. `sim_clock.py` owns elapsed simulation time: initialized explicit dates become canonical; ambiguous historical calendars retain their origin with a T+ elapsed label. A candidate must match the exact next program timestamp before commit. Rejecting a 105-minute candidate for a 15-minute turn must preserve the previous state; never silently relabel its actions.

### Field-name normalization

Units arrive from the model with either English or Chinese keys. `state.get_unit_field` is the single field-alias resolution point — never re-implement alias tolerance elsewhere. **`unit_types.classify_unit` is the single modern unit-type resolver**; Python adjudication/rendering and Vue share `unit_types.json` (specific keywords precede generic infantry). Use `config.is_naval` and `geo.UNIT_TEAMS` rather than duplicating those contracts. `modern_unit_instruction` requires Korean War context; bare 1950–1953 years no longer trigger it.

### Battlefield display and loss accounting

`battlefield.py` is standard-library-only and derives read-only terrain point/line/polygon features, spherical max/min fire ranges, known directional sectors, supply lines/nodes, and loss tables. `battlefield_viz.py` adapts that DTO to Folium; Vue uses `MapView.vue` and `BattlefieldPanel.vue`. Mirror behavior in both front doors. Supplied routes take precedence over node straight lines and geometric rear estimates; estimates must stay labeled. Unknown terrain, direction or counts must remain unknown.

Personnel casualties and noncombat losses each divide by their own unit's cumulative personnel `committed`; equipment destroyed, damaged and operational each divide by that category's equipment `committed`. Aggregate by summing matching numerators/denominators, never averaging percentages. HP is not a head count. Drawing terrain or a cut supply route does not implement DEM line of sight, transport throughput, ballistics or deterministic damage; `battlefield.CAPABILITY_NOTE` constrains review/chat claims and states the exact formulas.

### Report style is a field split, not a prompt adjective

`situation_report`/`report` carries narration only; provenance goes in `source_note` (banned vocabulary like `史料未载`/`学界有争议` in the body). If report output reads like a literature review, fix the field split in `prompts.py` (`REPORT_STYLE_RULES`), not the wording.

### Deployment

Cloudflare Containers shell: `src/index.ts` is a Worker that routes everything to a **single fixed-name container** (`max_instances: 1`) running Streamlit — session isolation is Streamlit's job, not one-container-per-user. The Dockerfile pins `python:3.13-slim`, `linux/amd64`, binds `0.0.0.0`, and disables CORS/XSRF because the Worker rewrites headers and would otherwise break the `_stcore/stream` WebSocket. `corpus/` and `adjudication/` data must be copied into the image (checked by `verify_all.py --with-container`). CI is `.github/workflows/deploy.yml`.

### Desktop variant: dsh harness + Vue3/Electron

`backend/harness_engine.py` replaces `engine.py`'s direct OpenAI calls: all three model brains go
through the **DeepSeek Harness Python SDK** (`DeepSeekHarness`, `profile="sdk-minimal"`, one lazily
started subprocess speaking JSON-RPC over stdio) pointed at a LAN vLLM endpoint. Connection config
comes from `backend/llm_settings.py`: root `.gentactix-llm.enc` (Fernet) overrides environment bootstrap values. Its 0600 key stays outside the project in the XDG user config directory; never package or publish either. The configuration API never returns the API key. A lost key/tampered vault produces a visible configuration error and leaves the UI recoverable. **`dsh_home` is an isolated project or desktop profile directory — never `~/.dsh`**; `verify_harness_engine.py` asserts this, and the existing
global dsh configuration must stay untouched. The coordinate-reflection loop and JSON-extraction
semantics mirror `engine.py` deliberately. Every model call passes one gate (`_gate`): a
`finish_reason` outside `{"completed"}` (error/max-tokens/None) is a hard failure — a half-written
JSON is more dangerous than a visible 502. Reasoning blocks and `usage.totalTokens` are extracted
from the event stream (`RunObserver`), never discarded; the observer feeds progress notices, a
one-time 70%-context warning, and `engine_last_meta` (surfaced as `engine_meta` in the snapshot).

Role sessions: when `GENTACTIX_PERSIST_ROLES` is on (or the session was created with
`use_agent_tools`), each brain reuses one DSH session per battle+role
(`gentactix-{battle_id}e{epoch}-{engine|strategist|chat}`); re-init bumps the epoch because DSH has
no session-clear API. A failed `resume_session` (stale lock/missing) falls back to a fresh session
with a *visible* warning plus a `decision_log` re-injection — silent amnesia is the failure being
guarded against.

`backend/agent_tools.py` supplies shared read-only domain executors and the compatibility JSON protocol: the model replies with
`{"tool_calls":[…]}` (≤4/step, ≤4 steps) or `{"action":"submit_orders","orders":{…}}`, the backend
executes read-only/dry-run tools (`get_observation`, `retrieve_history`, `evaluate_action` — the
latter deepcopies before probing, so it uses the *same* `geo`/`check_movement` functions as the
post-hoc audit) and feeds results back into the same session. Agent mode is default-off and only
changes the single branch point that produces the candidate state; the downstream guard chain
(sanitize → reconcile → numeric → post-adjudicate → check_movement → atomic commit) is fully
shared, so the wholesale-replacement invariant holds either way. New desktop sessions default to
continuous role memory and select native tools when the agent toggle is enabled; older API callers
can retain JSON mode. `plugins/dsh-gentactix` is a real Cordis plugin loaded by a per-launch patch,
registering five native read-only tools, `agent/request` token/effort policy and step budgets.
It disables shell/image/directory tools in the selected runtime without changing `~/.dsh` or generated profiles.
`runtime_bridge.py` binds each native tool to a detached operation snapshot. Cancellation bypasses
`_OP_LOCK` and reaches native `Agent.cancel` through the plugin's authenticated local control channel;
without it the existing step-boundary flag remains. The installed SDK 0.2.1rc2 rejects raw
`client.request('session/cancel')` and `'session/fork'`; don't confuse core/Web capabilities with SDK RPC exposure.

`backend/extensions.py` exposes detached application hooks for trace persistence, clock audit and causal
attribution, selected by `plugins/runtime.json`. Required trace persistence finishes before battle commit.
`gentactix/causal.py` is standard-library-only: observed deltas, geometric status transitions, controlled
single/pair movement ablations. These are sensitivity analyses, not deterministic casualty or victory attribution.
`analysis_routes.py` and `CausalPanel.vue` expose evidence-linked interpretation and paired model branches.
Each pair inherits the exact same completed **predecision** DSH prefix using public `AgentRegistry.create`
seed/parent metadata; no factual postdecision result enters either branch. Native events and parent lineage
are persisted by DSH. Branch state is reconciled in isolation and never replaces the main battle.
Events have session IDs and seq ranges; public traces omit raw reasoning. Native JSONL can lag notifications,
so trace verification reports `partial` until the owner flushes; it must never claim verification from an API 200 alone.

Role policies use `GENTACTIX_{ENGINE|STRATEGIST|CHAT|CAUSAL|COUNTERFACTUAL}_MAX_TOKENS` and
`..._REASONING_EFFORT` (unset preserves adapter defaults). Max tokens were already 16384 globally;
chat now defaults to 2048, strategist 8192, causal explanation 4096, engine/branches retain 16384.
PTC is not enabled: native registered tools have explicit schemas; no arbitrary model-authored code is needed.
See `docs/因果分析与DSH插件架构.md` for contracts and exact live evidence.

`backend/main.py` is a FastAPI mirror of `app.py`'s pipeline (anchors → adjudicator → RAG → harness
→ geo retry → integrity), holding sessions in a dict and returning one `Snapshot` per call so the
frontend stays dumb. Engine-side notices that would have been `st.toast` in Streamlit collect in
`ENGINE_NOTICES`/`geo.GEO_NOTICES` and ride along in the snapshot (`drain_notices`). `geo.py` and
`state.py` import cleanly without Streamlit (guarded import) — keep both paths working.

`frontend/` is Vite + Vue 3 + TS + Leaflet; `electron/main.cjs` spawns the backend via
`conda run -n wargame` but **skips the spawn when `/api/health` already answers** (debug uvicorn
reuse). The map is a faithful port of `viz.py`: `frontend/src/tactical.ts` holds the NATO
APP-6 symbol HTML, engagement-radius derivation (attacker range × 1.2 → unit-type table →
explicit → neutral), directional sector / degraded hollow ring, and the tile-layer set (Gaode
stays hidden for its GCJ-02 offset). **Any map change must be mirrored against `viz.py`** — both
doors must speak the same map language. The caption strip under the map (engagement stats, RAG
injection, roster/numeric conservation) and the full adjudication panel replicate app.py's
analysis layer; keep their wording parity. The Streamlit door and its
`verify_workflow.py`/`verify_app_integration.py` checks remain the reference behavior for the
backend's endpoint semantics.

### RAG and retrieval visibility

`rag.py` is BM25 over recursively indexed `corpus/**/*.md`, chunked at `##` headings, Chinese tokenized as character bigrams + single chars (no dictionary). `search(query, battle=...)` applies battle scope **before ranking**: other Korean campaign cards are excluded while shared nested reference docs remain eligible. Main/app initialization, turn retrieval and the agent history tool must pass the current selection. Every retrieval failure mode (toggle off, empty corpus, zero hits) must surface a distinct visible message — a turn that reads well-sourced while the model improvised is the failure being guarded against.

Python source edits require a new backend process; refreshing Vite cannot update an already-running API. Sessions are process-local memory and exports are not automatic restart recovery. Test new code in an isolated fresh process when existing battles need preservation. `TESTING.md` distinguishes offline fixtures from actual DSH/vLLM calls; do not infer model identity from the SDK brand (the latest live run used configured Qwen).
