"""Bio advisor registry.

Importing this package loads every *.yaml file in this directory and
registers the resulting AgentSpec under its name. Adding a new advisor
is then two files: bio/advisors/X.yaml + bio/prompts/X.md.
"""
from pathlib import Path
from core.runtime.agent import load_agent_spec, register_agent_spec

_HERE = Path(__file__).parent

for _yaml in sorted(_HERE.glob("*.yaml")):
    try:
        spec = load_agent_spec(_yaml)
        register_agent_spec(spec)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "could not load advisor spec %s: %s", _yaml.name, e
        )

# Register hook handlers (e.g. methodologist auto-trigger).
from . import handlers  # noqa: F401,E402
