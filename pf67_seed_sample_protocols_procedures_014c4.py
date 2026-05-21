#!/usr/bin/env python3
"""
PF67 Sample Data Seed 014C4 - Exact Schema, Clear Sample Records, Reinsert Protocols and Procedures

Run:
  cd /volume1/docker/pf67
  python pf67_seed_sample_protocols_procedures_014c4.py

Optional:
  python pf67_seed_sample_protocols_procedures_014c4.py --dry-run
  python pf67_seed_sample_protocols_procedures_014c4.py --no-restart
  python pf67_seed_sample_protocols_procedures_014c4.py --db data/db/pf67.sqlite3

What this script does:
- Uses the exact schema confirmed by diagnostic:
    protocol_templates:
      id, name, description, current_version, created_at, category,
      is_private, protocol_role, can_start_flow
    step_templates:
      id, template_id, sort_order, name, step_type, instructions_html,
      minimum_minutes, ideal_minutes, limit_minutes, detrimental_minutes,
      failure_minutes, estimated_duration_minutes, context_tag, step_kind,
      procedure_template_id, procedure_snapshot_title, procedure_snapshot_description,
      procedure_snapshot_minutes
- Deletes only existing Sample records:
    protocol_templates where name like 'Sample %'
    related step_templates and protocol_attachments for those sample IDs
- Reinserts sample Procedures and Protocols with:
    protocol_role = 'procedure' or 'protocol'
    instructions_html filled correctly
    timing windows placed on the next actual task, not on fake check-only steps
    reference image blocks in descriptions and selected steps
- Does not delete real/non-sample records.
- Does not create jobs/flows.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


VERSION = "014C4"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def find_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, cwd.parent, Path("/volume1/docker/pf67")]:
        if (candidate / "data").is_dir() or (candidate / "app").is_dir():
            return candidate
    print("ERROR: Could not find PF67 root. Run from /volume1/docker/pf67.")
    sys.exit(1)


ROOT = find_root()
BACKUP_DIR = ROOT / "_pf67_patch_backups" / ("seed_014c4_" + STAMP)
REPORT_PATH = ROOT / ("pf67_seed_014c4_report_" + STAMP + ".txt")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def hours(n: int) -> int:
    return n * 60


def days(n: int) -> int:
    return n * 24 * 60


def image_block(label: str, search_terms: str) -> str:
    """
    Use a deterministic labeled placeholder image so it always appears on-topic,
    plus a manual Commons MediaSearch URL under it for later replacement with a real uploaded image.
    """
    text = quote(label[:80])
    placeholder = "https://placehold.co/1200x800/png?text=" + text
    media_search = "https://commons.wikimedia.org/w/index.php?search=" + quote(search_terms) + "&title=Special:MediaSearch&type=image"
    return (
        '<figure class="pf67-reference-image" style="margin: 12px 0;">'
        '<img src="' + html.escape(placeholder, quote=True) + '" alt="' + html.escape(label, quote=True) + '" '
        'style="max-width:100%;height:auto;border-radius:12px;">'
        '<figcaption style="font-size:0.9em;opacity:0.78;margin-top:6px;">'
        '<strong>Temporary reference image:</strong> ' + html.escape(label) + '<br>'
        'Replace this placeholder with an uploaded PF67 image when ready.<br>'
        '<strong>Placeholder URL:</strong><br><code>' + html.escape(placeholder) + '</code><br>'
        '<strong>Suggested image search:</strong><br><code>' + html.escape(media_search) + '</code>'
        '</figcaption>'
        '</figure>'
    )


def p(text: str) -> str:
    return "<p>" + html.escape(text) + "</p>"


def html_body(text: str, image_label: str | None = None, search_terms: str | None = None) -> str:
    out = p(text)
    if image_label and search_terms:
        out += image_block(image_label, search_terms)
    return out


def item(kind: str, title: str, category: str, description: str, steps: list[dict], public: bool = False, image_label: str | None = None, search_terms: str | None = None):
    return {
        "kind": kind,
        "title": title,
        "category": category,
        "description": html_body(description, image_label, search_terms),
        "is_private": 0 if public else 1,
        "can_start_flow": 1,
        "steps": steps,
    }


def step(title: str, instructions: str, *, step_type: str = "task", context: str = "", minimum=None, ideal=None, limit=None, risk=None, failure=None, estimated=10, image_label=None, search_terms=None):
    return {
        "title": title,
        "step_type": step_type,
        "context_tag": context,
        "instructions_html": html_body(instructions, image_label, search_terms),
        "minimum_minutes": minimum,
        "ideal_minutes": ideal,
        "limit_minutes": limit,
        "detrimental_minutes": risk,
        "failure_minutes": failure,
        "estimated_duration_minutes": estimated,
    }


SAMPLES = [
    item(
        "procedure",
        "Sample Procedure - Sanitize Fermenter, Lid, Bung, and Airlock",
        "Fermentation Cleanup",
        "Reusable Procedure for preparing a fermenter and airlock hardware before a fermentation Protocol.",
        [
            step("Inspect fermenter and fittings", "Inspect the fermenter, lid, bung, airlock, gasket, and valve areas. Remove residue and confirm there are no cracks, dried material, or questionable seals before washing.", context="inspection", estimated=10, image_label="Fermenter fittings and airlock", search_terms="fermenter airlock gasket"),
            step("Wash and rinse contact parts", "Wash the vessel and all contact parts using your normal cleaner. Rinse well, then move directly into sanitizer contact time so the equipment does not sit around partially cleaned.", context="wash", estimated=20),
            step("Sanitize and drain", "Sanitize the vessel, lid, bung, airlock, and tools using your normal contact-time method. Let contact time complete, then drain and stage parts so they are ready to fill within about half an hour.", context="sanitize", minimum=5, ideal=10, limit=30, risk=60, estimated=10),
        ],
        image_label="Fermenter sanitation setup",
        search_terms="fermenter airlock homebrewing sanitation",
    ),
    item(
        "procedure",
        "Sample Procedure - Clean Reflux Column and Condenser After Use",
        "Equipment Cleanup",
        "Reusable equipment-cleaning Procedure for cooldown, rinse, inspection, drying, and storage of a reflux column and condenser. This is not an operating Protocol.",
        [
            step("Allow equipment to cool", "Leave the column, condenser, and connected fittings alone until they are cool enough to handle. Check after about half an hour if there is any uncertainty; the ideal next action is disassembly after about one hour.", context="cooldown", minimum=30, ideal=60, limit=120, estimated=5, image_label="Condenser cooldown reference", search_terms="Liebig condenser glassware"),
            step("Disassemble and rinse removable parts", "Disassemble only after cooldown. Rinse removable parts and inspect gaskets, clamps, condenser ports, and column sections for residue or blocked paths.", context="rinse", estimated=30),
            step("Dry and store glassware and fittings", "Let parts dry fully before storage. Check after two hours for water trapped in the condenser jacket, tubing, or joints; the ideal next action is storage after about four hours.", context="drying", minimum=120, ideal=240, limit=480, risk=days(1), estimated=15),
        ],
        image_label="Reflux column and condenser cleaning",
        search_terms="reflux condenser laboratory glassware",
    ),
    item(
        "procedure",
        "Sample Procedure - Leak Check Condenser Water Lines",
        "Equipment Setup",
        "Reusable Procedure for checking condenser water-line connections before any workflow that relies on steady cooling water.",
        [
            step("Connect cooling-water lines", "Connect inlet and outlet lines and confirm tubing is routed safely. Open water slowly and watch for drips, loose clamps, kinks, or blocked outlet flow.", context="setup", estimated=10, image_label="Condenser tubing leak check", search_terms="condenser water tubing laboratory"),
            step("Observe flow and connection points", "Let water run long enough to confirm stable flow and dry connection points. Check again after about five minutes; the ideal next action is to mark the condenser ready after ten minutes with no dripping or movement.", context="leak_check", minimum=5, ideal=10, limit=20, risk=30, failure=60, estimated=5),
        ],
        image_label="Condenser water-line check",
        search_terms="condenser water line tubing",
    ),
    item(
        "procedure",
        "Sample Procedure - Prepare Laminar Airflow Hood Before Petri Pouring",
        "Mycology Lab",
        "Reusable setup Procedure for clearing, wiping, staging, and running in the laminar airflow hood before Petri dish work.",
        [
            step("Clear and wipe work area", "Remove nonessential items, wipe the working surface, and stage only the supplies needed for the session. Once wiped down, start the hood and let it run before opening sterile supplies.", context="hood_setup", estimated=15, image_label="Laminar airflow hood setup", search_terms="laminar flow cabinet laboratory"),
            step("Run hood before sterile handling", "Let the hood run before opening sterile items. Check after about fifteen minutes that airflow is unobstructed and the work area remains clear; the ideal next action is the final sterile-field check after about twenty minutes.", context="hood_run_in", minimum=15, ideal=20, limit=35, estimated=5),
            step("Final sterile-field check", "Confirm plates, media, labels, and tools are positioned for controlled handling. If the setup was interrupted, reset the area before pouring.", context="sterile_field", estimated=5),
        ],
        image_label="Laminar airflow hood reference",
        search_terms="laminar flow cabinet",
    ),
    item(
        "procedure",
        "Sample Procedure - Reset Microgreen Gear After Harvest",
        "Microgreens Cleanup",
        "Reusable cleanup Procedure for trays, domes, mats, scissors, and storage after a microgreen harvest.",
        [
            step("Clear crop residue and media", "Remove crop residue and spent media from trays and harvest tools. Keep dirty items grouped so washing can happen in one pass.", context="cleanup", estimated=15, image_label="Microgreen trays after harvest", search_terms="microgreens tray harvest"),
            step("Wash and sanitize trays and tools", "Wash trays, domes, mats, scissors, and any reusable surfaces. Apply your normal sanitizer contact time and let it complete before drying.", context="sanitize", minimum=10, ideal=30, limit=75, risk=120, estimated=30),
            step("Dry and store in cabinet", "Let cleaned items dry fully before stacking. Check after about one hour; the ideal next action is cabinet storage after about two hours if corners and ridges are dry.", context="drying", minimum=60, ideal=120, limit=240, estimated=10),
        ],
        image_label="Clean microgreen trays",
        search_terms="microgreens trays cleaning",
    ),
    item(
        "protocol",
        "Sample Protocol - Birdwatcher-Style Sugar Wash Fermenter Observation",
        "Fermentation",
        "Sample fermentation observation Protocol for testing PF67 timing windows on a sugar-wash style fermenter. This is a timing/workflow sample only and does not include distillation or collection instructions.",
        [
            step("Prepare fermenter and start wash", "Prepare the fermenter, confirm sanitation is complete, and stage ingredients according to your legal and appropriate recipe. After filling and mixing, seal the fermenter. Check after about two days that bubbling, temperature, airlock liquid, and top seal look normal; the ideal next action is fermentation evaluation after about one week.", context="fermenter", estimated=60, image_label="Fermenter with airlock", search_terms="fermenter airlock bucket"),
            step("Evaluate primary fermentation", "Evaluate whether fermentation appears complete using your normal non-operational checks and notes. If it needs more time, leave it alone and check again tomorrow; the ideal next action is final outcome review after two more days.", context="fermentation", step_type="wait", minimum=days(2), ideal=days(7), limit=days(10), risk=days(14), estimated=15),
            step("Final outcome review and cleanup decision", "Record the result, attach photos if useful, and clean the fermenter or move into the appropriate next legal process outside this sample Protocol.", context="review", step_type="wait", minimum=days(1), ideal=days(2), limit=days(4), estimated=30),
        ],
        public=False,
        image_label="Sugar wash fermenter observation",
        search_terms="fermentation airlock bucket",
    ),
    item(
        "protocol",
        "Sample Protocol - Wine Kit in Conical Fermenter",
        "Wine Fermentation",
        "Sample wine-kit Protocol for a conical fermenter. This tests long waits, kit-style stage notes, racking/clearing readiness, and final bottling readiness.",
        [
            step("Prepare conical fermenter and mix kit", "Sanitize the conical fermenter, lid, airlock, valve area, spoon, and kit-contact tools. Mix the wine kit according to the kit directions, seal the lid, and set the airlock. Check after about two days that the airlock, seal, and temperature look normal; the ideal next action is primary evaluation after about one week.", context="wine_primary", estimated=60, image_label="Conical fermenter setup", search_terms="conical fermenter wine airlock"),
            step("Evaluate primary fermentation", "Evaluate primary fermentation using the kit's expected signs and your normal hydrometer or visual notes. If it is ready, move to the kit's stabilization, racking, or clearing stage. If it is not ready, check tomorrow and aim for the next review within two days.", context="wine_primary", step_type="wait", minimum=days(2), ideal=days(7), limit=days(10), risk=days(14), estimated=20),
            step("Start clearing and settling period", "After the kit's clearing or stabilizing stage, leave the wine to settle. Check after about one week for visible clearing and sediment; the ideal next action is final readiness review after about two weeks.", context="clearing", step_type="wait", minimum=days(1), ideal=days(2), limit=days(5), estimated=30),
            step("Bottle-readiness review", "Review clarity, sediment, aroma, and kit guidance before bottling or aging. Record the outcome and attach photos if useful.", context="bottling", step_type="wait", minimum=days(7), ideal=days(14), limit=days(21), estimated=45),
        ],
        public=True,
        image_label="Conical wine fermenter",
        search_terms="conical fermenter wine",
    ),
    item(
        "protocol",
        "Sample Protocol - Reflux Condenser Cleanup and Storage",
        "Equipment Cleanup",
        "Sample equipment workflow for cooldown, water-line checks, cleaning, and storage notes for a reflux column and condenser. It does not include operation or product collection instructions.",
        [
            step("Confirm cooldown and water shutoff", "Confirm the equipment is cool enough to handle and the cooling water is shut off. Check after about thirty minutes if there is any uncertainty; the ideal next action is disassembly after one hour.", context="cooldown", minimum=30, ideal=60, limit=120, estimated=5, image_label="Reflux condenser glassware", search_terms="reflux condenser glassware"),
            step("Disconnect condenser lines", "Disconnect condenser water lines carefully and drain trapped water into a safe container. Inspect tubing and fittings before cleaning.", context="disconnect", estimated=15),
            step("Clean, dry, and store parts", "Clean the column and condenser using your normal safe equipment-cleaning method. Let parts air dry; check after two hours for water trapped in the condenser jacket or fittings, with ideal storage after four hours.", context="storage", minimum=120, ideal=240, limit=480, risk=days(1), estimated=20),
        ],
        public=False,
        image_label="Reflux condenser cleanup",
        search_terms="reflux condenser laboratory glassware",
    ),
    item(
        "protocol",
        "Sample Protocol - Broccoli Microgreens Tray",
        "Microgreens",
        "Sample broccoli microgreen Protocol for testing blackout timing, moisture checks, light transition, and harvest readiness.",
        [
            step("Seed broccoli tray and start blackout", "Prepare the tray, moisten the media, spread broccoli seed evenly, and cover or stack for blackout. Check after about eighteen hours for moisture if needed; the ideal next action is the first tray check after one day.", context="seeding", estimated=30, image_label="Broccoli microgreens tray", search_terms="broccoli microgreens tray"),
            step("Continue blackout or prepare for light", "Check moisture and germination. Mist only if needed and continue blackout if the tray is not pushing evenly. Check again tomorrow; the ideal next action is the light-transition decision.", context="blackout", step_type="wait", minimum=hours(18), ideal=days(1), limit=hours(36), estimated=10),
            step("Move tray into light", "Move the tray into light when the crop is ready. Water as needed and watch for dry corners. Check after one day if the tray dries quickly; the ideal next action is harvest-readiness review after two days.", context="light", step_type="wait", minimum=hours(18), ideal=days(1), limit=days(2), estimated=10),
            step("Review harvest readiness", "Evaluate height, color, and cotyledon development. Harvest if ready, or continue another day and record why.", context="harvest", step_type="wait", minimum=days(1), ideal=days(2), limit=days(3), estimated=30),
        ],
        public=True,
        image_label="Broccoli microgreens",
        search_terms="broccoli microgreens",
    ),
    item(
        "protocol",
        "Sample Protocol - Radish Microgreens Tray",
        "Microgreens",
        "Fast microgreen sample Protocol for radish trays with a shorter harvest window.",
        [
            step("Seed radish tray and cover", "Prepare the tray and spread radish seed evenly. Cover or stack for blackout. Check after about eighteen hours for moisture and fast germination; the ideal next action is first tray review after one day.", context="seeding", estimated=25, image_label="Radish microgreens tray", search_terms="radish microgreens tray"),
            step("Move to light or continue blackout", "Inspect germination and crop push. Move to light if the tray is ready, otherwise continue blackout briefly. Check tomorrow for color and height development.", context="blackout", step_type="wait", minimum=hours(18), ideal=days(1), limit=days(2), estimated=10),
            step("Harvest-readiness review", "Evaluate color, texture, and height. Radish often moves quickly, so harvest if ready or continue one more day with a note.", context="harvest", step_type="wait", minimum=days(2), ideal=days(3), limit=days(5), risk=days(7), estimated=30),
        ],
        public=True,
        image_label="Radish microgreens",
        search_terms="radish microgreens",
    ),
    item(
        "protocol",
        "Sample Protocol - Sunflower Microgreens Tray",
        "Microgreens",
        "Sunflower microgreen Protocol for testing soak, blackout, hull monitoring, light transition, and harvest timing.",
        [
            step("Soak sunflower seed", "Start the sunflower seed soak using your normal container. Check in the morning that the seed is hydrated and ready to drain; the ideal next action is tray setup after about twelve hours.", context="soak", minimum=hours(10), ideal=hours(12), limit=hours(16), risk=hours(24), estimated=10, image_label="Sunflower microgreen seed soak", search_terms="sunflower microgreens seeds"),
            step("Drain seed and start tray", "Drain soaked seed, prepare the tray, spread seed evenly, and cover or stack. Check after about thirty-six hours for rooting and moisture; the ideal next action is blackout review after two days.", context="setup", estimated=25),
            step("Move sunflower tray into light", "Move to light when roots are established and the tray is ready. Watch for hull shedding and moisture. Check after two days if hulls are holding; the ideal next action is harvest review after three days.", context="light", step_type="wait", minimum=hours(36), ideal=days(2), limit=days(3), estimated=15),
            step("Harvest or continue briefly", "Evaluate height, hulls, and leaf quality. Harvest if ready, or continue one more day with notes.", context="harvest", step_type="wait", minimum=days(2), ideal=days(3), limit=days(5), estimated=30),
        ],
        public=True,
        image_label="Sunflower microgreens",
        search_terms="sunflower microgreens tray",
    ),
    item(
        "protocol",
        "Sample Protocol - Lettuce Leafy Greens Tray",
        "Leafy Greens",
        "Sample leafy greens tray Protocol for testing longer indoor greens timing beyond quick microgreens.",
        [
            step("Seed lettuce tray", "Prepare the tray, seed lettuce evenly, and water gently. Check after two days for surface moisture if needed; the ideal next action is germination review after three days.", context="seeding", estimated=30, image_label="Lettuce leafy greens tray", search_terms="lettuce seedlings tray"),
            step("Thin or space seedlings if needed", "Review germination and thin crowded areas if needed. Keep the tray evenly moist. Check after five days for growth and light balance; the ideal next action is growth review after one week.", context="growth", step_type="wait", minimum=days(2), ideal=days(3), limit=days(5), estimated=15),
            step("Review baby-leaf harvest point", "Evaluate leaf size and quality. Harvest baby greens if ready, or continue another few days and record the reason.", context="harvest", step_type="wait", minimum=days(10), ideal=days(14), limit=days(21), risk=days(28), estimated=30),
        ],
        public=True,
        image_label="Leafy greens tray",
        search_terms="lettuce leafy greens tray",
    ),
    item(
        "protocol",
        "Sample Protocol - Potato Plants in Raised Bed",
        "Garden Beds",
        "Sample raised-bed potato Protocol for testing garden timing, hilling intervals, and harvest windows.",
        [
            step("Plant seed potatoes in raised bed", "Plant seed potatoes in the prepared raised bed using your normal spacing and depth. Check after ten days for emergence if weather has been warm; the ideal next action is emergence review after two weeks.", context="planting", estimated=45, image_label="Potato plants in raised bed", search_terms="potato plants raised bed"),
            step("First hilling when plants are established", "When plants are established and tall enough, hill soil or loose mulch around the stems while leaving top growth exposed. Check after ten days for continued growth; the ideal next action is the next hilling decision after two weeks.", context="hilling", step_type="wait", minimum=days(10), ideal=days(14), limit=days(21), estimated=30),
            step("Second hilling and bed inspection", "Hill again if plants are still growing actively. Inspect moisture, weeds, and exposed tubers. Check after five weeks if foliage changes; the ideal next action is pre-harvest review after about six weeks.", context="hilling", step_type="wait", minimum=days(10), ideal=days(14), limit=days(21), estimated=30),
            step("Harvest-window review", "Review foliage condition, variety timing, and bed moisture. Harvest if appropriate or schedule a later harvest note.", context="harvest", step_type="wait", minimum=days(35), ideal=days(42), limit=days(56), estimated=45),
        ],
        public=True,
        image_label="Potato raised bed",
        search_terms="potato plants raised bed garden",
    ),
    item(
        "protocol",
        "Sample Protocol - Onion Sets in Raised Bed",
        "Garden Beds",
        "Sample raised-bed onion Protocol for testing long plant-to-harvest timing and curing notes.",
        [
            step("Plant onion sets", "Plant onion sets in the prepared raised bed with tips up and spacing appropriate to the bed plan. Check after ten days for establishment and moisture; the ideal next action is early growth review after two weeks.", context="planting", estimated=40, image_label="Onion sets in raised bed", search_terms="onion plants raised bed"),
            step("Early growth and weed review", "Review growth, moisture, and weeds. Keep the bed evenly watered and open. Check after three weeks if weeds or dry soil are showing; the ideal next action is bulbing progress review after one month.", context="growth", step_type="wait", minimum=days(10), ideal=days(14), limit=days(21), estimated=20),
            step("Bulbing and harvest timing review", "Review bulb development and foliage condition. Check earlier if tops begin falling over; the ideal next action is harvest readiness review in about six weeks.", context="bulbing", step_type="wait", minimum=days(21), ideal=days(30), limit=days(45), estimated=20),
            step("Harvest and cure onions", "Harvest when the crop is ready and begin curing in a dry, ventilated location. Check after five days; the ideal next action is storage review after one week.", context="curing", step_type="wait", minimum=days(28), ideal=days(42), limit=days(60), estimated=45),
            step("Storage-readiness review", "Review neck dryness, skins, and storage condition. Continue curing if necks or skins are not ready for storage.", context="storage", step_type="wait", minimum=days(5), ideal=days(7), limit=days(14), risk=days(21), estimated=20),
        ],
        public=True,
        image_label="Onion curing for storage",
        search_terms="onion curing storage garden",
    ),
]


def choose_db(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if not path.is_absolute():
            path = ROOT / path
    else:
        path = ROOT / "data" / "db" / "pf67.sqlite3"
    if not path.exists():
        print("ERROR: DB not found:", path)
        sys.exit(1)
    return path


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def backup_db(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / db_path.name
    shutil.copy2(db_path, backup)
    return backup


def clear_existing_samples(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    sample_rows = conn.execute(
        "select id, name from protocol_templates where name like 'Sample %' order by id"
    ).fetchall()
    ids = [row[0] for row in sample_rows]
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)

    conn.execute("delete from step_templates where template_id in (" + placeholders + ")", ids)

    if table_exists(conn, "protocol_attachments"):
        conn.execute("delete from protocol_attachments where template_id in (" + placeholders + ")", ids)

    conn.execute("delete from protocol_templates where id in (" + placeholders + ")", ids)

    return [(int(row[0]), str(row[1])) for row in sample_rows]


def insert_protocol_template(conn: sqlite3.Connection, sample: dict) -> int:
    cur = conn.execute(
        """
        insert into protocol_templates
            (name, description, current_version, created_at, category, is_private, protocol_role, can_start_flow)
        values
            (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample["title"],
            sample["description"],
            1,
            now(),
            sample["category"],
            int(sample["is_private"]),
            sample["kind"],
            int(sample["can_start_flow"]),
        ),
    )
    return int(cur.lastrowid)


