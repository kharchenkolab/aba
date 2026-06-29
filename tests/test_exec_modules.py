"""Hermetic tests for the cluster module provider (core.exec.modules).

Pure-function level — no live `module` system needed, so these run in CI. The
live paths (catalog/resolve/env_delta) are gated on `modules_active()`, which is
False off a Slurm cluster, so they no-op here; we test the matching/parsing
logic directly. See misc/cluster_modules.md.
"""
import os
import sys
import tempfile

# Self-setup for standalone runs (under pytest, conftest already does this).
os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_modtest_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec import modules as M  # noqa: E402

_SAMPLE_AVAIL = """\
/opt/ohpc/admin/modulefiles:
samtools/1.4-foss-2018b
samtools/1.9-foss-2018b
samtools/1.10-foss-2018b
samtools/1.10-gcc-8.3.0
SAMtools/1.17-GCC-11.3.0
bwa/0.7.17-foss-2018b
bwa-meth/0.2.2-foss-2018b
cellranger/6.1.1
cellranger/7.2.0
star/2.5.2a-foss-2018b
star/2.7.1a-foss-2018b (D)
"""


def test_parse_avail_structure():
    cat = M._parse_avail(_SAMPLE_AVAIL)
    assert "samtools" in cat and "bwa" in cat and "cellranger" in cat
    assert "/opt/ohpc/admin/modulefiles" not in cat          # dir header ignored
    sam = {e["version"] for e in cat["samtools"]}
    assert {"1.4", "1.9", "1.10"} <= sam
    # toolchain split + (D) default marker stripped
    star = {e["full"] for e in cat["star"]}
    assert "star/2.7.1a-foss-2018b" in star
    e0 = cat["bwa"][0]
    assert e0["version"] == "0.7.17" and e0["toolchain"] == "foss-2018b"


def test_best_match_newest_and_exact():
    cat = M._parse_avail(_SAMPLE_AVAIL)
    # newest version wins
    assert M._best_match(cat, "samtools") == "SAMtools/1.17-GCC-11.3.0"
    assert M._best_match(cat, "cellranger") == "cellranger/7.2.0"
    assert M._best_match(cat, "STAR") == "star/2.7.1a-foss-2018b"   # case-insensitive
    # exact normalized name — bwa must NOT match bwa-meth, and vice-versa
    assert M._best_match(cat, "bwa") == "bwa/0.7.17-foss-2018b"
    assert M._best_match(cat, "bwa-meth") == "bwa-meth/0.2.2-foss-2018b"
    # missing tool → None (caller falls through to build)
    assert M._best_match(cat, "scvi-tools") is None
    assert M._best_match(cat, "") is None


def test_version_key_ordering():
    k = M._ver_key
    assert k("1.10") > k("1.9")          # numeric, not lexical
    assert k("2.7.1a") > k("2.5.2a")
    assert k("7.2.0") > k("6.1.1")
    # mixed alpha parts must not raise
    assert k("1.0rc") and k("0.7.17-patch-1")


def test_env_delta_diff():
    before = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": "/lib"}
    after = {"PATH": "/opt/samtools/bin:/opt/htslib/lib:/usr/bin:/bin",
             "LD_LIBRARY_PATH": "/lib", "CUDA_HOME": "/opt/cuda"}
    d = M._delta(before, after)
    assert d["PATH"] == ["/opt/samtools/bin", "/opt/htslib/lib"]   # only prepended entries
    assert "LD_LIBRARY_PATH" not in d                              # unchanged → omitted
    assert d.get("CUDA_HOME") == "/opt/cuda"                       # scalar set


def test_gating_off_when_not_cluster():
    saved = {k: os.environ.pop(k, None) for k in ("ABA_BATCH_SUBMITTER", "ABA_MODULES_ENABLED")}
    try:
        assert M.modules_active() is False
        assert M.resolve("samtools") is None      # gated → no live discovery
        assert M.catalog() == {}
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_unsafe_module_name_rejected():
    # env_delta must refuse a name that could inject shell, before any subprocess.
    assert M.env_delta("samtools/1.0; rm -rf /") == {}


def test_load_lines_job_prologue():
    saved = os.environ.get("ABA_BATCH_SUBMITTER")
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"          # simulate a cluster install
    try:
        if not M.modules_active():                       # no module system (e.g. CI) → safe no-op
            assert M.load_lines(["samtools/1.10-foss-2018b"]) == ""
            return
        out = M.load_lines(["samtools/1.10-foss-2018b", "bwa/0.7.17"])
        assert "module load samtools/1.10-foss-2018b bwa/0.7.17" in out
        assert out.lstrip().startswith(".")              # sources an init script first
        assert M.load_lines([]) == ""                    # nothing → no prologue
        assert M.load_lines(["evil; rm -rf /"]) == ""    # unsafe name dropped
    finally:
        if saved is None:
            os.environ.pop("ABA_BATCH_SUBMITTER", None)
        else:
            os.environ["ABA_BATCH_SUBMITTER"] = saved


def test_project_module_set_threads_to_jobs(tmp_path):
    """ensure_capability records resolved modules per project; the submitter reads
    them and load_lines() turns them into job.sh `module load` lines (1c threading)."""
    saved = os.environ.get("ABA_BATCH_SUBMITTER")
    saved_rt = os.environ.get("ABA_RUNTIME_DIR")
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
    os.environ["ABA_RUNTIME_DIR"] = str(tmp_path)            # isolate the per-project file
    os.environ.pop("ABA_PROJECTS_DIR", None)
    try:
        if not M.modules_active():                          # no module system (CI) → record no-ops
            M.record_project_module("p1", "samtools/1.10-foss-2018b")
            assert M.project_modules("p1") == []
            return
        M.record_project_module("p1", "samtools/1.10-foss-2018b")
        M.record_project_module("p1", "samtools/1.10-foss-2018b")   # dedup
        M.record_project_module("p1", "bwa/0.7.17")
        assert M.project_modules("p1") == ["bwa/0.7.17", "samtools/1.10-foss-2018b"]
        assert M.project_modules("other") == []             # per-project isolation
        assert "module load bwa/0.7.17 samtools/1.10-foss-2018b" in M.load_lines(M.project_modules("p1"))
    finally:
        if saved is None:
            os.environ.pop("ABA_BATCH_SUBMITTER", None)
        else:
            os.environ["ABA_BATCH_SUBMITTER"] = saved
        if saved_rt is not None:
            os.environ["ABA_RUNTIME_DIR"] = saved_rt


def test_kernel_env_snippet():
    """In-process application (B): the snippet prepends the module's bin to the
    kernel's PATH so subprocesses find the binary — no background job needed."""
    saved = os.environ.get("ABA_BATCH_SUBMITTER")
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
    try:
        if not M.modules_active():                       # no module system (CI) → no-op
            assert M.kernel_env_snippet("samtools/1.10-foss-2018b") == ""
            return
        snip = M.kernel_env_snippet("samtools/1.10-foss-2018b")
        assert "import os as _o" in snip
        assert "_o.environ['PATH']" in snip
        assert "samtools/1.10-foss-2018b/bin" in snip    # the module's bin prepended
    finally:
        if saved is None:
            os.environ.pop("ABA_BATCH_SUBMITTER", None)
        else:
            os.environ["ABA_BATCH_SUBMITTER"] = saved
