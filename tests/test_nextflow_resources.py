"""Unit tests for nf-core resource estimation (parsers + compute_estimate are pure;
the GitHub fetch wrapper is exercised in tests/live_nextflow_hpc.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.exec import nextflow_resources as nr  # noqa: E402

# Trimmed but faithful slice of a real nf-core conf/base.config.
BASE = """
process {
    cpus   = { check_max( 1    * task.attempt, 'cpus'   ) }
    memory = { check_max( 6.GB * task.attempt, 'memory' ) }
    time   = { check_max( 4.h  * task.attempt, 'time'   ) }
    withLabel:process_single {
        cpus   = { check_max( 1                  , 'cpus'    ) }
        memory = { check_max( 6.GB * task.attempt, 'memory'  ) }
        time   = { check_max( 4.h  * task.attempt, 'time'    ) }
    }
    withLabel:process_medium {
        cpus   = { check_max( 6     * task.attempt, 'cpus'    ) }
        memory = { check_max( 36.GB * task.attempt, 'memory'  ) }
        time   = { check_max( 8.h   * task.attempt, 'time'    ) }
    }
    withLabel:process_high {
        cpus   = { check_max( 12    * task.attempt, 'cpus'    ) }
        memory = { check_max( 72.GB * task.attempt, 'memory'  ) }
        time   = { check_max( 16.h  * task.attempt, 'time'    ) }
    }
    withLabel:process_long {
        time   = { check_max( 20.h  * task.attempt, 'time'    ) }
    }
    withLabel:process_high_memory {
        memory = { check_max( 200.GB * task.attempt, 'memory' ) }
    }
    withName:CUSTOM_DUMPSOFTWAREVERSIONS {
        cache = false
    }
}
"""
TEST_CFG = "params {\n max_cpus = 2\n max_memory = '6.GB'\n max_time = '6.h'\n}\n"


def test_unit_conversions():
    assert nr.mem_to_gb("72.GB") == 72 and nr.mem_to_gb("200.GB") == 200
    assert nr.mem_to_gb("'128.GB'") == 128 and nr.mem_to_gb("512.MB") == 0.512
    assert nr.time_to_h("16.h") == 16 and nr.time_to_h("20.h") == 20
    assert nr.time_to_h("30.min") == 0.5


def test_parse_labels_and_inheritance():
    L = nr.parse_resource_labels(BASE)
    assert L["default"] == {"cpus": 1, "mem_gb": 6, "time_h": 4}
    assert L["process_high"] == {"cpus": 12, "mem_gb": 72, "time_h": 16}
    assert L["process_medium"]["cpus"] == 6 and L["process_medium"]["mem_gb"] == 36
    # partial labels inherit unset fields from the default process block
    assert L["process_long"]["time_h"] == 20 and L["process_long"]["cpus"] == 1
    assert L["process_high_memory"]["mem_gb"] == 200 and L["process_high_memory"]["cpus"] == 1
    # withName: overrides are not resource labels
    assert "CUSTOM_DUMPSOFTWAREVERSIONS" not in L


def test_parse_max_caps():
    c = nr.parse_max_caps(TEST_CFG)
    assert c == {"cpus": 2, "mem_gb": 6, "time_h": 6}
    assert nr.parse_max_caps("nothing here") == {}


def test_parse_max_caps_resource_limits_block():
    # Newer nf-core (nf-schema era) drops max_* for a resourceLimits block — must still parse,
    # else a -profile test is mis-sized to the 128GB template fallback and mis-routes to slurm.
    cfg = "params {\n  resourceLimits = [\n cpus: 4,\n memory: '15.GB',\n time: '1.h'\n  ]\n}\n"
    assert nr.parse_max_caps(cfg) == {"cpus": 4, "mem_gb": 15, "time_h": 1}


def test_estimate_test_profile_is_local_viable():
    # test caps everything tiny → heaviest task is 2c/6GB → easily fits one node
    L = nr.parse_resource_labels(BASE)
    est = nr.compute_estimate(L, nr.parse_max_caps(TEST_CFG))
    assert est["heaviest_task"] == {"cpus": 2, "mem_gb": 6, "time_h": 6}
    assert est["local_viable"] is True
    # recommended is floored for parallelism headroom (>= 8c/32GB), within ceiling
    assert est["recommended_local"]["cores"] == 8 and est["recommended_local"]["mem_gb"] == 32


def test_estimate_real_data_needs_big_node():
    # template ceiling (16c/128GB) → process_high_memory's 200GB capped to 128 → still huge
    L = nr.parse_resource_labels(BASE)
    est = nr.compute_estimate(L, dict(nr._TEMPLATE_MAX))
    assert est["heaviest_task"]["mem_gb"] == 128 and est["heaviest_task"]["cpus"] == 12
    assert est["local_viable"] is True          # 128GB ≤ default 180GB ceiling
    assert est["recommended_local"]["mem_gb"] == 128

    # a stricter (smaller) single-node ceiling makes the same pipeline fan-out-only
    est2 = nr.compute_estimate(L, dict(nr._TEMPLATE_MAX), ceiling={"cores": 16, "mem_gb": 64})
    assert est2["local_viable"] is False
    assert "exceeds the single-node ceiling" in est2["reason"]


def test_single_node_ceiling_env_override(monkeypatch):
    monkeypatch.setenv("ABA_NEXTFLOW_LOCAL_MAX_CORES", "64")
    monkeypatch.setenv("ABA_NEXTFLOW_LOCAL_MAX_MEM_GB", "500")
    c = nr.single_node_ceiling()
    assert c == {"cores": 64, "mem_gb": 500.0}
