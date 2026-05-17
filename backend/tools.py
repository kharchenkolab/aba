import subprocess
import shutil
import uuid
import json
import sys
from pathlib import Path
from config import DATA_DIR, ARTIFACTS_DIR

# ---------- Tool schemas (passed to Claude API) ----------

TOOL_SCHEMAS = [
    {
        "name": "list_data_files",
        "description": "List all CSV files available in the data folder. Returns filenames and sizes.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "read_csv_info",
        "description": "Read a CSV file and return its shape, column names with dtypes, and first 5 rows as a markdown table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename of the CSV (just the name, not a path)"
                }
            },
            "required": ["filename"]
        }
    },
    {
        "name": "run_python",
        "description": (
            "Execute Python code in a sandboxed subprocess. "
            "pandas and matplotlib are available. "
            "The data folder is available as the variable DATA_DIR (a string path). "
            "Save any plots as plt.savefig('output.png') or any .png name — they will be captured and displayed. "
            "Print any text results you want returned. "
            "Timeout is 60 seconds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                }
            },
            "required": ["code"]
        }
    }
]

# ---------- Executors ----------

def list_data_files(_input: dict) -> dict:
    files = []
    for p in sorted(DATA_DIR.glob("*.csv")):
        files.append({"filename": p.name, "size_bytes": p.stat().st_size})
    if not files:
        return {"files": [], "message": "No CSV files found in the data folder."}
    return {"files": files}


def read_csv_info(input_: dict) -> dict:
    import pandas as pd
    filename = input_.get("filename", "")
    path = DATA_DIR / filename
    if not path.exists():
        return {"error": f"File not found: {filename}"}
    try:
        df = pd.read_csv(path)
        cols = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
        preview = df.head(5).to_markdown(index=False)
        return {
            "filename": filename,
            "rows": len(df),
            "columns": len(df.columns),
            "column_info": cols,
            "preview": preview
        }
    except Exception as e:
        return {"error": str(e)}


def run_python(input_: dict) -> dict:
    code = input_.get("code", "")
    tmp_dir = Path("/tmp") / f"aba_{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    try:
        # Prepend DATA_DIR injection
        full_code = f"DATA_DIR = {str(DATA_DIR)!r}\n" + code
        script = tmp_dir / "script.py"
        script.write_text(full_code)

        import os
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"
        # Use the same python that's running this server (keeps venv libs available)
        python = sys.executable

        result = subprocess.run(
            [python, str(script)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=str(tmp_dir)
        )

        # Collect any PNG files produced
        plots = []
        for png in tmp_dir.glob("*.png"):
            dest_name = f"{uuid.uuid4().hex}.png"
            dest = ARTIFACTS_DIR / dest_name
            shutil.move(str(png), str(dest))
            plots.append({"url": f"/artifacts/{dest_name}", "original_name": png.name})

        return {
            "stdout": result.stdout[:4000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "returncode": result.returncode,
            "plots": plots
        }
    except subprocess.TimeoutExpired:
        return {"error": "Code execution timed out (60s limit)"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


EXECUTORS = {
    "list_data_files": list_data_files,
    "read_csv_info": read_csv_info,
    "run_python": run_python,
}

def execute_tool(name: str, input_: dict) -> str:
    fn = EXECUTORS.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        result = fn(input_)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})
