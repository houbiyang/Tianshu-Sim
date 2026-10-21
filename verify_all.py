"""Run all offline checks; optionally compile the Worker and verify its image.

    python verify_all.py
    python verify_all.py --with-worker --with-container

Logs and machine-readable results are saved under .cache/qa/. No deployment or
live model calls are made. Python dependencies must already be installed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / ".cache" / "qa"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-worker", action="store_true", help="typecheck and bundle Worker without uploading")
    parser.add_argument("--with-container", action="store_true", help="build Docker image and check runtime battle data")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    env = os.environ.copy()
    # Do not let an activated ROS/system installation leak packages into the venv.
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["WRANGLER_SEND_METRICS"] = "false"

    def run(name, command, timeout=180):
        started = time.monotonic()
        path = OUTPUT / f"{name}.log"
        error = ""
        try:
            with path.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, error = 1, str(exc)
            with path.open("a", encoding="utf-8") as log:
                log.write(f"\n{error}\n")
        results.append({"name": name, "exit_code": code,
                        "seconds": round(time.monotonic() - started, 2),
                        "log": str(path.relative_to(ROOT)), "error": error})
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({results[-1]['seconds']}s)", flush=True)
        if code:
            print(path.read_text(encoding="utf-8", errors="replace")[-3500:], flush=True)
        return code == 0

    run("dependencies", [sys.executable, "-m", "pip", "check"])
    for path in sorted(ROOT.glob("verify_*.py")):
        if path.name != Path(__file__).name:
            run(path.stem, [sys.executable, str(path)])
    run("measure_turn_degradation", [sys.executable, "measure_turn_degradation.py"])
    probe_path = OUTPUT / "probe_result.json"
    if run("live_probe_dry_run", [sys.executable, "live_adjudication_probe.py", "--dry-run", "--out", str(probe_path)]):
        payload = json.loads(probe_path.read_text(encoding="utf-8"))["checks"]
        if not (payload["movement_violations"] and payload["engagement_overreach"]
                and payload["supply_narrative_hits"] and payload["fingerprint_stable"]):
            results[-1]["exit_code"] = 1
            results[-1]["error"] = "Dry-run detector did not identify the deliberately invalid fixture"
            print("FAIL dry-run detector assertions", flush=True)

    if args.with_worker:
        npm, npx = shutil.which("npm") or "npm", shutil.which("npx") or "npx"
        run("typecheck", [npm, "run", "typecheck"])
        run("worker_build", [npx, "wrangler", "deploy", "--dry-run", "--containers-rollout", "none",
                             "--outdir", str(OUTPUT / "worker")])
    if args.with_container:
        run("docker_build", ["docker", "build", "--platform", "linux/amd64", "-t", "gentactix:qa", "."], timeout=900)
        check = (
            "from pathlib import Path; from gentactix import adjudication_lib as lib, rag; "
            "spec = lib.resolve('长平之战'); "
            "assert Path('/app/logo.png').is_file(); "
            "assert Path('/app/.streamlit/config.toml').is_file(); "
            "assert spec.source_kind == 'library' and spec.calibrated, spec; "
            "assert rag.search('长平之战 白起'); "
            "print('Container assets, historical retrieval and battle parameters verified')"
        )
        run("docker_runtime_data", ["docker", "run", "--rm", "--entrypoint", "python", "gentactix:qa", "-c", check])

    failed = sum(result["exit_code"] != 0 for result in results)
    (OUTPUT / "results.json").write_text(json.dumps({
        "python": sys.version, "passed": len(results) - failed, "failed": failed,
        "live_model_calls": False, "results": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(results) - failed}/{len(results)} checks passed. Logs: {OUTPUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
