"""The core/services content-inversion seam (Phase 1, 1A.1): core asks content for
values via register_service/call_service instead of importing content/."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def test_register_call_and_default():
    from core.services import register_service, call_service
    register_service("t_upper", lambda s: s.upper())
    assert call_service("t_upper", "hi") == "HI"
    assert call_service("t_missing", default="D") == "D"        # unregistered -> default
    register_service("t_boom", lambda: 1 / 0)
    assert call_service("t_boom", default="SAFE") == "SAFE"      # raises -> default


def test_bio_registers_language_sniffer_and_host_tools():
    import content.bio  # noqa: F401 — triggers bio registration (incl. services)
    from core.services import call_service, service_names
    assert "language_sniffer" in service_names()
    assert "host_tool_names" in service_names()
    assert call_service("language_sniffer", "library(Seurat)", default="python") == "r"
    assert call_service("language_sniffer", "import scanpy as sc", default="python") == "python"
    tools = call_service("host_tool_names", default=set())
    assert "run_python" in tools and "run_r" in tools
