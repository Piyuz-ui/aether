"""Offline code interpreter and data tools.

python_sandbox  Runs a Python script in a separate, locked-down process (numpy, pandas and
                matplotlib available; 15 s limit; no API keys or network credentials in its
                environment). Charts are saved to workspace/artifacts/ and shown in the chat.
analyze_dataset Describes a CSV / TSV / Excel / JSON table (shape, columns, types, gaps,
                summary statistics, a preview) and answers simple questions about it with safe,
                structured queries: filter, group, aggregate, sort. No free-form code.

A tool's output marks each image it made with a line `[artifact] artifacts/<file>.png`; the
engine turns those into URLs on the tool_result event, and the chat renders them as cards.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .context import workspace
from .registry import Risk, ToolError, tool

SANDBOX_TIMEOUT_S = 15
OUTPUT_LIMIT = 6000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".svg", ".webp")
ARTIFACT_MARK = "[artifact] "

# Runs before your code: headless charts, ARTIFACTS points at the output folder.
PRELUDE = '''
import os as _os, warnings as _w
_w.filterwarnings("ignore")
import matplotlib as _mpl
_mpl.use("Agg")
ARTIFACTS = _os.environ["AETHER_ARTIFACTS"]
_before = set(_os.listdir(ARTIFACTS))
'''
# Runs after: figures you drew but didn't save are saved for you.
POSTLUDE = '''
import matplotlib.pyplot as _plt
_new = [f for f in _os.listdir(ARTIFACTS) if f not in _before]
if not _new:
    for _i, _n in enumerate(_plt.get_fignums()):
        _plt.figure(_n).savefig(_os.path.join(ARTIFACTS, f"{_os.environ['AETHER_RUN']}-{_i + 1}.png"),
                                dpi=144, bbox_inches="tight")
'''


def artifacts_dir() -> Path:
    d = workspace().root / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mpl_cache() -> Path:
    """matplotlib's font cache, kept between runs (building it takes seconds)."""
    from ..config import get_settings
    d = get_settings().data_dir / "mpl-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _limits() -> None:  # runs in the child before Python starts
    import resource
    os.setsid()  # own process group, so a timeout kills everything it started
    for res, value in ((resource.RLIMIT_CPU, SANDBOX_TIMEOUT_S + 2),
                       (resource.RLIMIT_FSIZE, 50 * 1024 * 1024),   # no file over 50 MB
                       (resource.RLIMIT_NOFILE, 256)):
        try:
            resource.setrlimit(res, (value, value))
        except (ValueError, OSError):
            pass


def _sandbox_summary(args: dict[str, Any]) -> str:
    lines = str(args.get("code", "")).count("\n") + 1
    return (f"Run a {lines}-line Python script in a separate process ({SANDBOX_TIMEOUT_S} s limit, working "
            "folder = your workspace, no API keys). It can read and write files in your user account, "
            "so check what it does.")


@tool(description=(
    "Run Python with numpy, pandas, matplotlib and sympy to analyse data, solve maths (symbolically too), "
    "or draw charts. "
    "Save charts with plt.savefig(f'{ARTIFACTS}/name.png') (or just draw them: unsaved figures are "
    "saved for you) and they appear in the chat. Files the user attached are in uploads/. "
    "print() what the user should see. 15-second limit; the user approves each run."),
    params={"code": "A complete Python script. Use print() for results; draw with matplotlib for charts."},
    risk=Risk.REQUIRES_APPROVAL, category="code_data", icon="chart-column",
    risk_summary=_sandbox_summary, timeout_s=SANDBOX_TIMEOUT_S + 10)
