# GenTactix-DSH

**开发维护单位：沈阳理工大学装备工程学院深度学习实验室。**

基于 [pystrategylab/Gentactix](https://github.com/pystrategylab/Gentactix) 扩展，感谢原作者开源。
保留上游 MIT 版权和许可证，详见 [署名与致谢](NOTICE.md)。

本分支增加 Vue/Electron 界面、DSH 原生领域工具与控制插件、决策轨迹、归因与反事实分析。
启动后显示中国及周边地图，在战役资料库中选择场景，或通过问答生成想定，确认参数后进入推演工作台。
菜单 **配置 → DSH · LLM配置**（`Ctrl+,`）显示当前生效的接口与模型，可编辑 vLLM/OpenAI 兼容或 DeepSeek Flash 的地址、模型、密钥、上下文、超时和各角色预算。
配置与密钥使用 Fernet 认证加密保存于项目根目录 `.gentactix-llm.enc`（已忽略）；解密密钥位于 `~/.config/GenTactix-DSH/vault.key`，不随项目上传。更换接口不会自动发送原接口密钥。

```bash
python -m pip install -r requirements.txt -r requirements-dsh.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700
# 另开终端启动前端开发服务器
cd frontend
npm ci
npm run dev
```

vLLM 使用实际 `served-model-name`；DeepSeek 推荐 `deepseek-flash`，也支持填写 `deepseek-v4-flash` 兼容别名，参见[官方API文档](https://api-docs.deepseek.com/)。
首次启动可用 `GENTACTIX_LLM_BASE_URL`、`GENTACTIX_LLM_MODEL`、`GENTACTIX_LLM_API_KEY` 环境变量引导；界面保存的加密配置优先。公开版本不含预置私有 API Key。
战局自动写入 `.cache/sessions/`，新版后端重启自动恢复；JSON 导出支持 `POST /api/sessions/import` 导入。
后端 `/api/health` 返回加载版本与磁盘版本，二者不一致会显示重启提示。

Linux 用户可从 [Releases](https://github.com/snow-wind-001/Gentactix-DSH/releases) 下载 AppImage 或便携压缩包，无需另装 Python/Conda。源码打包与加密配置迁移见[桌面使用说明](docs/桌面启动与配置.md)。

当前回归与真实调用证据见 [TESTING.md](TESTING.md) 和[3.1.1发布验收报告](docs/发布验收-3.1.1.md)，插件和分析边界见[架构说明](docs/因果分析与DSH插件架构.md)。以下保留上游功能说明与历史记录。

![启动与战役选择](docs/screenshots/launch.png)

![推演工作台（固定响应界面验收）](docs/screenshots/workspace.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](requirements.txt)

**A teaching sandbox for military history: put a campaign on a real map, then watch the decisions play out round by round.**

> **Ships an optional adjudication kernel, off by default.** A small geometry module can rule on
> movement reach, supply connectivity and encirclement before each turn, then inject those verdicts
> as hard constraints the narrative cannot contradict — same position, same turn, same verdict,
> verifiable by fingerprint. Left off, the sandbox behaves exactly as before. See
> [Deterministic adjudication](#deterministic-adjudication).

Describe a scenario — *"Changping, 262 BC"*, *"Pearl Harbor, 1941"*, or a campaign that never happened — and GenTactix lays the order of battle onto an interactive map. Each round advances the situation and explains why it moved that way; a second AI model sits alongside as chief of staff to critique the decisions taken.

Built with Streamlit, Folium, and DeepSeek.

---

## Scope: what this is, and what it is not

GenTactix is a **teaching aid for military history and strategic thinking**. Its job is to make the
geography of a campaign legible — why a pass mattered, why a supply line was exposed, why an army
that outnumbered its opponent still lost.

It is **not an operations simulator**. An optional adjudication kernel can rule on three things —
how far a unit can move, whether a supply line is cut, and whether a unit is encircled — all of which
are geometry. It is off unless you switch it on. Casualties, morale scores and combat power are
always narrated by a language model, never derived from a model of combat. With the kernel on, two
runs of the same position and turn give the same adjudication; they still do not give the same
casualty figures. Read it as a wargame, or use it to reason about real operations, and it will
mislead you — the figures exist to
make a situation concrete, not to be trusted as estimates.

**Good for**

- Seeing how terrain and distance constrain a plan
- Comparing a historical decision against the alternatives it ruled out
- Asking "what if" and getting an answer that changed because *the decision* changed, not because the
  model changed its mind
- Learning where a claim comes from: every report separates narration from sourcing

**Not for**

- Force-on-force analysis, attrition estimates, or any comparison that needs reproducible numbers
- Operational planning, professional military training, or advice of any kind
- Anything where a wrong number has a cost

---

## Highlights

| | |
|---|---|
| **Tri-model reasoning** | A fast engine model generates each turn as structured JSON; a deeper strategist model writes the after-action review; a reasoning model answers follow-up questions in the sidebar. Each role has an automatic fallback chain so a missing model never kills a session. |
| **Deterministic adjudication** *(opt-in)* | Before each turn, a pure-geometry kernel (`adjudicator.py`, stdlib only) computes how far every unit can move under the current terrain, whether its supply line crosses enemy-controlled ground, and how much of its perimeter is sealed. Verdicts are injected as hard constraints; the narrative cannot contradict them. Same position + same turn ⇒ identical fingerprint. Off by default — switch it on when you want two runs to be comparable. |
| **Real geography, validated** | Unit positions are checked against a land/sea mask *and* SRTM elevation data. A capital ship inland is not a cosmetic bug — the engine forces it back into water before the map is drawn. |
| **NATO-standard map symbology** | Blue/red force markers, movement trails, and directional engagement sectors rendered as Folium overlays with a selectable basemap (satellite by default). |
| **Optional source grounding (RAG)** | A local BM25 index over Markdown battle cards in `corpus/`. When enabled, retrieved passages are injected as *authoritative* context: numbers, dates and names from the sources override the model's memory. Fully offline — no embedding API, no extra key, no cost. |
| **Curated anchor library** | Well-known battles ship with hand-placed high-precision coordinates — Pearl Harbor, Malacca, Midway, the Persian Gulf, and the Battle of Changping (Changping Pass, Dan River valley, Daliang Mountain, Hanwang Mountain, Kongcang Ridge). When a scenario matches, the AI is handed real anchor points instead of hallucinating latitude/longitude. |
| **Full state export** | Every turn — coordinates, unit status, narrative log, staff review — can be exported as structured JSON for post-analysis. |
| **Bring your own key** | No server-side secrets. You paste your own DeepSeek API key into the sidebar; it never leaves the session. |

---

## How it works

```
 scenario text
      │
      ▼
┌──────────────┐   match?   ┌──────────────────┐
│ anchors.py   │───────────▶│ high-precision   │
│ anchor match │            │ coordinate hints │
└──────────────┘            └──────────────────┘
      │ no match                    │
      ▼                             ▼
┌────────────────────────────────────────────┐
│ adjudicator.py — geometry, before the LLM  │
│ movement ceiling / supply cut / perimeter  │
└────────────────────────────────────────────┘
      │ hard constraints
      ▼
┌────────────────────────────────────────────┐
│ prompts.py — assembles the turn prompt     │
└────────────────────────────────────────────┘
      │
      ▼
┌──────────────┐   JSON    ┌──────────────────┐
│ engine.py    │──────────▶│ geo.py           │
│ DeepSeek     │           │ land/sea + SRTM  │
└──────────────┘           │ coordinate audit │
      ▲                    └──────────────────┘
      │  retry with error report (max 3)
      └──────────────────────────┘
      │
      ▼
┌──────────────┐   ┌──────────────────────────┐
│ viz.py       │   │ adjudicator.check_       │
│ Folium map   │   │ movement — overruns shown│
└──────────────┘   └──────────────────────────┘
```

Two audits sit on either side of the model, and they answer different questions. `geo.py` asks
*"is this position physically possible?"* — a destroyer inland gets sent back for correction, up to
three times. `adjudicator.py` asks *"was this position reachable?"* — an overrun is reported in the
panel rather than corrected, because silently dragging a formation back can place it in a river.

The coordinate audit is the interesting part of the first kind. If the model returns a position that
fails validation, the specific error is fed back to the model as a correction request rather than
being silently clamped — the engine gets up to three attempts to place a unit somewhere plausible
before the session gives up on that unit.

---

## Project structure

```
Gentactix/
├── app.py                     # Thin entry point: sidebar, button callbacks, map rendering
├── gentactix/
│   ├── adjudicator.py         # Deterministic kernel: movement, supply, encirclement
│   ├── adjudication_lib.py    # Per-battle scale library: discovery, matching, validation
│   ├── config.py              # Model names, fallback chains, naval-unit helpers
│   ├── anchors.py             # Curated high-precision coordinate library
│   ├── geo.py                 # Land/sea mask + SRTM audit, water enforcement, sanitizers
│   ├── engine.py              # DeepSeek client (cached), completion + retry, review, chat
│   ├── integrity.py           # Roster + numeric conservation: restore omissions, flag drift
│   ├── prompts.py             # Initialization and per-turn prompt builders
│   ├── rag.py                 # Local BM25 retrieval over corpus/*.md
│   ├── state.py               # Session defaults, field extraction, JSON export
│   └── viz.py                 # Folium map, NATO marker HTML, trails, engagement sectors
├── adjudication/              # One file per battle: the geometry scales (data, not code)
├── corpus/                    # Battle cards (Markdown): sources, numbers, disputes
├── verify_adjudicator.py      # Offline guard: geometry, reproducibility, prompt injection
├── verify_adjudication_lib.py # Offline guard: no bleed between battles, validation, round-trip
├── verify_app_integration.py  # Streamlit runtime check: panel renders, toggle actually bites
├── verify_anchors.py          # Anchor-library regression
├── verify_integrity.py        # Offline guard: roster restore, kill channel, numeric flags
├── verify_integrity_panel.py  # Runtime check: panel appears on trouble, stays silent otherwise
├── verify_roundtrip_guard.py  # End-to-end: stubbed model omits a unit, guard must save it
├── measure_turn_degradation.py # Measures prompt growth and roster attrition over N turns
└── requirements.txt           # Python dependencies (pinned with rationale)
```

`app.py` is deliberately thin. All logic lives in the `gentactix` package so it can be tested without a Streamlit runtime. `adjudicator.py` and `adjudication_lib.py` import nothing but the standard library — no Streamlit, no Folium, no network — so verdicts can be reproduced in a bare Python shell.

---

## Run locally

```bash
cd Gentactix

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run app.py
```

Then open http://localhost:8501, paste a DeepSeek API key in the sidebar, and enter a scenario.

**Requirements:** Python 3.10+, a [DeepSeek](https://platform.deepseek.com/) API key.

> SRTM elevation tiles are downloaded lazily on first use and cached locally. The first coordinate audit in a new region can take a few seconds.

**Verify without spending a token.** All of these run offline and need no API key:

```bash
python verify_all.py                # Run every offline regression, workflow and probe check
# Also validate the deployment build (requires npm ci and a running Docker daemon):
python verify_all.py --with-worker --with-container

python verify_adjudicator.py        # 68 checks: geometry, supply cut, encirclement, reproducibility
python verify_adjudication_lib.py   # 59 checks: no bleed between battles, validation, round-trip
python verify_app_integration.py    # 22 checks: kernel stays opt-in, panel renders when on, toggle bites
python verify_anchors.py            # 74 checks: anchor library regression
python verify_integrity.py          # 43 checks: roster restore, kill channel, numeric flags
python verify_integrity_panel.py    # 16 checks: panel appears on trouble, stays silent otherwise
python verify_roundtrip_guard.py    # 14 checks: stubbed model omits a unit, guard must save it
python measure_turn_degradation.py  #  6 checks: prompt growth vs turn count, roster attrition
python verify_engine.py             # SDK response parsing, model fallback, coordinate retries
python verify_features.py           # RAG, session isolation, geography, map layers, JSON export
python verify_workflow.py           # Initialize → advance → review → chat → export, using fixed API responses

python live_adjudication_probe.py --dry-run   # detector self-test: feed it a known-bad response
python live_adjudication_probe.py             # needs DEEPSEEK_API_KEY: does the model obey?
python live_workflow_probe.py                 # Real init/turn/review/chat/export; prompts for key, incurs API usage
```

`verify_all.py` saves individual logs and a JSON summary in `.cache/qa/`; any failed check
returns a nonzero exit code. It never calls a live model or uploads a deployment. See
[TESTING.md](TESTING.md) for the local reproduction steps, coverage and external-service limits.

`verify_adjudication_lib.py` is the one worth reading if you plan to add a battle: it builds two
temporary battles that override *different* keys and asserts neither can see the other's values.
The same file reproduces the old last-write-wins merge and asserts it produces a blend that
neither battle ever specified — so the isolation assertion cannot pass vacuously.

---

## 抗美援朝九次战役资料

新增九份同名配对的 `corpus/` 史料与 `adjudication/` 几何参数，名称统一为
`抗美援朝-战役名称-起止时间`。含来源核对、损失口径差异、简称匹配和近现代兵种分类。
完整清单、使用方法与史实修订见 [抗美援朝资料说明](docs/抗美援朝资料说明.md)。
九场分别提供28个有来源的[地理参考锚点](docs/抗美援朝地理锚点.md)，区分历史地名、现代参考点与工作精度。
史料检索与确定性裁决须分别开启；几何工作值不等于历史战损模型。

## 战场图层与战损展示

地图支持地形底图、地形要素、方向射界、最大/最小射程及补给节点和路线，各图层可单独开关。
“态势与战损”页按双方和单位展示人员战斗/非战斗减员、装备可用/待修/永久损失。
人数和装备采用独立计数，未知数据保持空缺；退场单位保留最后损失记录。
字段结构、比例公式、数据来源与功能边界见[战场图层与战损数据](docs/战场图层与战损数据.md)。
Vue和Streamlit共用同一展示数据模块；射程图形未计算地形遮挡或确定性伤亡。

## Desktop variant: dsh harness + Vue3/Electron

新增[因果分析与DSH插件架构](docs/因果分析与DSH插件架构.md)：原生领域工具、在途取消、分角色预算、
决策轨迹、状态转折候选、几何归因和成对原生分支。进入“因果与轨迹”页使用；各分支保持只读。

A second front door for the same simulation core: a FastAPI backend (`backend/`) whose three model
brains all run through the **DeepSeek Harness** (dsh) Python SDK — `dsh --profile sdk-minimal` as a
subprocess over JSON-RPC — instead of the OpenAI SDK, and a **Vue 3 + Electron** desktop app
(`frontend/`) in place of Streamlit. The Streamlit flow above stays intact; both doors share
`gentactix/`, `corpus/` and `adjudication/`.

The harness talks to a **LAN vLLM** endpoint (OpenAI-compatible). Connection settings live in
`.env.wargame` (gitignored):

```bash
GENTACTIX_LLM_BASE_URL=http://<lan-host>:8081/v1
GENTACTIX_LLM_API_KEY=<key>
GENTACTIX_LLM_MODEL=<served-model-name>
```

**dsh isolation:** the harness runs with `dsh_home` pointed at the project-local
`.dsh-wargame-home/` and `cwd` at `.dsh-wargame-workspace/`. It never reads or writes `~/.dsh`,
so an existing global dsh configuration (providers, profiles, sessions) is untouched.

Run it (conda env `wargame`, Python 3.10+; needs `deepseek_harness`, `fastapi`, `uvicorn`, `SRTM.py`):

```bash
conda activate wargame
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700   # backend only

cd frontend
npm install
npm run dev     # Vite dev server + Electron window (Electron also spawns the backend itself)
npm run build && npm start   # production build
```

The Electron shell starts the backend via `conda run -n wargame python -m uvicorn …`, waits for
`/api/health`, and reuses an already-running backend instead of double-binding the port.

The desktop map is a faithful port of `gentactix/viz.py` (`frontend/src/tactical.ts`): NATO APP-6
style symbols (red diamond / blue rectangle frames with per-branch glyphs and nameplates), march
trails with animated ant paths and `➤` arrows, engagement radii derived from the attacker's range
(× 1.2, unit-type fallback table), directional impact sectors (±30°) that degrade to hollow dashed
rings when no attack bearing is given, radius-tiered colors, and the same tile-layer set — Gaode
stays hidden for its GCJ-02 offset. The caption strip under the map (engagement stats, RAG
injection, roster/numeric conservation) and the adjudication panel keep app.py's analysis wording.

Offline regression for this layer (skips cleanly where `deepseek_harness` is not installed):

```bash
python verify_harness_engine.py   # JSON extraction, coordinate retry, dsh_home isolation, review/chat
```

---

## Usage

> The interface is currently Chinese-only. Scenario input and AI output handle both Chinese and English, but the UI labels are not localized yet.

1. **Initialize** — enter a scenario and press `🚀 Initialize sandbox`. The AI returns the initial order of battle, start time, and geography. With the kernel on, the adjudication panel appears immediately, so you can see which supply line is already exposed before the first shot.
2. **Advance** — press `⏩ Next turn`. With the kernel on it runs first and fixes this turn's movement ceilings; the model then narrates within them. Each round moves the clock forward by the interval you selected (5 minutes to 1 month).
3. **Review** — press `🧐 Call the chief of staff`. A deeper model reads the full battle record and writes a strategic critique, with its reasoning chain available on demand.
4. **Ask** — the staff-review tab includes a chat panel where you can question the current situation (*"which side actually holds the advantage?"*).
5. **Export** — download the entire session as JSON whenever you want to keep the record.

The `⚖️ 启用确定性裁决` toggle in the sidebar switches the kernel on. **It is off by default**, so a
fresh clone behaves exactly like the plain narrative sandbox; turn it on when you want two runs to be
comparable. Off means free narrative: faster to watch, impossible to compare two runs.

Under the toggle, `🎯 战场裁决库` picks which battle's scales to use. It defaults to *auto-match*
(name appears in the scenario text) and lists every file in `adjudication/`. Choose a battle
explicitly when the scenario mentions two of them — *"what if Changping had been fought the Normandy
way"* would otherwise bind Changping's geometry to a question about Normandy's.

---

## Deployment

This variant is a **local build**: it keeps the application code and drops the Cloudflare container
plumbing (`Dockerfile`, `wrangler.jsonc`, `src/index.ts`, `deploy.sh`, CI workflows). Run it with
`streamlit run app.py`; the narrative-sandbox upstream carries the deployment setup if you need it.

---

## Historical grounding (RAG)

A model's memory of a battle is not a source. Enable **史料检索 (RAG)** in the sidebar and the engine
stops guessing: relevant passages from `corpus/*.md` are retrieved and injected into the prompt as
authoritative context.

| | |
|---|---|
| **Retrieval** | BM25 over Markdown sections split at `##`. Chinese is tokenized into character bigrams, so unfamiliar place and person names still match without a dictionary. |
| **Cost** | Zero. No embedding endpoint, no extra API key, fully offline. |
| **When it applies** | Only when the toggle is on *and* at least one passage matches. Every other case is stated in the UI — there is no silent fallback. |
| **Precedence** | Retrieved text outranks the model's memory. Figures the source marks as "not recorded" or "disputed" must stay qualified; the model is forbidden from promoting them to precise values. |

### Adding your own sources

Drop a `.md` file into `corpus/`. Each `##` heading becomes an independently retrievable section;
the `#` heading becomes the source name shown in the sidebar.

```markdown
# Battle of Changping

## Order of battle
State the numbers, and cite the primary source.

## Terrain
Describe the ground the fight was actually fought on.
```

Two rules matter more than the format:

1. **Cite.** A number without a source is worse than no number — it looks authoritative and cannot be checked.
2. **Keep "recorded" and "disputed" apart.** Changping's "400,000" is both what the *Shiji* records and a figure historians contest. If the card only states it, the model will use it as established fact.

`corpus/README.md` documents the full format in Chinese.

---

## Deterministic adjudication

The narrative is allowed to be eloquent. It is not allowed to contradict arithmetic.

Before every turn, `adjudicator.py` reads the current order of battle and answers three questions
with geometry alone. The answers are injected into the prompt **above** the task description — placed
after the task, a model treats them as reference material; placed before it, as preconditions.

| Question | How it is computed | Output |
|---|---|---|
| **How far can this unit move this turn?** | arm-type speed × turn length ÷ terrain factor | `可机动 0.9 km` |
| **Is its supply line intact?** | straight-line path from unit to the rearmost friendly unit; cut if it crosses an enemy control radius | `已切断` / `未切断` / `无后方` |
| **Is it encircled, and can it withdraw?** | 24 bearings sampled to 12 km; share sealed by enemy control radii; withdrawal checks a ±75° rear arc | `被围` / `半包围` / `未被围` + `可退` / `已断` |

Those map onto four discrete fields — supply, encirclement, retreat, morale — which are also the
skeleton of the situation report. Booleans are easy to get right, easy to pin with tests, and easy for
a model to narrate; "the Zhao lost 3,200 men" is none of those things.

### Why only geometry

Because geometry is the part that does not differ between a Bronze Age battle and a 1944 beach
landing. A pike block and an armoured battalion both obey *distance ÷ speed × terrain*. What differs
is the numbers in the table, not the rule — so the kernel is shared and the parameters are per
campaign. Attrition is the opposite: it depends on formation, morale, weapon density and weather, and
a wrong attrition model produces confident wrong answers. It stays in the narrative, where it is
labelled as narration.

### Tuning per campaign

Geometry scales are **data, not code**. One file per battle, under `adjudication/`:

```text
adjudication/
├── README.md           # how to add a battle (Chinese)
├── _template.md        # copy → rename → fill in
└── 长平之战.md          # Changping: contact 3 km, encirclement 12 km
```

```markdown
---
battle_name: 长平之战
era: 战国
contact_km: 3.0               # ≤ this distance counts as contact
control_radius_km: 3.0        # floor for enemy area control (longer range wins)
encircle_radius_km: 12.0      # how far out to look for a sealed perimeter
sealed_ratio_encircled: 0.85  # ≥ share of bearings sealed ⇒ encircled
sealed_ratio_partial: 0.50    # ≥ ⇒ partially encircled
---
```

Drop a file in and it takes effect on the next turn — no registration, no restart, no code change.
A file may list only the keys it wants to differ from the defaults; unlisted keys are **not** copied
into the file, so changing a default later actually reaches every battle that never overrode it.

Resolution order: explicit choice in the sidebar → exact scenario-name match → longest contained
name → `corpus/<battle>.md` frontmatter (legacy location) → defaults. **Once a file is chosen, only
its values are used.** Earlier versions merged every matching file with last-write-wins, which meant
adding a second battle silently rewrote the first one's scales and could produce a blend (Changping's
contact line with Normandy's encirclement radius) that no battle ever specified.

Only the first two keys matter in practice. `contact_km` and `control_radius_km` differ by an order
of magnitude between a crossbow battle (≈0.9 km effective range) and a mechanised one (20 km+
artillery), and a single value across eras does not fail loudly — it quietly reports *no contact*
for formations that were in fact fighting.

Mistakes are reported rather than swallowed: an unknown key, a non-numeric value or an out-of-range
number is listed in the panel and ignored, while the valid keys still apply. A typo leaves a visible
warning instead of a parameter that silently never takes effect.

### Reproducibility, and how to check it

Every adjudication returns a 12-character fingerprint derived from the full result. Same position,
same turn, same turn length ⇒ same fingerprint, byte for byte. Run it twice yourself:

```python
from gentactix import adjudicator
a = adjudicator.adjudicate(state, "30分钟", turn=3)
b = adjudicator.adjudicate(state, "30分钟", turn=3)
assert a["fingerprint"] == b["fingerprint"]
```

The fingerprint is shown in the panel under the map. If a student's "what if" produces a different
verdict, it is because the position changed — not because the model was in a different mood.

### Does the model actually obey?

A constraint written into a prompt is a request, not a guarantee. `live_adjudication_probe.py` runs
one turn against a fixed Changping position and reports four countable outcomes:

| Question | How it is measured |
|---|---|
| Did it respect the movement ceiling? | `check_movement` — actual displacement vs the ceiling given |
| Did it start a fight it could not reach? | any `engagements[].attacker` whose unit was graded `够不着` |
| Did it contradict a supply verdict? | a sentence that names a cut-off unit *and* claims the road is open |
| Is the adjudication stable? | two runs, identical fingerprints |

Run it with `--dry-run` first: that path feeds a deliberately non-compliant response through the same
checks. If the dry run does not flag three violations, the detector is broken — not the model. This
matters because a checker that never fires looks exactly like a model that always obeys.

The supply check is heuristic and sentence-scoped on purpose: whole-text matching flagged "the main
body's supply line is open" as a violation when the *rear* unit was the one cut off. It will miss
sarcasm and negation. Read the report it prints before believing the score.

---

### What the kernel refuses to do

- **It does not correct the model's coordinates.** When a unit moves further than its ceiling, the
  panel reports the overrun and leaves the data alone. Forcing the point back can drop a formation
  into a river and create an error that is harder to explain than the original one.
- **It does not compute casualties.** No attrition equation, no Lanchester model.
- **It does not adjudicate weather, command failure or morale collapse as events.** Those remain
  narrative; only their *preconditions* (cut supply, sealed perimeter) are computed.

### Roster and numeric conservation

Geometry is only half the problem. State is replaced wholesale every turn — `war_state = updated_data`
— so anything the model omits vanishes permanently: the next prompt simply does not contain that name,
and the unit never comes back. Measured on a 12-unit scenario, a 2% omission rate leaves 3 units alive
after 50 turns. `gentactix/integrity.py` guards the two things geometry cannot.

| Check | Behaviour | Why |
|---|---|---|
| **Missing unit** | Restored from the previous turn's snapshot, and reported | The unit had no reason to vanish. Restoring it cancels a deletion that should not have happened — unlike clamping coordinates, it introduces no new information |
| **Eliminated unit** | Allowed, if named in `eliminated` or if last turn's `hp` was already 0 | Without an explicit channel, a guard that restores everything makes units unkillable — worse than losing them |
| **`hp` increase** | Flagged, not corrected | Reinforcement and rest are legitimate; a missed subtraction is not. The system has no basis to decide which one it is looking at |
| **`hp` = 0 still listed** | Flagged | The map still draws a full-strength marker |

The distinction that matters: **restoring a missing unit is not the same as correcting a coordinate.**
Clamping a position invents a state that never existed; restoring a roster entry only undoes an
accidental deletion. That is why one is automated and the other is not.

`verify_roundtrip_guard.py` stubs the model to return a response with one unit missing and another
unit's `hp` raised, then clicks the real "next turn" button. Removing the guard makes 7 of its 14
assertions fail — the test is proven able to catch the regression, not merely green.

---

## Battle report style

A report is read to understand the battle, not to audit the sources. Those are two jobs, so they live
in two fields. Mixing them produced paragraphs that answered a question nobody asked:

> "赵军降卒约四十万系《史记》所载数字、学界有争议；秦军总数《史记》未载，仅记奇兵二万五千、骑五千。"

Every fact there is correct. None of it says what is happening on the ground.

| Field | Carries | Length |
|---|---|---|
| `situation_report` / `report` | Narration: where each army is, what it is doing, who holds the advantage and why, what happens next | 150–200 chars |
| `source_note` | Provenance: the figures, their citations, what is disputed | 30–60 chars |
| `current_time` | The timestamp, nothing else | 8–18 chars |

The body is banned from textual-criticism vocabulary (`史料未载` / `学界有争议` / `据《史记》载` /
`可考` / `存疑`) and from citing works in 《》 brackets; those belong in `source_note`, which renders as
a separate dimmed line under the report. An empty note renders nothing — a stray "考据：" hanging
under every turn would be worse than having no note at all.

The prompt ships a matched pair of ✗/✓ examples, because "write in a narrative register" on its own
produces something half-academic. The model has to see that the target is a causal chain
("they were not trying to stop him, they were letting him go deeper"), not a different adjective.

---

## Model configuration

Model assignments live in `gentactix/config.py`:

```python
MODEL_ENGINE     = "deepseek-v4-flash"   # per-turn JSON simulation
MODEL_STRATEGIST = "deepseek-v4-pro"     # after-action review
MODEL_CHAT       = "deepseek-reasoner"   # sidebar Q&A
```

Each entry has a fallback chain in `MODEL_FALLBACKS`. If a model name is unavailable on your account, the engine degrades to the next model instead of raising.

To point at a different OpenAI-compatible provider, change `DEEPSEEK_BASE_URL` and the model names together.

The desktop variant above ignores these names: its model is whatever the LAN vLLM serves
(`GENTACTIX_LLM_MODEL`), reached through the dsh harness rather than the OpenAI SDK.

---

## Limitations

Listed before you find them yourself. Every item here is a deliberate trade-off, not a bug waiting
to be fixed:

| Limitation | What it means in practice |
|---|---|
| **Only geometry is computed** | Movement ceilings, supply connectivity and encirclement are deterministic (`adjudicator.py`). Casualties, morale points and combat outcomes are not: no attrition equation, no Lanchester model, no logistics curve. Two runs agree on who can reach whom, not on how many died. |
| **Numbers are checked, not conserved** | `integrity.py` flags a rising `hp`, a zeroed unit still on the roster, or a unit listed twice — but it never corrects them. There is no total-force budget across turns, so the casualty ledger drifts. What is guaranteed is that no unit silently disappears. |
| **A wrong parameter table gives a wrong verdict, reproducibly** | The kernel is only as good as `contact_km`, `control_radius_km` and the arm-type speed table. A control radius set too wide will declare ground sealed that never was — and it will do so identically every run, which is worse than a vague answer. |
| **No C2, sensor or terrain database** | Control radii are circles, not front lines. There is no line-of-sight computation, no command delay, no fuel or fodder consumption. Manoeuvre therefore looks cleaner and faster than it was. |
| **12–24 units per side, by design** | Armies are symbolic formations, not troops. Changping's hundreds of thousands collapse into a dozen markers — the map teaches *position*, never *scale*. |
| **Engagement radii are derived, not measured** | The sector radius comes from `attack_range × 1.2`, falling back to a per-arm-type table. It marks *who can reach whom*, not a hit probability. The adjudicator uses the same table, so map and verdict never disagree about reach. |
| **Grounding is not verification** | RAG makes the model quote your corpus instead of its memory, but a wrong corpus produces confident wrong answers. `source_note` tells you which claim came from where; it cannot tell you the source is right. |
| **UI is Chinese-only** | Scenario text and model output handle English; the interface labels do not. |

If you need reproducible *casualty* figures, this is still the wrong tool — a rule-based wargame or a
proper simulation will serve you better. What the optional kernel buys you is reproducible
*positions*: who could reach whom, and whether the road behind them was open. GenTactix is for the
step before attrition maths: understanding what the problem looked like from the inside.

---

## Design notes

- **The scenario is a closed sandbox.** The engine is explicitly instructed not to invent capabilities outside what the scenario establishes, and there is no live web access — DeepSeek's API offers no native browsing. Grounding, when enabled, comes only from the local corpus, never from the network. The "live intel" toggle only biases the model toward injecting unexpected events.
- **The map is rebuilt on every rerun, on purpose.** Folium mutates its own layer tree when the map is serialized, so a cached map object produces a different component hash on each run; Streamlit then tears down and rebuilds the iframe, which reads as a flickering map. Rebuilding from state keeps the hash stable, and the rebuild is cheap next to the map paint.
- **Retrieval can fail quietly, so it is not allowed to.** A disabled toggle, an empty corpus and a zero-hit query each surface a different, visible message. The failure mode being guarded against is not a crash — it is a turn that reads as well-sourced while the model was improvising.
- **Field names are normalized in one place.** Units arrive from the model with either English or Chinese keys; `state.get_unit_field` is the single resolution point, so tolerance for alias drift never has to be re-implemented.
- **Unreadable reports are a field-design failure, not a tone failure.** The first version asked for a 100-character assessment and also demanded that sources be cited inside it. Crowded into one field, the only possible output is compressed noun phrases — which is why adding "be more conversational" to a prompt does nothing, while splitting the field fixes it outright.
- **A unit that vanishes was dropped, not destroyed.** Every turn replaces the whole battle state, so a formation the model forgets to return is gone for good — the next prompt never mentions it again. `integrity.reconcile_roster` refills any unit that disappeared without either an explicit `eliminated` entry or a prior `hp <= 0`, and says so in the UI. Backfilling is not the same as clamping a coordinate: clamping invents a state that never existed, while backfilling undoes a deletion that should not have happened. Numeric anomalies (hp rising, hp at zero but still on the roster) are reported and never rewritten — rising strength may be a legitimate reinforcement, and the system has no basis for guessing which.
- **The kernel is off by default, and that is a product decision.** With it on, the model stops narrating freely and starts narrating inside a boundary — comparable runs, but also visibly duller: at a 30-minute step, infantry in hills can cover 0.9 km, so most turns contain no contact at all. A reader who meets that on first launch concludes the map is broken. Opt-in keeps the default experience honest to what the README claims.
- **The adjudicator never writes back to the map.** Its job is to bound the narrative, not to own the data. When a unit overshoots its movement ceiling the panel reports the overrun and leaves the coordinate untouched — silently dragging a formation back can put it in a river, and a visible inconsistency is easier to reason about than a quiet corruption.
- **The kernel returns states, never scores.** `已切断` / `被围` / `崩溃` are booleans with three values, not percentages. A number invites precision the underlying evidence cannot support; a state can be checked, tested and argued about.

---

## License

[MIT](LICENSE) © 2026 pystrategylab

Free to use, modify, and redistribute — including commercially — provided the copyright notice and license text are retained. Provided without warranty of any kind.

---

**Author:** [pystrategylab](https://github.com/pystrategylab) — *Turning history into something you can see.*
