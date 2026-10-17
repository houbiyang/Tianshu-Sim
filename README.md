# GenTactix

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](requirements.txt)

**An AI-driven wargame simulator that turns historical events and hypothetical conflicts into a live, turn-based tactical sandbox on a real map.**

Describe a scenario — *"Pearl Harbor, 1941"*, *"Normandy, 1944"*, or a conflict that never happened — and GenTactix deploys the order of battle onto an interactive map, then advances the battle round by round while a second AI model sits in the command tent as your chief of staff to review every decision.

Built with Streamlit, Folium, and DeepSeek, and packaged for deployment as a container on Cloudflare.

---

## Highlights

| | |
|---|---|
| **Tri-model reasoning** | A fast engine model generates each turn as structured JSON; a deeper strategist model writes the after-action review; a reasoning model answers follow-up questions in the sidebar. Each role has an automatic fallback chain so a missing model never kills a session. |
| **Real geography, validated** | Unit positions are checked against a land/sea mask *and* SRTM elevation data. A capital ship inland is not a cosmetic bug — the engine forces it back into water before the map is drawn. |
| **NATO-standard map symbology** | Blue/red force markers, movement trails, and directional engagement sectors rendered as Folium overlays with a selectable basemap (satellite by default). |
| **Optional source grounding (RAG)** | A local BM25 index over Markdown battle cards in `corpus/`. When enabled, retrieved passages are injected as *authoritative* context: numbers, dates and names from the sources override the model's memory. Fully offline — no embedding API, no extra key, no cost. |
| **Curated anchor library** | Well-known battles ship with hand-placed high-precision coordinates (e.g. Battleship Row, Ford Island). When a scenario matches, the AI is handed real anchor points instead of hallucinating latitude/longitude. |
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
┌──────────────┐
│ viz.py       │  Folium map + NATO markers + trails
└──────────────┘
```

The coordinate audit is the interesting part. If the model returns a position that fails validation, the specific error is fed back to the model as a correction request rather than being silently clamped — the engine gets up to three attempts to place a unit somewhere plausible before the session gives up on that unit.

---

## Project structure

```
Gentactix/
├── app.py                  # Thin entry point: sidebar, button callbacks, map rendering
├── gentactix/
│   ├── config.py           # Model names, fallback chains, naval-unit helpers
│   ├── anchors.py          # Curated high-precision coordinate library
│   ├── geo.py              # Land/sea mask + SRTM audit, water enforcement, sanitizers
│   ├── engine.py           # DeepSeek client (cached), completion + retry, review, chat
│   ├── prompts.py          # Initialization and per-turn prompt builders
│   ├── rag.py              # Local BM25 retrieval over corpus/*.md
│   ├── state.py            # Session defaults, field extraction, JSON export
│   └── viz.py              # Folium map, NATO marker HTML, trails, engagement sectors
├── corpus/                 # Battle cards (Markdown), indexed at runtime
├── src/index.ts            # Cloudflare Worker wrapper for the container
├── Dockerfile              # Container image for Cloudflare Containers
├── wrangler.jsonc          # Cloudflare deployment config (JSONC)
├── .github/workflows/      # CI: build + deploy on push to main
├── deploy.sh               # Local preflight + manual deploy
└── requirements.txt        # Python dependencies (pinned with rationale)
```

`app.py` is deliberately thin. All logic lives in the `gentactix` package so it can be tested without a Streamlit runtime.

---

## Run locally

```bash
git clone https://github.com/pystrategylab/Gentactix.git
cd Gentactix

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run app.py
```

Then open http://localhost:8501, paste a DeepSeek API key in the sidebar, and enter a scenario.

**Requirements:** Python 3.10+, a [DeepSeek](https://platform.deepseek.com/) API key.

> SRTM elevation tiles are downloaded lazily on first use and cached locally. The first coordinate audit in a new region can take a few seconds.

---

## Usage

> The interface is currently Chinese-only. Scenario input and AI output handle both Chinese and English, but the UI labels are not localized yet.

1. **Initialize** — enter a scenario and press `🚀 Initialize sandbox`. The AI returns the initial order of battle, start time, and geography.
2. **Advance** — press `⏩ Next turn`. Each round moves the clock forward by the interval you selected (5 minutes to 1 month) and returns updated positions, casualties, and a narrative log.
3. **Review** — press `🧐 Call the chief of staff`. A deeper model reads the full battle record and writes a strategic critique, with its reasoning chain available on demand.
4. **Ask** — the staff-review tab includes a chat panel where you can question the current situation (*"which side actually holds the advantage?"*).
5. **Export** — download the entire session as JSON whenever you want to keep the record.

---

## Deployment

GenTactix runs as a container on **Cloudflare Containers**, fronted by a small Worker.

### CI (recommended)

Push to `main` and GitHub Actions builds and deploys automatically.

Add two repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | API token with **Workers Scripts: Edit** and **Cloudflare Containers: Edit** |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |

Then:

```bash
git push origin main
```

### Manual

```bash
./deploy.sh --check     # preflight only, no side effects
./deploy.sh             # requires Docker + a logged-in wrangler
```

### Prerequisites

- A Cloudflare account on the **Workers Paid** plan ($5/mo) — Containers are not available on the free tier.
- The API token must include the **Cloudflare Containers** permission. A Workers-only token will build and upload the image successfully and then fail on `/containers/me` with `401 Unauthorized`.
- Node.js 22+ (wrangler 4.x requirement).

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

## Model configuration

Model assignments live in `gentactix/config.py`:

```python
MODEL_ENGINE     = "deepseek-v4-flash"   # per-turn JSON simulation
MODEL_STRATEGIST = "deepseek-v4-pro"     # after-action review
MODEL_CHAT       = "deepseek-reasoner"   # sidebar Q&A
```

Each entry has a fallback chain in `MODEL_FALLBACKS`. If a model name is unavailable on your account, the engine degrades to the next model instead of raising.

To point at a different OpenAI-compatible provider, change `DEEPSEEK_BASE_URL` and the model names together.

---

## Design notes

- **The scenario is a closed sandbox.** The engine is explicitly instructed not to invent capabilities outside what the scenario establishes, and there is no live web access — DeepSeek's API offers no native browsing. Grounding, when enabled, comes only from the local corpus, never from the network. The "live intel" toggle only biases the model toward injecting unexpected events.
- **The map is rebuilt on every rerun, on purpose.** Folium mutates its own layer tree when the map is serialized, so a cached map object produces a different component hash on each run; Streamlit then tears down and rebuilds the iframe, which reads as a flickering map. Rebuilding from state keeps the hash stable, and the rebuild is cheap next to the map paint.
- **Retrieval can fail quietly, so it is not allowed to.** A disabled toggle, an empty corpus and a zero-hit query each surface a different, visible message. The failure mode being guarded against is not a crash — it is a turn that reads as well-sourced while the model was improvising.
- **Field names are normalized in one place.** Units arrive from the model with either English or Chinese keys; `state.get_unit_field` is the single resolution point, so tolerance for alias drift never has to be re-implemented.

---

## License

[MIT](LICENSE) © 2026 pystrategylab

Free to use, modify, and redistribute — including commercially — provided the copyright notice and license text are retained. Provided without warranty of any kind.

---

**Author:** [pystrategylab](https://github.com/pystrategylab) — *Turning history into a testbed.*