async def python_sandbox(code: str) -> str:
    out_dir = artifacts_dir()
    run_id = f"chart-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    before = {p.name: p.stat().st_mtime for p in out_dir.iterdir()}
    with tempfile.TemporaryDirectory(prefix="aether-sandbox-") as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(PRELUDE + "\n" + code + "\n" + POSTLUDE, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "LANG": "en_US.UTF-8",
               "MPLCONFIGDIR": str(_mpl_cache()), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
               "AETHER_ARTIFACTS": str(out_dir), "AETHER_RUN": run_id}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(script), cwd=str(workspace().root), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, preexec_fn=_limits if os.name == "posix" else None)
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), SANDBOX_TIMEOUT_S)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            await proc.wait()
            raise ToolError(f"The script ran longer than {SANDBOX_TIMEOUT_S} s and was stopped. "
                            "Use less data or a simpler computation.") from None
    out = raw.decode(errors="replace").replace(str(script), "main.py")
    out = "\n".join(ln for ln in out.splitlines()
                    if not ln.startswith("Matplotlib is building the font cache")).strip()
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + f"\n… ({len(out) - OUTPUT_LIMIT:,} more characters cut)"
    made = sorted(p for p in out_dir.iterdir()
                  if p.suffix.lower() in IMAGE_EXT and before.get(p.name) != p.stat().st_mtime)
    if proc.returncode != 0:
        hint = ""
        if "ModuleNotFoundError" in out:
            hint = " Only the standard library, numpy, pandas, matplotlib and sympy are available."
        raise ToolError(f"The script failed (exit {proc.returncode}).{hint}\n{out or '(no output)'}")
    lines = [out or "(no printed output)"]
    lines += [f"{ARTIFACT_MARK}{workspace().relative(p)}" for p in made]
    if made:
        lines.append(f"{len(made)} chart{'s' * (len(made) != 1)} saved and already shown to the user in the "
                     "chat. Don't embed or link the image; just describe what it shows.")
    return "\n".join(lines)


# ----------------------------------------------------------------- analyze_dataset
def _read_table(path: Path, sheet: str) -> Any:
    import pandas as pd
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(path, sep=None, engine="python")   # sniffs , ; | tab
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t")
    if ext in (".xlsx", ".xlsm"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    if ext == ".json":
        return pd.read_json(path)
    raise ToolError(f"Can't read {ext} files. Use CSV, TSV, XLSX or JSON (save old .xls files as .xlsx).")


def _analyse(path: Path, sheet: str) -> str:
    import pandas as pd
    df = _read_table(path, sheet)
    rows, cols = df.shape
    parts = [f"# {path.name}", f"{rows:,} rows × {cols} columns"]
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        sheets = pd.ExcelFile(path).sheet_names
        if len(sheets) > 1:
            parts.append(f"Sheets: {', '.join(sheets)} (showing {sheet or sheets[0]})")
    parts.append("\n## Columns\n| column | type | filled | missing | unique |\n|---|---|---|---|---|")
    for c in df.columns[:60]:
        s = df[c]
        parts.append(f"| {str(c)[:40]} | {s.dtype} | {int(s.notna().sum()):,} | {int(s.isna().sum()):,} | "
                     f"{int(s.nunique(dropna=True)):,} |")
    if cols > 60:
        parts.append(f"… and {cols - 60} more columns")
    num = df.select_dtypes("number")
    if not num.empty:
        desc = num.describe().T[["mean", "std", "min", "50%", "max"]].round(3)
        desc.columns = ["mean", "std", "min", "median", "max"]
        parts.append("\n## Numbers\n" + desc.head(30).to_markdown())
    text_cols = [c for c in df.columns if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object]
    cats = [c for c in text_cols if 1 < df[c].nunique() <= 50][:4]
    for c in cats:
        top = df[c].value_counts().head(5)
        parts.append(f"\n## Top values · {c}\n" + ", ".join(f"{k} ({v:,})" for k, v in top.items()))
    parts.append("\n## First rows\n" + df.head(5).to_markdown(index=False, maxcolwidths=24))
    dups = int(df.duplicated().sum())
    if dups:
        parts.append(f"\nNote: {dups:,} duplicate rows.")
    parts.append(f"\nTo chart or dig deeper, use python_sandbox with pd.read_csv('{workspace().relative(path)}').")
    return "\n".join(parts)


# A filter is a pandas query over column names, numbers, strings and comparisons only. No calls,
# attributes, @-variables or dunders, so it can't run code.
_SAFE_FILTER = re.compile(r"^[\w\s.'\"`<>=!&|~+\-*/%(),\[\]]+$")
AGGS = {"count", "sum", "mean", "median", "min", "max", "std", "nunique"}


def _query(path: Path, sheet: str, filter: str, columns: str, group_by: str, agg: str,
           sort_by: str, descending: bool, limit: int) -> str:
    import pandas as pd
    df = _read_table(path, sheet)
    notes = []
    if filter.strip():
        f = filter.strip()
        if not _SAFE_FILTER.match(f) or "__" in f or "@" in f or re.search(r"[A-Za-z_]\w*\s*\(", f) or "import" in f:
            raise ToolError("The filter may only compare columns with values, e.g. `marks > 80 and subject == 'Maths'` "
                            "(use backticks for names with spaces: `` `study hours` > 2 ``).")
        try:
            df = df.query(f, engine="python", local_dict={}, global_dict={})
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"The filter didn't work: {e}. Columns: {', '.join(map(str, df.columns[:30]))}") from e
        notes.append(f"filter `{f}` → {len(df):,} rows")
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    missing = [c for c in cols + [g.strip() for g in group_by.split(",") if g.strip()] if c not in df.columns]
    if missing:
        raise ToolError(f"No column named {', '.join(missing)}. Columns: {', '.join(map(str, df.columns[:40]))}")
    if group_by.strip():
        keys = [g.strip() for g in group_by.split(",") if g.strip()]
        fn = (agg or "count").strip().lower()
        if fn not in AGGS:
            raise ToolError(f"agg must be one of {', '.join(sorted(AGGS))}.")
        target = cols or [c for c in df.select_dtypes("number").columns if c not in keys]
        grouped = df.groupby(keys, dropna=False)
        df = (grouped.size().to_frame("count") if fn == "count" or not target
              else getattr(grouped[target], fn)()).reset_index()
        notes.append(f"grouped by {', '.join(keys)} ({fn})")
    elif cols:
        df = df[cols]
    if sort_by.strip():
        if sort_by.strip() not in df.columns:
            raise ToolError(f"Can't sort by '{sort_by}': not a column of the result ({', '.join(map(str, df.columns))}).")
        df = df.sort_values(sort_by.strip(), ascending=not descending)
    limit = max(1, min(int(limit), 200))
    shown = df.head(limit)
    head = f"# {path.name}: {len(df):,} rows" + (f" ({'; '.join(notes)})" if notes else "")
    more = f"\n… {len(df) - limit:,} more rows" if len(df) > limit else ""
    return f"{head}\n\n{shown.round(4).to_markdown(index=False, maxcolwidths=28)}{more}"


