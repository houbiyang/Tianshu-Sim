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
