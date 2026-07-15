"""Execution layer — the Sandbox/Executor abstraction + placement router.

`Executor.materialize(provisioning)` builds/returns a runnable environment;
`Executor.exec(env, command, ...)` runs a command in it with cancellation.
`ExecutionRouter.route(...)` decides *where* a step runs (capabilities.md §10).

P0 ships `LocalSubprocessExecutor` (the base venv, extracted from run_python's
subprocess logic) and `LocalRouter` (always "local"). Conda materialization
(P1), containers, and HPC/remote backends slot in behind these same
interfaces with no caller changes.
"""
from core.exec.base import Executor, Provisioning, Env, ExecResult
from core.exec.local import LocalSubprocessExecutor
from core.exec.materialize import MaterializingExecutor
from core.exec.router import ExecutorChoice, decide

__all__ = [
    "Executor", "Provisioning", "Env", "ExecResult",
    "LocalSubprocessExecutor", "MaterializingExecutor",
    "ExecutorChoice", "decide",
]
