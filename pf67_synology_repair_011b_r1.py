#!/usr/bin/env python3
'''
PF67 Synology Repair Patch 011B-R1 - Roll Back Completed Step Shading

Run from either:
  /volume1/docker/pf67
or:
  /volume1/docker/pf67/app

Recommended:
  cd /volume1/docker/pf67
  python pf67_synology_repair_011b_r1.py

Purpose:
- Restore the container after 011B broke the app.
- Prefer restoring files from the automatic 011B backup directory.
- If no backup is found, surgically remove 011B template/CSS changes.
- This should return the app to the previous working 011A2 state.
- Does not touch database, Flow creation, Procedure behavior, or step ordering.
'''

from __future__ import annotations

import re
import shutil
import sys
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
TEMPLATES = ROOT / "app" / "templates"
BACKUP_ROOT = ROOT / "_pf67_patch_backups"
REPAIR_BACKUP_DIR = BACKUP_ROOT / ("repair_011b_r1_" + STAMP)

CHANGES = []
WARNINGS = []


def log(message: str) -> None:
    print(message)


def warn(message: str) -> None:
    WARNINGS.append(message)
    print("WARNING: " + message)


def backup_current(path: Path) -> None:
    if not path.exists():
        return

    REPAIR_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(ROOT)
    dest = REPAIR_BACKUP_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def write_file(path: Path, content: str) -> None:
    backup_current(path)
    path.write_text(content, encoding="utf-8")
    CHANGES.append("Wrote " + str(path.relative_to(ROOT)))
    log("Wrote " + str(path.relative_to(ROOT)))


def latest_011b_backup() -> Path | None:
    if not BACKUP_ROOT.exists():
        return None

    candidates = [
        path for path in BACKUP_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("patch_011b_")
    ]

    if not candidates:
        return None

    candidates.sort(key=lambda path: path.name, reverse=True)
    return candidates[0]


