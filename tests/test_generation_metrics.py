"""tool_use a1: per-generation metrics. record_generation writes one row per LLM
round-trip; generation_stats derives round-trips/turn, parallelism, and prompt-cache
effectiveness — the numbers that drive the tool-reduction strategy."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _db():
    os.environ["ABA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="aba_gen_")) / "s.db")
    from core.graph._schema import init_db, set_db_path
    set_db_path(os.environ["ABA_DB_PATH"]); init_db()


def test_round_trips_parallelism_cache():
    _db()
    from core.runtime.tool_telemetry import record_generation, generation_stats
    # run A: 3 generations — 2 tools, 1 tool, then a tool-free final answer.
    record_generation(run_id="A", agent_spec="grounded_guide", gen_index=0, n_tool_uses=2,
                      input_tokens=5, cache_read=100, cache_write=50, stop_reason="tool_use")
    record_generation(run_id="A", agent_spec="grounded_guide", gen_index=1, n_tool_uses=1,
                      input_tokens=5, cache_read=200, cache_write=0, stop_reason="tool_use")
    record_generation(run_id="A", agent_spec="grounded_guide", gen_index=2, n_tool_uses=0,
                      input_tokens=5, cache_read=200, cache_write=0, stop_reason="end_turn")
    s = generation_stats(days=1)
    assert s["n_runs"] == 1, s
    assert s["avg_round_trips_per_run"] == 3.0, s
    assert s["avg_tool_uses_per_run"] == 3.0, s
    # 3 tool_uses over 2 tool-emitting generations (3 gens − 1 run) = 1.5
    assert s["avg_parallelism"] == 1.5, s
    assert s["max_parallelism"] == 2, s
    # cache_read 500, cache_write 50, fresh input 15 → 500/565 served from cache
    assert s["cache_read_total"] == 500 and s["cache_write_total"] == 50, s
    assert abs(s["cache_hit_frac"] - 500 / 565) < 0.01, s


def test_two_runs_average():
    _db()
    from core.runtime.tool_telemetry import record_generation, generation_stats
    # run A: 2 round-trips; run B: 4 round-trips → avg 3.0
    for i in range(2):
        record_generation(run_id="A", agent_spec="g", gen_index=i, n_tool_uses=1)
    for i in range(4):
        record_generation(run_id="B", agent_spec="g", gen_index=i, n_tool_uses=1)
    s = generation_stats(days=1)
    assert s["n_runs"] == 2 and s["avg_round_trips_per_run"] == 3.0, s


def test_empty_is_safe():
    _db()
    from core.runtime.tool_telemetry import generation_stats
    s = generation_stats(days=1)
    assert s["n_runs"] == 0 and s["cache_hit_frac"] is None, s


if __name__ == "__main__":
    test_round_trips_parallelism_cache(); print("ok  round-trips / parallelism / cache")
    test_two_runs_average(); print("ok  two-run average")
    test_empty_is_safe(); print("ok  empty-safe")
    print("all generation-metrics tests passed")
