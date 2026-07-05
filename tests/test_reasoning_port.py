"""Reasoning continuation port (modularity_audit3 Item 1). core.jobs re-enters the agent
loop through a registered port instead of `from guide import stream_response`. The port is
MANDATORY: an unregistered port raises loudly (a dropped continuation breaks the deferred-turn
contract), unlike core/services which returns a silent default. And importing the orchestrator
(guide) must register the handler — the wiring that keeps the up-edge dissolved at runtime."""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="aba_rport_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "t.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)

from core import reasoning_port  # noqa: E402


def test_unregistered_raises_loud():
    reasoning_port._CONTINUATION = None
    assert reasoning_port.is_registered() is False
    raised = False
    try:
        reasoning_port.run_continuation("x", focus_entity_id="workspace", thread_id="t", run_id="r")
    except RuntimeError as e:
        raised = True
        assert "not registered" in str(e)
    assert raised, "unregistered port must RAISE (never silently drop a continuation)"


def test_register_then_run_passes_args_and_returns_body_gen():
    seen = {}

    def handler(cont_text, *, focus_entity_id, thread_id, run_id):
        seen.update(cont_text=cont_text, focus_entity_id=focus_entity_id,
                    thread_id=thread_id, run_id=run_id)
        return "BODY_GEN"

    reasoning_port.register_continuation(handler)
    assert reasoning_port.is_registered() is True
    out = reasoning_port.run_continuation("hello", focus_entity_id="workspace",
                                          thread_id="t1", run_id="r1")
    assert out == "BODY_GEN"
    assert seen == dict(cont_text="hello", focus_entity_id="workspace",
                        thread_id="t1", run_id="r1")
    reasoning_port._CONTINUATION = None


def test_importing_guide_registers_the_port():
    reasoning_port._CONTINUATION = None
    import guide  # noqa: F401,E402 — importing the orchestrator must wire the continuation port
    assert reasoning_port.is_registered() is True, \
        "importing guide must register the continuation handler (else finished jobs never resume)"


if __name__ == "__main__":
    for fn in [test_unregistered_raises_loud,
               test_register_then_run_passes_args_and_returns_body_gen,
               test_importing_guide_registers_the_port]:
        fn()
        print("PASS", fn.__name__)
    print("all passed")