def insert_step_template(conn: sqlite3.Connection, template_id: int, sort_order: int, data: dict) -> int:
    cur = conn.execute(
        """
        insert into step_templates
            (template_id, sort_order, name, step_type, instructions_html,
             minimum_minutes, ideal_minutes, limit_minutes, detrimental_minutes, failure_minutes,
             estimated_duration_minutes, context_tag, step_kind, procedure_template_id,
             procedure_snapshot_title, procedure_snapshot_description, procedure_snapshot_minutes)
        values
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            template_id,
            sort_order,
            data["title"],
            data.get("step_type") or "task",
            data.get("instructions_html") or "",
            data.get("minimum_minutes"),
            data.get("ideal_minutes"),
            data.get("limit_minutes"),
            data.get("detrimental_minutes"),
            data.get("failure_minutes"),
            data.get("estimated_duration_minutes"),
            data.get("context_tag") or "",
            "step",
            None,
            None,
            None,
            0,
        ),
    )
    return int(cur.lastrowid)


def seed(db_path: Path, dry_run: bool) -> dict:
    conn = sqlite3.connect(str(db_path))
    backup = backup_db(db_path)

    for required in ["protocol_templates", "step_templates"]:
        if not table_exists(conn, required):
            conn.close()
            print("ERROR: required table missing:", required)
            sys.exit(1)

    removed = clear_existing_samples(conn)
    created = []

    for sample in SAMPLES:
        template_id = insert_protocol_template(conn, sample)
        for index, st in enumerate(sample["steps"], start=1):
            insert_step_template(conn, template_id, index, st)
        created.append({"id": template_id, "title": sample["title"], "kind": sample["kind"], "steps": len(sample["steps"])})

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    conn.close()

    return {
        "db": str(db_path),
        "backup": str(backup),
        "dry_run": dry_run,
        "removed": removed,
        "created": created,
    }


def write_report(report: dict):
    removed = "\n".join("- id " + str(row_id) + ": " + title for row_id, title in report["removed"]) or "- None"
    created = "\n".join("- id " + str(item["id"]) + " " + item["kind"] + ": " + item["title"] + " (" + str(item["steps"]) + " steps)" for item in report["created"]) or "- None"
    REPORT_PATH.write_text(
        "PF67 Seed 014C4 report\n"
        "Dry run: " + str(report["dry_run"]) + "\n"
        "Database: " + report["db"] + "\n"
        "Backup: " + report["backup"] + "\n\n"
        "Removed existing Sample records:\n" + removed + "\n\n"
        "Created Sample records:\n" + created + "\n\n"
        "Notes:\n"
        "- Procedure records use protocol_templates.protocol_role = 'procedure'.\n"
        "- Protocol records use protocol_templates.protocol_role = 'protocol'.\n"
        "- Step instructions are written to step_templates.instructions_html.\n"
        "- Check windows are stored in minimum_minutes.\n"
        "- Ideal windows are stored in ideal_minutes.\n"
        "- Limit windows are stored in limit_minutes.\n"
        "- Risk windows are stored in detrimental_minutes.\n"
        "- Failure windows are stored in failure_minutes.\n"
        "- No jobs/flows were created.\n",
        encoding="utf-8",
    )
    print("Wrote", REPORT_PATH.relative_to(ROOT))


def restart(no_restart: bool, dry_run: bool):
    if no_restart or dry_run:
        print("Skipping Docker restart.")
        return
    try:
        result = subprocess.run(["docker", "restart", "pf67"], text=True, capture_output=True)
        if result.returncode == 0:
            print("Restarted Docker container: pf67")
        else:
            print("WARNING: docker restart pf67 failed")
            print((result.stderr or result.stdout or "").strip())
            print("Run manually: docker restart pf67")
    except Exception as exc:
        print("WARNING: docker restart pf67 failed:", exc)
        print("Run manually: docker restart pf67")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    print("PF67 Seed 014C4 starting")
    db_path = choose_db(args.db)
    print("Database:", db_path)
    report = seed(db_path, args.dry_run)
    write_report(report)
    print("Removed existing samples:", len(report["removed"]))
    print("Created samples:", len(report["created"]))
    restart(args.no_restart, args.dry_run)
    print("PF67 Seed 014C4 complete")


if __name__ == "__main__":
    main()
