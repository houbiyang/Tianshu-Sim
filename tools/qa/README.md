# Release QA tools

Run from the repository root. These tools create evidence under `.cache/qa/`;
never publish that directory because it can contain model outputs and local paths.

## Repeatable offline checks

```bash
.venv/bin/python verify_all.py --with-worker --with-container
conda run -n wargame python verify_release_boundaries.py
node tools/qa/electron_boundary.cjs
```

The legacy environment skips desktop checks when the SDK is absent. Run the
desktop suites in the environment containing `requirements-dsh.txt`; inspect
`unittest_skipped` in the result JSON instead of treating task exit codes as
complete coverage.

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
conda run -n wargame python tools/qa/live_cancel.py --live \
  --session-export .cache/qa/live-release/export.json
```

These commands use the existing encrypted LLM settings and incur real API usage.
They isolate battles, traces and DSH homes. `--resume` on the first command resumes
its isolated two-turn checkpoint after a later-stage failure; retain the original
failed result separately. A pass does not certify every historical scenario.

For Linux packages, use a separate Xvfb display and `GENTACTIX_PORT`,
`GENTACTIX_DESKTOP_DATA`, and CDP port. `desktop_key.py` exercises native menus.
Keep the sandbox enabled. Verify file downloads, restart persistence and owned
backend shutdown. Compare published SHA256 digests with the local artifacts.
