#!/usr/bin/env python3
'''
PF67 Backend Flows Pill Source Diagnostic

Run from either:
  /volume1/docker/pf67
or:
  /volume1/docker/pf67/app

Recommended:
  cd /volume1/docker/pf67
  python pf67_backend_flows_pill_diagnostic.py

Purpose:
- Does NOT modify any app files.
- Finds the exact backend source responsible for the broken Flows count row:
    10Flowshown Active10 Scheduled0 Completed0 Public10 Private0
- Searches templates, Python views, static JS, and generated rendered HTML if possible.
- Writes a report file to the PF67 root.

Upload the generated report here before the next patch.
'''

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path


STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def find_root() -> Path:
    cwd = Path.cwd().resolve()
    candidates = [
        cwd,
        cwd.parent,
        Path("/volume1/docker/pf67"),
    ]

    for candidate in candidates:
        if (candidate / "app" / "templates").is_dir() and (candidate / "app" / "pf67").is_dir():
            return candidate

    print("ERROR: Could not find PF67 project root.")
    print("Run from /volume1/docker/pf67 or /volume1/docker/pf67/app.")
    sys.exit(1)


ROOT = find_root()
APP = ROOT / "app"
TEMPLATES = APP / "templates"
PF67 = APP / "pf67"

PATTERNS = [
    "Flowshown",
    "Flowsshown",
    "Flows shown",
    "Flow shown",
    "Active10",
    "Scheduled0",
    "Completed0",
    "Public10",
    "Private0",
    "Flowshown",
    "Flows",
    "Refine view",
]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return "ERROR reading " + str(path) + ": " + str(exc)


def context_for_lines(lines: list[str], index: int, radius: int = 6) -> str:
    start = max(1, index + 1 - radius)
    end = min(len(lines), index + 1 + radius)

    return "\n".join(
        str(line_no) + ": " + lines[line_no - 1]
        for line_no in range(start, end + 1)
    )


def source_files() -> list[Path]:
    out = []

    for base in [APP, ROOT]:
        if not base.exists():
            continue

        for suffix in ["*.html", "*.jinja", "*.j2", "*.py", "*.js", "*.css"]:
            for path in base.rglob(suffix):
                p = str(path)

                if "_pf67_patch_backups" in p:
                    continue

                if "__pycache__" in p:
                    continue

                if path.name.endswith(".pyc"):
                    continue

                out.append(path)

    seen = set()
    unique = []

    for path in out:
        resolved = str(path.resolve())

        if resolved in seen:
            continue

        seen.add(resolved)
        unique.append(path)

    return sorted(unique)


def search_sources() -> list[dict]:
    hits = []

    for path in source_files():
        text = read_text(path)
        lower = text.lower()
        rel = str(path.relative_to(ROOT))

        likely = (
            "flow" in rel.lower()
            or "job" in rel.lower()
            or "dashboard" in rel.lower()
            or "refine view" in lower
            or "flowshown" in lower
            or "scheduled" in lower and "completed" in lower and "public" in lower
        )

        if not likely:
            continue

        lines = text.splitlines()
        file_hits = []

        for idx, line in enumerate(lines):
            low = line.lower()

            direct = any(pattern.lower() in low for pattern in PATTERNS)
            jinja_combo = "{{" in line and (
                "flow" in low
                or "active" in low
                or "scheduled" in low
                or "completed" in low
                or "public" in low
                or "private" in low
            )
            html_combo = (
                ("active" in low or "scheduled" in low or "completed" in low or "public" in low or "private" in low)
                and ("span" in low or "pill" in low or "badge" in low or "count" in low)
            )

            if direct or jinja_combo or html_combo:
                file_hits.append({
                    "line": idx + 1,
                    "text": line,
                    "context": context_for_lines(lines, idx),
                })

        if file_hits:
            hits.append({
                "file": rel,
                "hits": file_hits[:30],
            })

    return hits


def inspect_routes_static() -> list[dict]:
    path = PF67 / "views.py"
    text = read_text(path)
    lines = text.splitlines()
    hits = []

    for idx, line in enumerate(lines):
        low = line.lower()

        if (
            "@views_bp.route" in line
            or "render_template" in low
            or "jobs" in low and "template" in low
            or "flows" in low and "template" in low
            or "refine" in low
        ):
            hits.append({
                "line": idx + 1,
                "text": line,
                "context": context_for_lines(lines, idx, 8),
            })

    return hits[:80]


def try_render_routes() -> list[dict]:
    '''
    Best-effort only. This may fail if app creation requires Synology runtime state or auth.
    '''
    results = []

    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(ROOT))

    candidates = [
        "/flows",
        "/jobs",
        "/flows/",
        "/jobs/",
    ]

    try:
        app_obj = None

        try:
            from pf67 import create_app
            app_obj = create_app()
        except Exception:
            try:
                from app import create_app
                app_obj = create_app()
            except Exception:
                app_obj = None

        if app_obj is None:
            results.append({
                "route": "app_import",
                "ok": False,
                "error": "Could not import create_app from pf67 or app.",
            })
            return results

        client = app_obj.test_client()

        for route in candidates:
            try:
                response = client.get(route)
                body = response.get_data(as_text=True)
                low = body.lower()

                snippets = []
                for pattern in ["flowshown", "active", "scheduled", "completed", "public", "private", "refine view"]:
                    pos = low.find(pattern)
                    if pos != -1:
                        start = max(0, pos - 500)
                        end = min(len(body), pos + 800)
                        snippets.append({
                            "pattern": pattern,
                            "snippet": body[start:end],
                        })

                results.append({
                    "route": route,
                    "status_code": response.status_code,
                    "ok": response.status_code < 500,
                    "snippets": snippets,
                })
            except Exception as exc:
                results.append({
                    "route": route,
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                })

    except Exception as exc:
        results.append({
            "route": "test_client_setup",
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })

    return results


def collect_file_names() -> dict:
    return {
        "templates": [str(path.relative_to(ROOT)) for path in sorted(TEMPLATES.rglob("*")) if path.is_file()],
        "pf67_py": [str(path.relative_to(ROOT)) for path in sorted(PF67.rglob("*.py"))],
    }


def main() -> None:
    report = {
        "generated_at": datetime.now().isoformat(),
        "root": str(ROOT),
        "source_hits": search_sources(),
        "views_route_context": inspect_routes_static(),
        "render_attempts": try_render_routes(),
        "file_names": collect_file_names(),
    }

    out_path = ROOT / ("pf67_backend_flows_pill_diagnostic_" + STAMP + ".json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Wrote", out_path)
    print("Upload this JSON report here before the next patch.")


if __name__ == "__main__":
    main()
