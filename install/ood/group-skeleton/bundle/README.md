# Lab bundle

Recipes, capability-catalog entries, and rules **specific to this lab**. They
layer on top of the image-baked pack (system scope) and any institution bundle —
so you customize without rebuilding the SIF.

- `skills/recipes/<domain>/` — lab recipes (same format as the pack)
- `catalog/*.yaml` — capability-catalog additions
- `rules/` — lab policies / system-prompt addenda

Edits here take effect on the next session launch (no rebuild).
