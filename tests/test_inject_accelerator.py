"""Deployment-conditional base: inject-accelerator.sh adds the CUDA pytorch pin to the
conda env spec ONLY when ABA_ACCELERATOR=cuda (else the conda-forge CPU-only default). The
single-source GPU-base mechanism — no drift-prone duplicate env file (docs/arch/envs.md)."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install" / "core" / "inject-accelerator.sh"
BASE = ROOT / "install" / "core" / "environment.yml"


def _run(env_yml: Path, accel: str, cuda_ver: str = "") -> None:
    e = dict(os.environ, ABA_ACCELERATOR=accel)
    e.pop("ABA_CUDA_VERSION", None)
    if cuda_ver:
        e["ABA_CUDA_VERSION"] = cuda_ver
    subprocess.run(["bash", str(SCRIPT), str(env_yml)], env=e, check=True,
                   capture_output=True, text=True)


def test_cpu_is_noop(tmp_path):
    y = tmp_path / "environment.yml"
    y.write_text(BASE.read_text())
    _run(y, "cpu")
    assert "pytorch-gpu" not in y.read_text()


def test_cuda_injects_pin_before_pip_section(tmp_path):
    y = tmp_path / "environment.yml"
    y.write_text(BASE.read_text())
    _run(y, "cuda", "12.4")
    lines = y.read_text().splitlines()
    assert "  - pytorch-gpu" in lines, "CUDA base must pin pytorch-gpu"
    assert "  - cuda-version=12.4" in lines, "ABA_CUDA_VERSION must be pinned when set"
    gi = next(i for i, l in enumerate(lines) if "pytorch-gpu" in l)
    pi = next(i for i, l in enumerate(lines) if l.strip() == "- pip:")
    assert gi < pi, "pytorch-gpu must be a CONDA dependency (before the pip: section)"


def test_cuda_idempotent(tmp_path):
    y = tmp_path / "environment.yml"
    y.write_text(BASE.read_text())
    _run(y, "cuda")
    _run(y, "cuda")
    assert y.read_text().count("pytorch-gpu") == 1
