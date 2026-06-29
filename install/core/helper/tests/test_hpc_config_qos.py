"""Tests for sacctmgr-based QOS/account discovery in the installer's hpc-config.

Slurm exposes partitions via `sinfo` but not a user's QOS, so without this the
generated hpc.yaml has `qos: []` and ABA submits no `--qos` → jobs land on the
default QOS (often 8h) and anything longer is rejected. `_discover_qos` fills
that gap. These stub `sacctmgr` on PATH (no real Slurm needed).
"""
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"   # install/core/helper/src
sys.path.insert(0, str(SRC))
from aba_installer import cli  # noqa: E402

# Dispatches the two probes _discover_qos makes. `assoc` is checked first because
# the assoc command's `format=account,qos` also contains the substring "qos".
SACCTMGR_STUB = r"""#!/usr/bin/env bash
case "$*" in
  *assoc*) echo "labacct|long,medium,short,c_long,rapid" ;;
  *qos*)   printf '%s\n' "long|14-00:00:00" "medium|2-00:00:00" "short|08:00:00" \
                         "c_long|14-00:00:00" "rapid|01:00:00" "grid_generic|14-00:00:00" ;;
esac
"""


def test_wall_to_h_parsing():
    assert cli._wall_to_h("14-00:00:00") == 336
    assert cli._wall_to_h("2-00:00:00") == 48
    assert cli._wall_to_h("08:00:00") == 8
    assert cli._wall_to_h("01:00:00") == 1
    assert cli._wall_to_h("") is None
    assert cli._wall_to_h("unlimited") is None
    assert cli._wall_to_h(None) is None


def test_discover_qos_ranks_and_picks_generic_long(tmp_path):
    binr = tmp_path / "bin"
    binr.mkdir()
    s = binr / "sacctmgr"
    s.write_text(SACCTMGR_STUB)
    s.chmod(0o755)
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{binr}:{old}"
    try:
        ranked, walls, account = cli._discover_qos("someuser")
    finally:
        os.environ["PATH"] = old

    assert account == "labacct"
    # Only the user's own QOS appear (grid_generic is NOT in their assoc list).
    assert set(ranked) == {"long", "medium", "short", "c_long", "rapid"}
    assert "grid_generic" not in walls
    # Ranked most-permissive first; among the 14h-day tie, the generic/shorter
    # name 'long' beats the partition-scoped 'c_long'.
    assert ranked[0] == "long"
    assert ranked.index("long") < ranked.index("c_long")
    assert ranked[-1] == "rapid"          # smallest MaxWall (1h) last
    assert walls["long"] == 336 and walls["short"] == 8 and walls["rapid"] == 1


def test_discover_qos_empty_when_sacctmgr_absent(tmp_path):
    # A PATH with no sacctmgr → graceful empty (jobs fall back to default QOS).
    old = os.environ["PATH"]
    os.environ["PATH"] = str(tmp_path / "empty")
    try:
        ranked, walls, account = cli._discover_qos("someuser")
    finally:
        os.environ["PATH"] = old
    assert ranked == [] and walls == {} and account is None
