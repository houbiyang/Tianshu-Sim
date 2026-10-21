# Repository Guidelines

## Project Structure & Module Organization

GenTactix is a Python/Streamlit military-history teaching sandbox with a Cloudflare Containers deployment wrapper.

- `app.py`: thin UI entry point; keep simulation logic in `gentactix/`.
- `gentactix/`: model calls, prompts, geography validation, state, map rendering, retrieval, and adjudication.
- `corpus/`: sourced historical Markdown cards; `##` headings define retrieval chunks.
- `adjudication/`: per-battle geometry parameters; copy `_template.md` when adding a battle.
- Root `verify_*.py`: regression and integration checks.
- `src/index.ts`: Cloudflare Worker/container proxy; `Dockerfile`, `wrangler.jsonc`, and `.github/workflows/deploy.yml` configure deployment.
- `.streamlit/`: UI configuration; `lib/`: bundled browser assets.

## Build, Test, and Development Commands

Use Python 3.10+ (the container uses 3.13) and Node.js 22 for deployment tooling. Run commands from the repository root.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py                 # Local UI at localhost:8501
python verify_adjudicator.py         # Offline geometry regression
python verify_adjudication_lib.py    # Parameter validation and isolation
python verify_app_integration.py     # Streamlit integration checks
npm ci                              # Install Worker dependencies
npm run typecheck                   # Strict TypeScript validation
npm run dev                         # Local Wrangler development; requires Docker
npm run deploy                      # Build and deploy Worker/container
```

## Coding Style & Naming Conventions

Follow surrounding code: four-space indentation and `snake_case` functions/modules in Python; tabs and double-quoted strings in TypeScript. Use `PascalCase` classes and `UPPER_SNAKE_CASE` constants. No formatter or linter is configured. Preserve Chinese UI text and comments where appropriate. Keep `adjudicator.py` and `adjudication_lib.py` standard-library-only, and retain adjudication's default-off behavior.

## Testing Guidelines

Tests are standalone scripts with explicit checks; UI integration uses Streamlit `AppTest`. Name regressions `verify_<feature>.py`. Run relevant scripts and all root `verify_*.py` scripts for cross-cutting changes; run `python measure_turn_degradation.py` for prompt/state changes. These checks need no API key. No coverage threshold is configured. Add regressions for changed behavior, particularly roster preservation and battle-parameter isolation. The live probe requires `DEEPSEEK_API_KEY` unless invoked with `--dry-run`.

## Commit & Pull Request Guidelines

Follow Git history's Conventional Commit pattern: `feat(anchors): ...`, `fix(state): ...`, `test(adjudication): ...`, or `docs: ...`. Use imperative summaries. PRs should explain behavior changes, link relevant issues, list verification results, and include screenshots for UI/map changes.

## Security & Configuration

Keep API keys, `.env`, `.dev.vars`, and `.streamlit/secrets.toml` untracked. Use GitHub Actions secrets for Cloudflare credentials. Keep historical citations in `corpus/` and estimated geometry scales in `adjudication/`.