def restore_from_backup() -> bool:
    backup_dir = latest_011b_backup()

    if not backup_dir:
        warn("No patch_011b_* backup directory found; using surgical cleanup fallback.")
        return False

    restored_any = False

    for rel in [
        Path("app/templates/base.html"),
        Path("app/templates/job_detail.html"),
    ]:
        src = backup_dir / rel
        dest = ROOT / rel

        if not src.exists():
            warn("Backup file missing: " + str(src))
            continue

        backup_current(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        restored_any = True
        CHANGES.append("Restored " + str(rel) + " from " + str(backup_dir.relative_to(ROOT)))
        log("Restored " + str(rel) + " from " + str(backup_dir.relative_to(ROOT)))

    return restored_any


def remove_script_by_function(text: str, function_name: str) -> str:
    marker = "function " + function_name

    while True:
        marker_pos = text.find(marker)

        if marker_pos == -1:
            return text

        script_start = text.rfind("<script", 0, marker_pos)
        script_end = text.find("</script>", marker_pos)

        if script_start == -1 or script_end == -1:
            return text

        script_end += len("</script>")
        text = text[:script_start] + text[script_end:]


def remove_css_block(text: str, marker: str) -> str:
    while True:
        start = text.find(marker)

        if start == -1:
            return text

        next_patch = text.find("        /* PF67 Patch", start + len(marker))
        style_end = text.find("</style>", start)
        candidates = [pos for pos in [next_patch, style_end] if pos != -1]

        if not candidates:
            return text

        end = min(candidates)
        text = text[:start] + text[end:]


def surgical_base_cleanup() -> None:
    path = TEMPLATES / "base.html"

    if not path.exists():
        warn("base.html not found for surgical cleanup.")
        return

    text = path.read_text(encoding="utf-8")
    original = text

    text = remove_css_block(text, "        /* PF67 Patch 011B Completed Flow Step Shading */")
    text = remove_script_by_function(text, "pf67Patch011BLoadedStamp")

    footer_start = text.find('<div class="pf67-footer">')

    if footer_start != -1:
        footer_end = text.find("</div>", footer_start)

        if footer_end != -1:
            footer_end += len("</div>")
            new_footer = '''<div class="pf67-footer">
        PF67 · <span class="pf67-footer-rev">Test Rev 011A2 Flow Creation Sequence</span>
        · App {{ pf67_revision }}
        · <span class="pf67-footer-loaded">Loaded <span id="pf67-loaded-stamp">...</span></span>
    </div>'''
            text = text[:footer_start] + new_footer + text[footer_end:]

    if text != original:
        write_file(path, text)
    else:
        log("base.html surgical cleanup: unchanged.")


def surgical_job_detail_cleanup() -> None:
    path = TEMPLATES / "job_detail.html"

    if not path.exists():
        warn("job_detail.html not found for surgical cleanup.")
        return

    text = path.read_text(encoding="utf-8")
    original = text

    # Remove the exact 011B class snippets.
    text = text.replace(' {% if step.completed_at %}pf67-flow-step-completed{% endif %}', '')
    text = text.replace(' {% if step.completed_at %}pf67-flow-step-pane-completed{% endif %}', '')

    # Remove any variations caused by whitespace or quote differences.
    text = re.sub(
        r'\s*{%\s*if\s+step\.completed_at\s*%}\s*pf67-flow-step-completed\s*{%\s*endif\s*%}',
        '',
        text,
    )
    text = re.sub(
        r'\s*{%\s*if\s+step\.completed_at\s*%}\s*pf67-flow-step-pane-completed\s*{%\s*endif\s*%}',
        '',
        text,
    )

    # Remove optional checkmark marker.
    text = re.sub(
        r'\s*{%\s*if\s+step\.completed_at\s*%}\s*<span\s+class=["\']pf67-step-completed-marker["\'][^>]*>.*?</span>\s*{%\s*endif\s*%}',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if text != original:
        write_file(path, text)
    else:
        log("job_detail.html surgical cleanup: unchanged.")


def write_report(restored_from_backup: bool) -> None:
    report = ROOT / ("pf67_repair_011b_r1_report_" + STAMP + ".txt")

    def list_or_none(values):
        if not values:
            return "- None\n"

        return "".join("- " + str(value) + "\n" for value in values)

    report.write_text(
        "PF67 Repair 011B-R1 completed.\n"
        "Root: " + str(ROOT) + "\n"
        "Repair backup of pre-repair files: " + str(REPAIR_BACKUP_DIR) + "\n\n"
        "What happened:\n"
        "- 011B touched base.html and job_detail.html for completed-step shading.\n"
        "- The uploaded Docker log export did not include a Python/Jinja traceback, so this repair rolls back the last visual patch safely.\n\n"
        "Repair method:\n"
        + ("- Restored from the automatic patch_011b_* backup directory.\n" if restored_from_backup else "- No 011B backup found; used surgical cleanup fallback.\n")
        + "\nChanges:\n"
        + list_or_none(CHANGES)
        + "\nWarnings:\n"
        + list_or_none(WARNINGS)
        + "\nTest checklist:\n"
        "1. Restart the PF67 container.\n"
        "2. Confirm the app loads again.\n"
        "3. Confirm footer is back to Test Rev 011A2 Flow Creation Sequence or the prior working footer.\n"
        "4. Open a Flow and confirm step order from 011A2 still works.\n"
        "5. Do not retest completed-step shading yet; this repair intentionally removes 011B.\n\n"
        "Next safer implementation plan:\n"
        "- Use the actual app/templates/job_detail.html file to make a direct, minimal template edit.\n"
        "- Avoid broad regex/template guessing.\n",
        encoding="utf-8",
    )
    log("Wrote " + str(report.relative_to(ROOT)))


def main() -> None:
    log("PF67 Repair 011B-R1 starting")
    log("Project root: " + str(ROOT))

    restored = restore_from_backup()

    if not restored:
        surgical_base_cleanup()
        surgical_job_detail_cleanup()

    write_report(restored)

    log("")
    log("PF67 Repair 011B-R1 complete.")
    log("Restart your PF67 container and confirm the app is back up.")


if __name__ == "__main__":
    main()
