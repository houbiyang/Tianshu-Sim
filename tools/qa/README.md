# Release QA tools

Run from the repository root. These tools create evidence under `.cache/qa/`;
never publish that directory because it can contain model outputs and local paths.

## Repeatable offline checks

```bash
.venv/bin/python verify_all.py --with-worker --with-container
conda run -n wargame python verify_release_boundaries.py
node tools/qa/electron_boundary.cjs
node tools/qa/build_proxy.cjs
```

The legacy environment skips desktop checks when the SDK is absent. Run the
desktop suites in the environment containing `requirements-dsh.txt`; inspect
`unittest_skipped` in the result JSON instead of treating task exit codes as
complete coverage.

The build proxy regression covers HTTP/HTTPS downloads, NO_PROXY, checksum
rejection and untrusted TLS certificates using local servers. It validates the
scoped `@electron/get@3.1.0` → `global-agent@4.1.3` override, which removes the
vulnerable `roarr` → `sprintf-js` build dependency chain. Keep full `npm audit`
enabled; remove the override when the upstream downloader no longer needs it.

## Browser acceptance

Build `frontend/`, start `fixture_server.py` with the desktop Python environment,
then serve `frontend/dist` on loopback port 8784. The fixture API uses port 8724.
Use a separate Playwright CLI session and set the backend before loading the UI:

```javascript
async page => {
  await page.addInitScript(() => {
    window.gentactix = { backendUrl: 'http://127.0.0.1:8724' };
  });
  await page.goto('http://127.0.0.1:8784');
}
```

Run `browser_acceptance.js`, followed by `browser_faults.js`, with
`playwright-cli run-code --filename=...`. Check for `### Error` as well as the
reported JSON result: CLI process success alone does not prove browser success.
Responses are fixed fixtures; these checks do **not** establish model quality.

## Real model and packaged desktop

```bash
conda run -n wargame python tools/live_release_smoke.py --live
conda run -n wargame python tools/live_campaign_soak.py --live --turns 20 \
  --out .cache/qa/live-soak
conda run -n wargame python tools/qa/audit_campaign.py \
  --export .cache/qa/live-soak/export.json --dsh-home .cache/qa/live-soak/dsh \
  --out .cache/qa/live-soak/audit.json
conda run -n wargame python tools/qa/live_cancel.py --live \
  --session-export .cache/qa/live-release/export.json
```

These commands use the existing encrypted LLM settings and incur real API usage.
They isolate battles, traces and DSH homes. `--resume` on the first command resumes
its isolated two-turn checkpoint after a later-stage failure; retain the original
failed result separately. A pass does not certify every historical scenario.
The soak runner also supports `--resume`: it continues from the last committed
checkpoint and retains earlier attempts. It restarts the native runtime after
turn 10. The export audit checks every snapshot, including turns committed before
a failed verification; optional loss records and guard warnings remain visible.
Use `--scenario-file <UTF-8-file>` for explicit test conditions. In the summary,
`completed` means the target turn was reached; `passed` additionally requires
every recorded request to have succeeded, including attempts before a resume.
`failed_requests` is retained. Neither flag certifies model narrative quality.

For the 8-unit, 15-minute teaching scenario, `audit_outputs.py --live-dir <dir>
--out <report.json>` checks personnel/equipment accounting, ranges, display
sectors, route discrepancies, clocks and raw-output completeness. It also reports
warnings that require interpretation; zero supply is an index, not an ammunition
count. See the [50-turn audit](../../docs/50步推演与输出审计-2026-10-22.md)
for manual narrative checks and the distinction between structural and semantic
acceptance. These scenario-specific checks are not generic historical validation.

For Linux packages, use a separate Xvfb display and `GENTACTIX_PORT`,
`GENTACTIX_DESKTOP_DATA`, and CDP port. `desktop_key.py` exercises native menus.
Keep the sandbox enabled. Verify file downloads, restart persistence and owned
backend shutdown. Compare published SHA256 digests with the local artifacts.
# 补给与输出一致性回放

