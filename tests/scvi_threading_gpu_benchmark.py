"""Empirical benchmark + regression test for scvi-tools threading / GPU use.

Two things are demonstrated:

1. Root cause of the "single CPU core pegged" symptom: when the kernel/process
   environment forces ``OMP_NUM_THREADS=1`` (jupyter/jax/stale env), torch comes
   up single-threaded (`torch.get_num_threads()==1`). The kernel-startup fix
   (`_kernel_env` in core/exec/kernels/jupyter.py) sets the BLAS/OMP thread vars
   to a sane count so no run_python cell is ever accidentally single-core.

2. Root cause of "poor GPU utilisation": scvi defaults of `dl_num_workers=0`
   (single-process host-side data loading) + small `batch_size=128` + CPU-side
   sparse->dense collation starve the GPU. The recipe fix uses a multi-worker
   loader, a larger batch, `load_sparse_tensor=True` (densify on the GPU), mixed
   precision, and an explicit `accelerator`.

Run directly:  .venv/bin/python tests/scvi_threading_gpu_benchmark.py
(the scvi/torch overlay is appended to sys.path automatically, matching how the
run_python kernel composes the .venv with the materialised pylib overlay).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

# Compose the .venv with the materialised pylib overlay (torch/scvi live there),
# exactly like the run_python kernel preamble does.
_OVERLAY = "/workspace/aba-runtime/envs/pylib"
if _OVERLAY not in sys.path:
    sys.path.append(_OVERLAY)

# Thread count the kernel fix injects (mirrors _kernel_threads in jupyter.py).
_KERNEL_THREADS = str(min(os.cpu_count() or 4, 8))


def _subprocess_threads(env_overrides: dict) -> int:
    """torch.get_num_threads() inside a fresh interpreter with the given env."""
    env = dict(os.environ)
    env.update(env_overrides)
    code = (f"import sys; sys.path.append({_OVERLAY!r});"
            "import torch; print(torch.get_num_threads())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, timeout=120)
    return int(out.stdout.strip().splitlines()[-1])


def _gpu_util_once() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return float(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None
    return None


class _Sampler:
    """Background sampler of GPU util + per-core CPU during a training run."""

    def __init__(self, period_s: float = 0.2):
        import psutil
        self._psutil = psutil
        self.period_s = period_s
        self._stop = threading.Event()
        self.gpu: list[float] = []
        self.percore: list[list[float]] = []
        self._t: threading.Thread | None = None

    def _loop(self):
        self._psutil.cpu_percent(percpu=True)  # prime delta counters
        while not self._stop.is_set():
            g = _gpu_util_once()
            if g is not None:
                self.gpu.append(g)
            self.percore.append(self._psutil.cpu_percent(percpu=True))
            self._stop.wait(self.period_s)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)

    def report(self) -> dict:
        gpu = self.gpu or [0.0]
        peak_busy = max((sum(1 for c in s if c > 50.0) for s in self.percore),
                        default=0)
        return {
            "gpu_mean": round(sum(gpu) / len(gpu), 1),
            "gpu_peak": max(gpu),
            "peak_concurrent_busy_cores": peak_busy,
        }


def _make_adata(n_cells=30000, n_genes=2000, seed=0):
    import numpy as np
    import anndata as ad
    import scipy.sparse as sp
    rng = np.random.default_rng(seed)
    lam = rng.gamma(shape=0.3, scale=2.0, size=(n_cells, n_genes))
    X = sp.csr_matrix(rng.poisson(lam).astype("float32"))  # sparse counts
    adata = ad.AnnData(X)
    adata.obs["batch"] = (np.arange(n_cells) % 2).astype(str)
    adata.layers["counts"] = X.copy()
    return adata


def _train_once(adata, *, dl_num_workers, num_threads, accelerator, batch_size,
                load_sparse_tensor, precision, matmul, max_epochs=12, tag=""):
    import scvi
    import torch

    if num_threads is not None:
        scvi.settings.num_threads = num_threads        # -> torch.set_num_threads
    scvi.settings.dl_num_workers = dl_num_workers
    scvi.settings.dl_persistent_workers = dl_num_workers > 0
    if matmul:
        torch.set_float32_matmul_precision(matmul)

    scvi.model.SCVI.setup_anndata(adata, layer="counts", batch_key="batch")
    model = scvi.model.SCVI(adata, n_latent=20, n_hidden=256, n_layers=2)

    torch_threads = torch.get_num_threads()
    t0 = time.time()
    with _Sampler() as smp:
        model.train(max_epochs=max_epochs, accelerator=accelerator, devices=1,
                    batch_size=batch_size, load_sparse_tensor=load_sparse_tensor,
                    precision=precision, enable_progress_bar=False)
    wall = round(time.time() - t0, 1)

    try:
        dev = str(next(model.module.parameters()).device)
    except Exception:  # noqa: BLE001
        dev = "?"

    rep = smp.report()
    rep.update({"tag": tag, "wall_s": wall, "torch_threads": torch_threads,
                "model_device": dev, "dl_num_workers": dl_num_workers,
                "batch_size": batch_size})
    return rep


def main():
    import torch
    print(f"torch {torch.__version__}  cuda_available={torch.cuda.is_available()}  "
          f"default_threads={torch.get_num_threads()}  cpu_count={os.cpu_count()}\n")

    # --- 1. single-core root cause + kernel fix --------------------------------
    print(">>> THREADING (root cause of single-core)")
    t_omp1 = _subprocess_threads({"OMP_NUM_THREADS": "1"})
    t_fix = _subprocess_threads({"OMP_NUM_THREADS": _KERNEL_THREADS,
                                 "MKL_NUM_THREADS": _KERNEL_THREADS,
                                 "OPENBLAS_NUM_THREADS": _KERNEL_THREADS,
                                 "NUMEXPR_NUM_THREADS": _KERNEL_THREADS})
    print(f"    OMP_NUM_THREADS=1            -> torch.get_num_threads()={t_omp1}  (single-core)")
    print(f"    kernel env (OMP={_KERNEL_THREADS}) -> torch.get_num_threads()={t_fix}  (multithreaded)\n")
    assert t_omp1 == 1, "expected OMP=1 to force single-threaded torch"
    assert t_fix > 1, "kernel thread env must yield torch.get_num_threads() > 1"

    # --- 2. GPU utilisation: baseline vs tuned ---------------------------------
    accel = "gpu" if torch.cuda.is_available() else "cpu"
    adata = _make_adata()
    print(f">>> TRAINING  accelerator={accel}  dataset={adata.n_obs}x{adata.n_vars} (sparse)\n")

    print("    BASELINE (scvi defaults: workers=0, batch=128, fp32, CPU densify)")
    base = _train_once(adata, dl_num_workers=0, num_threads=None, accelerator=accel,
                       batch_size=128, load_sparse_tensor=False, precision="32-true",
                       matmul=None, tag="baseline")
    print("    ", base, "\n")

    n_workers = min(4, max(2, (os.cpu_count() or 4) // 16))
    print(f"    TUNED (workers={n_workers}, threads=8, batch=1024, 16-mixed, GPU densify)")
    tuned = _train_once(adata, dl_num_workers=n_workers, num_threads=8, accelerator=accel,
                        batch_size=1024, load_sparse_tensor=(accel == "gpu"),
                        precision="16-mixed" if accel == "gpu" else "32-true",
                        matmul="high", tag="tuned")
    print("    ", tuned, "\n")

    speedup = base["wall_s"] / tuned["wall_s"] if tuned["wall_s"] else float("nan")
    print("=== SUMMARY ===")
    print(f"baseline: wall={base['wall_s']}s  gpu_mean={base['gpu_mean']}%  "
          f"gpu_peak={base['gpu_peak']}%  device={base['model_device']}  "
          f"torch_threads={base['torch_threads']}")
    print(f"tuned:    wall={tuned['wall_s']}s  gpu_mean={tuned['gpu_mean']}%  "
          f"gpu_peak={tuned['gpu_peak']}%  device={tuned['model_device']}  "
          f"torch_threads={tuned['torch_threads']}")
    print(f"wall-time speedup: {speedup:.2f}x   "
          f"gpu_mean delta: {base['gpu_mean']}% -> {tuned['gpu_mean']}%")

    # --- regression contract ---------------------------------------------------
    assert tuned["torch_threads"] > 1, "tuned run must be multithreaded"
    if torch.cuda.is_available():
        assert "cuda" in tuned["model_device"], "GPU present but model not on cuda"
        assert tuned["gpu_peak"] > 0, "GPU present but never utilised"
    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
