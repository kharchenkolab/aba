"""Kernel setup-code helpers shared by kernel transports — the project
data/artifacts dir resolution and the `harvest_table()` convenience injected at
kernel startup. Extracted from the retired jupyter transport module (the weft
transport was importing them from there)."""
from __future__ import annotations

from pathlib import Path

from core.config import DATA_DIR, ARTIFACTS_DIR


def _project_data_artifacts() -> tuple[Path, Path]:
    """Resolve the active project's data + artifacts dirs at session-start time.
    Falls back to the workspace-level DATA_DIR/ARTIFACTS_DIR (no project active)."""
    from core import projects
    from core.config import project_data_dir, project_artifacts_dir
    pid = projects.current()
    if pid:
        return project_data_dir(pid), project_artifacts_dir(pid)
    return DATA_DIR, ARTIFACTS_DIR


def _harvest_helpers_r() -> str:
    """R `harvest_table()` helper, mirror of the Python one. Writes a
    CSV to the current cwd so the post-cell harvester picks it up.

    Auto-naming uses nanosecond Sys.time() + a random suffix to avoid
    collisions when called several times in the same cell — `digest`
    isn't guaranteed to be installed in every R image, so we stick
    with base R primitives."""
    return (
        "harvest_table <- function(df, name='auto') {\n"
        "  if (identical(name, 'auto')) {\n"
        "    .t <- format(as.numeric(Sys.time()) * 1e6, scientific=FALSE, digits=20)\n"
        "    .r <- paste(sample(c(0:9, letters[1:6]), 6, replace=TRUE), collapse='')\n"
        "    name <- paste0('table_', substr(.t, nchar(.t)-5, nchar(.t)), '_', .r, '.csv')\n"
        "  }\n"
        "  if (!grepl('\\\\.(csv|tsv)$', name, ignore.case=TRUE)) {\n"
        "    name <- paste0(name, '.csv')\n"
        "  }\n"
        "  path <- file.path(getwd(), name)\n"
        "  tryCatch(\n"
        "    write.csv(as.data.frame(df), path, row.names=FALSE),\n"
        "    error = function(e) write.csv(df, path)\n"
        "  )\n"
        "  cat(sprintf('[harvest_table] wrote %s\\n', basename(path)))\n"
        "  invisible(path)\n"
        "}\n"
    )


def _harvest_helpers_py() -> str:
    """Python `harvest_table()` helper, injected at kernel startup.

    Stage 6 of misc/exec_records_and_versioning.md — give recipes/agents
    a one-liner to mark a DataFrame for pinning. The function writes a
    CSV to the current cwd; the standard run_python post-cell harvester
    picks it up as a table artifact and registers it as a table entity.
    """
    return (
        "def harvest_table(obj, name='auto'):\n"
        "    \"\"\"Save a DataFrame (or anything with .to_csv()) to the current\n"
        "    workdir as a CSV so it surfaces as a pinnable table artifact.\n"
        "    Pass `name` to control the filename (default: auto-unique).\"\"\"\n"
        "    import os as _os, time as _t, hashlib as _h\n"
        "    from pathlib import Path as _P\n"
        "    if name == 'auto':\n"
        "        _seed = f'{_t.time_ns()}:{id(obj)}'.encode()\n"
        "        name = 'table_' + _h.md5(_seed).hexdigest()[:8] + '.csv'\n"
        "    if not name.lower().endswith(('.csv', '.tsv')):\n"
        "        name = name + '.csv'\n"
        "    _path = _P(_os.getcwd()) / name\n"
        "    if hasattr(obj, 'to_csv'):\n"
        "        # pandas / polars / etc. — let the library handle dialect + index\n"
        "        try:\n"
        "            obj.to_csv(_path, index=False)\n"
        "        except TypeError:\n"
        "            obj.to_csv(_path)\n"
        "    else:\n"
        "        import csv as _csv\n"
        "        with open(_path, 'w', newline='') as _f:\n"
        "            _w = _csv.writer(_f)\n"
        "            if isinstance(obj, dict):\n"
        "                _w.writerow(list(obj.keys()))\n"
        "                _w.writerow(list(obj.values()))\n"
        "            else:\n"
        "                for _row in obj:\n"
        "                    if isinstance(_row, (list, tuple)):\n"
        "                        _w.writerow(_row)\n"
        "                    else:\n"
        "                        _w.writerow([_row])\n"
        "    print(f'[harvest_table] wrote {_path.name}')\n"
        "    return str(_path)\n"
    )
