"""Persistent execution kernels (kernels.md).

A thread-scoped, notebook-like session so the agent loads data and builds
expensive objects once and reuses them across run_python calls. Conservative
lifecycle (lazy start, 15-min idle TTL, per-user cap of 5 + LRU). Local
jupyter_client impl now; the KernelSession interface is transport-agnostic so a
remote (gateway/E2B) impl drops in later.
"""
from core.exec.kernels.base import KernelSession
from core.exec.kernels.pool import KernelPool, get_pool, KernelCapacityError

__all__ = ["KernelSession", "KernelPool", "get_pool", "KernelCapacityError"]