`python tools/qa/replay_consistency.py --export <战局导出.json> --out <新目录>` 使用当前规则只读重算全部轨迹，检查地图/内核路径一致、射击几何及正文已知错误。旧越界事件标为rejected，不自动旋转射界；原文件SHA256与失败分类写入summary.json。此工具不进行真实模型续推，也不证明保留下来的自然语言均正确。

## Visual commands and native unit Agents

`verify_commands.py` exercises the pure command domain across all 20 fixed missions;
`verify_command_backend.py` checks permissions, idempotence, atomic checkpoint failure,
crash traces, current-state versions, draft provenance and native/missing-runtime behavior.
The full `verify_all.py` isolates SDK homes and the role index as well as model settings.

`verify_command_recovery.py` adds complete-turn unavailable/retired task cleanup,
unknown-position holds, read-only import preview, disk-only collision protection
and explicit control-scope cleanup. Preview and confirm share the same validator.

For the recovery/fault browser suite, start a separate fixture and Vite on the
ports below. Use a fresh output directory for each run:

```bash
GENTACTIX_QA_PORT=18880 GENTACTIX_QA_OUT="$PWD/.cache/qa/command-recovery-runtime" \
  python tools/qa/command_fixture.py
# In frontend/: npx vite --host 127.0.0.1 --port 18884
playwright-cli -s=recovery open about:blank
playwright-cli -s=recovery run-code --filename=tools/qa/command_recovery_browser.js
```

The seven cases cover stale/late drafts, known 409 rejection, a committed response
lost in transit, scoped comparisons, confirmed permission transitions, and import
preview/cancel/invalid/duplicate/restore. A retry after a lost response must retain
the original batch even if the user refreshes command observations.
`command_desktop.js` runs against that same isolated API in an attached Electron
file renderer; it checks same-turn import refresh, a subsequent turn, and unit
receipt coverage. Native menu shortcuts are exercised with `desktop_key.py` on
an isolated Xvfb display; never use the user's normal desktop profile.

For browser acceptance, run `python tools/qa/command_fixture.py` (18870), serve
`frontend/` using Vite at 18874, then use a separate browser session:

```bash
playwright-cli -s=commands open about:blank
playwright-cli -s=commands run-code --filename=tools/qa/command_browser.js
```

The fixture cannot call a real model. It checks map targeting, Escape/focus return,
continuous tasks, unit/global draft confirmation, cancellation, version conflicts,
and wide/narrow layouts. Inspect `passed:true` and screenshots in
`.cache/qa/commands-browser/`; CLI exit code alone is insufficient.

```bash
python tools/qa/command_live.py --live
```

This explicitly calls the locally configured model through DSH. It reads the root
encrypted configuration and user Vault key without changing them. Isolated games,
DSH journals and summaries go to `.cache/qa/commands-live/`. The run exercises two
native unit identities, observation tools, restart/resume, live cancellation and
retry, explicit memory reset, four-turn deterministic replay and an ordinary open
position-command test. Earlier attempts
are retained. A synthetic teaching pass does not validate historical casualties.

Worker dependency updates also require `npm audit`, `npm run typecheck`, the
Wrangler dry-run with `--containers-rollout none`, and `node tools/qa/worker_runtime.mjs`.
The last check sends a real local HTTP request through Miniflare and is included
in `verify_all.py --with-worker`. The scoped Miniflare→Undici override should be
removed once upstream includes the patched dependency, followed by these checks.

For release packages, start the frozen backend in disposable data/runtime
directories on 18731, then run `python tools/qa/packaged_commands.py --url
http://127.0.0.1:18731 --out .cache/qa/package-checks`. Add `--live` to test DSH
parsing and one native unit with its observation tool. This runner uses HTTP and
does not import the source backend. `packaged_ui.js` expects an actual AppImage
on isolated API port 18732 and an attached CDP browser; it checks commands,
receipts, imports and settings through the bundled renderer/backend. Never point
these runners at the user's live game directory.