@tool(description=(
    "Analyse a data table the user has (CSV, TSV, Excel .xlsx or JSON). With just a path: a profile "
    "(rows, columns, types, missing values, statistics, top values, first rows). Add filter / group_by / "
    "agg / sort_by / columns to answer questions, e.g. average marks per subject. Uploaded files are in uploads/."),
    params={"path": "The file's path in the workspace, e.g. uploads/sales.csv",
            "sheet": "Excel only: which sheet (default: the first)",
            "filter": "Optional row filter comparing columns with values, e.g. marks > 80 and subject == 'Maths'",
            "columns": "Optional comma-separated columns to keep (or to aggregate when grouping)",
            "group_by": "Optional comma-separated columns to group by",
            "agg": "With group_by: count, sum, mean, median, min, max, std or nunique (default count)",
            "sort_by": "Optional column of the result to sort by",
            "descending": "Sort high to low (default true)",
            "limit": "Rows to show (default 20, max 200)"},
    category="code_data", icon="table", timeout_s=30)
async def analyze_dataset(path: str, sheet: str = "", filter: str = "", columns: str = "", group_by: str = "",
                          agg: str = "", sort_by: str = "", descending: bool = True, limit: int = 20) -> str:
    target = workspace().resolve(path, must_exist=True)
    if not target.is_file():
        raise ToolError(f"'{path}' is a folder. Give a file, e.g. uploads/data.csv.")
    try:
        if any(x.strip() for x in (filter, columns, group_by, sort_by)):
            return await asyncio.to_thread(_query, target, sheet, filter, columns, group_by, agg, sort_by,
                                           descending, limit)
        return await asyncio.to_thread(_analyse, target, sheet)
    except ToolError:
        raise
    except ImportError as e:
        raise ToolError(f"A library is missing: {e}. Run: pip install -r requirements.txt") from e
    except Exception as e:  # noqa: BLE001 - pandas raises many kinds
        raise ToolError(f"Couldn't read {target.name} as a table: {type(e).__name__}: {e}") from e
