---
name: branch-from-figure
description: Spin a scenario / variant from an existing figure (different params, same code)
when_to_use: User wants to ask "what if we used X instead?" — reuses the figure's producing code with overrides
requires_tools: [create_scenario]
---

# Branch from figure

Use the `create_scenario` tool, passing the source figure's entity id
and the parameter overrides. The new scenario inherits the producing
code; only the parameters change. The Guide should plan-first if more
than one branch is needed.
