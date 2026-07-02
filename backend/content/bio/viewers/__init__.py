"""Bio viewer registry.

Importing this package loads bio/viewers/registry.yaml into the
central core.viewers.registry. Lab / project / personal overlays
will layer on top via the same loader when the layered-knowledge
stack ships.
"""
from pathlib import Path
from core.viewers.registry import register_viewers_yaml

_HERE = Path(__file__).parent
register_viewers_yaml(_HERE / "registry.yaml")

# Register external-viewer launchers (open_external ids referenced in the YAML).
from content.bio.viewers.launchers import pagoda3 as _pagoda3  # noqa: E402,F401
