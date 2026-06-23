# Shared Playwright setup for the OOD round-trip scripts (this box).
# chromium needs 3 conda libs + libgbm + libwayland on LD_LIBRARY_PATH;
# we re-exec under that env if not already set.
import os, sys
PWLIB = "/home/pkharchenko/aba/aba_runtime/.pwlibs/lib"
if PWLIB not in os.environ.get("LD_LIBRARY_PATH", ""):
    os.environ["LD_LIBRARY_PATH"] = PWLIB + ":" + os.environ.get("LD_LIBRARY_PATH", "")
    os.execv(sys.executable, [sys.executable] + sys.argv)  # re-exec with libs visible
BASE = "https://localhost:33000"
AUTH = {"username": "ood", "password": "ood"}
LAUNCH_ARGS = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
