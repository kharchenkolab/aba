"""Health check for the `.rds` (Seurat/SCE/pagoda2/conos) conversion bridge.

lstar bridges `.rds` → store through an Rscript; that R must have a WORKING lstar
R package. A package built against a different R (e.g. a stale `r/src/lstar.so`
left over from a system-R build, reused by a non-`--preclean` install) links the
wrong `libR` and SIGSEGVs in the conda tools-env R at first use — a cryptic
crash. This runs the R package in a SUBPROCESS (so a segfault is a clean nonzero
exit, not a crash here) and asserts it loads + reads a store.

Skipped when no lstar-equipped R is present (bridge not set up on this box)."""
import subprocess
import tempfile
from pathlib import Path

import pytest


def _rscript():
    from content.bio.viewers.launchers.pagoda3 import _rscript as r
    return r()


def _lstar_status(rscript: str) -> tuple[str, str]:
    """(status, detail): 'ok' | 'absent' | 'broken'."""
    r = subprocess.run(
        [rscript, "-e", 'x<-suppressWarnings(suppressMessages(require(lstar,quietly=TRUE)));'
                        'cat(if (isTRUE(x)) "LSTAR_LOADS" else "LSTAR_ABSENT")'],
        capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    if "LSTAR_LOADS" in out:
        return "ok", out
    if "LSTAR_ABSENT" in out or "no package called" in out:
        return "absent", out
    return "broken", f"exit={r.returncode} out={out[-300:]}"   # segfault / ABI mismatch lands here


def test_rds_bridge_lstar_r_package_is_healthy():
    rscript = _rscript()
    if not rscript:
        pytest.skip("no R available for the .rds bridge")
    status, detail = _lstar_status(rscript)
    if status == "absent":
        pytest.skip(f"lstar R package not installed in {rscript} — .rds bridge not set up")
    # A 'broken' load (segfault / wrong-libR ABI mismatch) fails LOUDLY here,
    # rather than surfacing as a cryptic crash on the first .rds a user opens.
    assert status == "ok", (
        f"lstar R package present in {rscript} but does NOT load — likely a stale/"
        f"mislinked build (rebuild with `R CMD INSTALL --preclean` in the target R). {detail}")


def test_rds_bridge_can_read_a_store():
    """End-to-end: the bridge R actually reads an lstar store (catches a load that
    succeeds but a C++ read that crashes)."""
    rscript = _rscript()
    if not rscript:
        pytest.skip("no R available")
    if _lstar_status(rscript)[0] != "ok":
        pytest.skip("lstar R package not healthy/installed (covered by the health test)")
    # build a tiny store with the Python lstar, then read it from R
    try:
        import lstar, numpy as np, scipy.sparse as sp
    except Exception:  # noqa: BLE001
        pytest.skip("python lstar unavailable")
    tmp = Path(tempfile.mkdtemp())
    ds = lstar.Dataset(kind="sample")
    ds.add_axis("cells", [f"c{i}" for i in range(20)])
    ds.add_axis("genes", [f"g{i}" for i in range(8)])
    ds.add_field("counts", sp.random(20, 8, density=0.4, format="csr").astype("float32"),
                 role="measure", span=["cells", "genes"], state="raw")
    store = tmp / "s.lstar.zarr"; lstar.write(ds, str(store))
    r = subprocess.run(
        [rscript, "-e", f'suppressMessages(library(lstar)); d<-lstar_read("{store}"); cat("READ_OK")'],
        capture_output=True, text=True, timeout=180)
    assert "READ_OK" in (r.stdout or ""), (
        f"lstar R read failed/crashed (exit={r.returncode}): "
        f"{((r.stderr or r.stdout) or '')[-300:]}")
