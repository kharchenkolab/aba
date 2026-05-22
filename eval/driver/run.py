"""Run a scenario through the simulated-scientist driver.

    python eval/driver/run.py --scenario L1-QC --mode fake   # zero-token plumbing
    python eval/driver/run.py --scenario L1-QC --mode live   # Haiku (needs key)

Boots a backend in SINGLE-project mode (the driver hits the API directly — no
browser needed), uploads the scenario seed, runs the loop, writes the log under
eval/runs/driver/.
"""
from __future__ import annotations
import argparse
import importlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER_DIR = Path(__file__).resolve().parent
BACKEND_PORT = 8000

# Load repo .env so both the scientist client (this process) and the booted
# backend (Guide) get ANTHROPIC_API_KEY in live mode.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

sys.path.insert(0, str(DRIVER_DIR))
import driver as driver_mod          # noqa: E402
import scientist as scientist_mod    # noqa: E402
import persona as persona_mod        # noqa: E402

SCENARIOS = {"L1-QC": "scenarios.l1_qc", "L1-DONOR": "scenarios.l1_donor"}


def wait_for(url, timeout=30, name=""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"{name or url} not ready ({last})")


def port_free(port):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally:
        s.close()


def upload(api, csv: Path):
    boundary = "ababoundary"
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{csv.name}"\r\n'
            f"Content-Type: text/csv\r\n\r\n").encode() + csv.read_bytes() + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{api}/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        import json
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="L1-QC")
    ap.add_argument("--mode", choices=["fake", "live"], default="fake")
    ap.add_argument("--budget", type=int, default=0)
    args = ap.parse_args()

    spec = importlib.import_module(SCENARIOS[args.scenario])
    if args.mode == "live" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("live mode needs ANTHROPIC_API_KEY", file=sys.stderr)
        return 2
    if not port_free(BACKEND_PORT):
        print(f"port {BACKEND_PORT} in use — stop the dev backend first", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="aba_driver_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    seed_rel = spec.FAKE["seed"] if args.mode == "fake" else spec.SEED_LIVE
    seed = ROOT / seed_rel
    shutil.copy(seed, data / seed.name)

    env = {**os.environ, "ARTIFACTS_DIR": str(artifacts), "DATA_DIR": str(data),
           "ABA_DB_PATH": str(work / "db.sqlite")}
    if args.mode == "fake":
        env["ABA_FAKE_SESSION"] = str(ROOT / spec.FAKE["fixture"])

    log = open(work / "backend.log", "w")
    backend = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"), env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)

    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", name="backend")
        api = f"http://127.0.0.1:{BACKEND_PORT}/api"
        ds = upload(api, data / seed.name)
        print(f"seeded {ds['id']} ({seed.name}); mode={args.mode}")

        if args.mode == "fake":
            policy = scientist_mod.FnPolicy(spec.script)
        else:
            policy = scientist_mod.LLMPolicy(persona_mod.for_goal(spec.GOAL))

        budget = args.budget or spec.BUDGET
        result = driver_mod.run_scenario(api, policy, budget=budget)
        out = driver_mod.write_run(result, spec.ID, DRIVER_DIR.parent / "runs" / "driver")
        tk = result["tokens"]
        print(f"\nsteps={result['steps']}  milestones={result['milestones']}")
        for who in ("scientist", "guide"):
            u = tk[who]
            print(f"tokens[{who}]: fresh_in={u['input']} cache_read={u['cache_read']} "
                  f"cache_write={u['cache_write']} out={u['output']}")
        print(f"TOTAL processed = {tk['total']}")
        print(f"log → {out}")
        rc = 0
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-1500:], file=sys.stderr)
    finally:
        try: os.killpg(backend.pid, signal.SIGINT)
        except ProcessLookupError: pass
        try: backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(backend.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        log.close()
        print(f"backend log in {work}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
