#!/usr/bin/env python3
"""
PF67 Sample Data Seed 014C2 - Realistic Protocols, Procedures, Timed Wording, Reference Images

Run:
  cd /volume1/docker/pf67
  python pf67_seed_sample_protocols_procedures_014c3.py

Optional:
  python pf67_seed_sample_protocols_procedures_014c3.py --dry-run
  python pf67_seed_sample_protocols_procedures_014c3.py --no-restart
  python pf67_seed_sample_protocols_procedures_014c3.py --db app/data/pf67.db

Purpose:
- Seed realistic sample Procedures and Protocols for testing PF67.
- Correct timing-language model:
    The step text explains the wait/check expectation.
    The same step stores check/ideal/limit/risk/failure timing values.
    A separate "check only" step is not created unless the check is the actual work due at ideal time.
- Add external reference image blocks using <img> tags so they appear in the WYSIWYG editor.
- Show the image URL directly under each image so it is easy to replace with an uploaded PF67 image.
- Keep distillation-related samples limited to equipment cleaning, condenser water checks, cooldown, and storage.
  This script does not seed instructions for producing, collecting, or separating distilled alcohol.
- Idempotent/update behavior:
    Existing sample records with the same title are updated and their sample steps are rebuilt.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


VERSION = "014C3"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def image_block(label: str, keywords: str) -> str:
    url = "https://source.unsplash.com/1200x800/?" + quote(keywords, safe=",")
    safe_label = html.escape(label)
    safe_url = html.escape(url, quote=True)
    return (
        "<figure class=\"pf67-reference-image\" style=\"margin: 12px 0;\">"
        "<img src=\"" + safe_url + "\" alt=\"" + safe_label + "\" "
        "style=\"max-width: 100%; height: auto; border-radius: 12px;\">"
        "<figcaption style=\"font-size: 0.9em; opacity: 0.78; margin-top: 6px;\">"
        "<strong>Temporary reference image:</strong> " + safe_label + "<br>"
        "<span>Replace this external image with an uploaded PF67 image when ready.</span><br>"
        "<code>" + safe_url + "</code>"
        "</figcaption>"
        "</figure>"
    )


def p(text: str) -> str:
    return "<p>" + html.escape(text) + "</p>"


def body(text: str, image_label: str | None = None, image_keywords: str | None = None) -> str:
    out = p(text)
    if image_label and image_keywords:
        out += image_block(image_label, image_keywords)
    return out


SAMPLES = [
    {
        "kind": "procedure",
        "title": "Sample Procedure - Sanitize Fermenter, Lid, Bung, and Airlock",
        "category": "Fermentation Cleanup",
        "description": body(
            "Reusable Procedure for preparing a fermenter and airlock hardware before a fermentation Protocol.",
            "Sanitized fermenter and airlock reference",
            "fermenter,airlock,homebrewing"
        ),
        "public": False,
        "steps": [
            {
                "title": "Inspect fermenter and fittings",
                "instructions": body(
                    "Inspect the fermenter, lid, bung, airlock, gasket, and valve areas. Remove residue and confirm there are no cracks, dried material, or questionable seals before washing.",
                    "Fermenter fittings inspection",
                    "fermenter,airlock,gasket"
                ),
                "ideal_minutes": 10,
                "limit_minutes": 30,
            },
            {
                "title": "Wash and rinse all contact parts",
                "instructions": body(
                    "Wash the vessel and all contact parts using your normal cleaner. Rinse well, then move directly into sanitizer contact time so the equipment does not sit around partially cleaned."
                ),
                "ideal_minutes": 20,
                "limit_minutes": 45,
            },
            {
                "title": "Sanitize and drain",
                "instructions": body(
                    "Sanitize the vessel, lid, bung, airlock, and tools using your normal contact-time method. Let the contact time complete, then drain or stage parts so they are ready for filling within about half an hour."
                ),
                "ideal_minutes": 10,
                "check_minutes": 5,
                "limit_minutes": 30,
                "risk_minutes": 60,
            },
        ],
    },
    {
        "kind": "procedure",
        "title": "Sample Procedure - Clean Reflux Column and Condenser After Use",
        "category": "Equipment Cleanup",
        "description": body(
            "Reusable equipment-cleaning Procedure for cooldown, rinse, inspection, drying, and storage of a reflux column and condenser. This is not an operating Protocol.",
            "Reflux column and condenser cleaning reference",
            "laboratory,condenser,glassware"
        ),
        "public": False,
        "steps": [
            {
                "title": "Cool equipment before handling",
                "instructions": body(
                    "Allow the column, condenser, and connected fittings to cool fully before disassembly. Check after about half an hour; if any parts are still warm or pressurized, leave them alone and continue cooling."
                ),
                "ideal_minutes": 60,
                "check_minutes": 30,
                "limit_minutes": 120,
            },
            {
                "title": "Disassemble and rinse removable parts",
                "instructions": body(
                    "Disassemble only after cooldown. Rinse removable parts and inspect gaskets, clamps, condenser ports, and column sections for residue or blocked paths."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 90,
            },
            {
                "title": "Dry and store glassware and fittings",
                "instructions": body(
                    "Let parts dry fully before storage. Check after two hours; if moisture remains in the condenser jacket, tubing, or joints, leave parts open longer before storing."
                ),
                "ideal_minutes": 180,
                "check_minutes": 120,
                "limit_minutes": 360,
                "risk_minutes": 24 * 60,
            },
        ],
    },
    {
        "kind": "procedure",
        "title": "Sample Procedure - Leak Check Condenser Water Lines",
        "category": "Equipment Setup",
        "description": body(
            "Reusable Procedure for checking condenser water-line connections before any workflow that relies on steady cooling water.",
            "Condenser water line leak check reference",
            "condenser,water,tubing,lab"
        ),
        "public": False,
        "steps": [
            {
                "title": "Connect cooling-water lines",
                "instructions": body(
                    "Connect inlet and outlet lines and confirm tubing is routed safely. Open the water slowly and watch for drips, loose clamps, kinks, or blocked outlet flow."
                ),
                "ideal_minutes": 10,
                "limit_minutes": 20,
            },
            {
                "title": "Run a short leak observation",
                "instructions": body(
                    "Let water run long enough to confirm stable flow and dry connection points. Check again after about five minutes; there should be no dripping, pooling, or tubing movement before the equipment is considered ready."
                ),
                "ideal_minutes": 10,
                "check_minutes": 5,
                "limit_minutes": 20,
                "risk_minutes": 30,
                "failure_minutes": 60,
            },
        ],
    },
    {
        "kind": "procedure",
        "title": "Sample Procedure - Prepare Laminar Airflow Hood Before Petri Pouring",
        "category": "Mycology Lab",
        "description": body(
            "Reusable setup Procedure for clearing, wiping, staging, and running in the laminar airflow hood before Petri dish work.",
            "Laminar airflow hood setup reference",
            "laminar,flow,hood,laboratory"
        ),
        "public": False,
        "steps": [
            {
                "title": "Clear and wipe the work area",
                "instructions": body(
                    "Remove nonessential items, wipe the working surface, and stage only the supplies needed for the session. Once wiped down, start the hood and let it run before opening sterile supplies."
                ),
                "ideal_minutes": 15,
                "limit_minutes": 30,
            },
            {
                "title": "Run hood before sterile handling",
                "instructions": body(
                    "Let the hood run before opening sterile items. Check after about fifteen minutes that airflow is unobstructed and the work area remains clear; begin the next setup action after about twenty minutes."
                ),
                "ideal_minutes": 20,
                "check_minutes": 15,
                "limit_minutes": 35,
            },
            {
                "title": "Final sterile-field check",
                "instructions": body(
                    "Confirm plates, media, labels, and tools are positioned for controlled handling. If the setup was interrupted, reset the area before pouring."
                ),
                "ideal_minutes": 5,
                "limit_minutes": 15,
                "risk_minutes": 30,
            },
        ],
    },
    {
        "kind": "procedure",
        "title": "Sample Procedure - Reset Microgreen Gear After Harvest",
        "category": "Microgreens Cleanup",
        "description": body(
            "Reusable cleanup Procedure for trays, domes, mats, scissors, and storage after a microgreen harvest.",
            "Clean microgreen trays reference",
            "microgreens,trays,cleaning"
        ),
        "public": False,
        "steps": [
            {
                "title": "Clear crop residue and media",
                "instructions": body(
                    "Remove crop residue and spent media from trays and harvest tools. Keep the dirty items grouped so washing can happen in one pass."
                ),
                "ideal_minutes": 15,
                "limit_minutes": 45,
            },
            {
                "title": "Wash and sanitize trays and tools",
                "instructions": body(
                    "Wash trays, domes, mats, scissors, and any reusable surfaces. Apply your normal sanitizer contact time and let it complete before drying."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 75,
                "risk_minutes": 120,
            },
            {
                "title": "Dry and store in cabinet",
                "instructions": body(
                    "Let cleaned items dry fully before stacking. Check after about an hour; if moisture remains in corners or under ridges, leave them open longer before storing."
                ),
                "ideal_minutes": 120,
                "check_minutes": 60,
                "limit_minutes": 240,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Birdwatcher-Style Sugar Wash Fermenter Observation",
        "category": "Fermentation",
        "description": body(
            "Sample fermentation observation Protocol for testing PF67 timing windows on a sugar-wash style fermenter. This is a timing/workflow sample only and does not include distillation or collection instructions.",
            "Sugar wash fermenter reference",
            "fermentation,airlock,bucket"
        ),
        "public": False,
        "steps": [
            {
                "title": "Prepare fermenter and ingredients",
                "instructions": body(
                    "Prepare the fermenter, confirm sanitation is complete, and stage ingredients according to your legal and appropriate recipe. After filling and mixing, the important next action is the active-fermentation check tomorrow."
                ),
                "ideal_minutes": 60,
                "limit_minutes": 180,
            },
            {
                "title": "Start fermentation and seal fermenter",
                "instructions": body(
                    "Pitch or start the fermenter according to your chosen method, seal it, and set the airlock or top seal. Leave it to ferment for about one week before evaluation. Check after about two days that bubbling, temperature, airlock liquid, and top seal look normal."
                ),
                "ideal_minutes": 7 * 24 * 60,
                "check_minutes": 2 * 24 * 60,
                "limit_minutes": 10 * 24 * 60,
                "risk_minutes": 14 * 24 * 60,
            },
            {
                "title": "Evaluate fermentation completion",
                "instructions": body(
                    "Evaluate whether the fermentation appears complete using your normal non-operational checks and notes. If it needs more time, leave it alone and check again in two days; the next ideal action is to confirm it has settled enough to move or archive."
                ),
                "ideal_minutes": 2 * 24 * 60,
                "check_minutes": 24 * 60,
                "limit_minutes": 4 * 24 * 60,
                "risk_minutes": 7 * 24 * 60,
            },
            {
                "title": "Record outcome and clean down",
                "instructions": body(
                    "Record the result, attach photos if useful, and clean the fermenter or move into the appropriate next legal process outside this sample Protocol."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 120,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Wine Kit in Conical Fermenter",
        "category": "Wine Fermentation",
        "description": body(
            "Sample wine-kit Protocol for a conical fermenter. This is intended to test long waits, kit-style stage notes, racking/clearing readiness, and final bottling readiness.",
            "Conical wine fermenter reference",
            "conical,fermenter,wine,airlock"
        ),
        "public": True,
        "steps": [
            {
                "title": "Prepare conical fermenter and kit materials",
                "instructions": body(
                    "Sanitize the conical fermenter, lid, airlock, valve area, spoon, and kit-contact tools. Stage the kit materials and prepare to mix according to the kit instructions."
                ),
                "ideal_minutes": 45,
                "limit_minutes": 120,
            },
            {
                "title": "Mix kit and start primary fermentation",
                "instructions": body(
                    "Mix the wine kit in the conical fermenter according to the kit directions, pitch as directed, seal the lid, and set the airlock. Leave it for primary fermentation. Check after about two days that the airlock, seal, and temperature look normal; the ideal next action is the primary evaluation after about one week."
                ),
                "ideal_minutes": 7 * 24 * 60,
                "check_minutes": 2 * 24 * 60,
                "limit_minutes": 10 * 24 * 60,
                "risk_minutes": 14 * 24 * 60,
            },
            {
                "title": "Evaluate primary fermentation",
                "instructions": body(
                    "Evaluate primary fermentation using the kit's expected signs and your normal hydrometer or visual notes. If it is ready, move to the kit's stabilization, racking, or clearing stage. If it is not ready, leave it and check again in two days."
                ),
                "ideal_minutes": 2 * 24 * 60,
                "check_minutes": 24 * 60,
                "limit_minutes": 5 * 24 * 60,
            },
            {
                "title": "Clearing and settling period",
                "instructions": body(
                    "After the kit's clearing/stabilizing stage, leave the wine to settle. Check after about a week for visible clearing and sediment. The ideal next action is final readiness review after about two weeks."
                ),
                "ideal_minutes": 14 * 24 * 60,
                "check_minutes": 7 * 24 * 60,
                "limit_minutes": 21 * 24 * 60,
            },
            {
                "title": "Bottle-readiness review",
                "instructions": body(
                    "Review clarity, sediment, aroma, and kit guidance before bottling or aging. Record the outcome and attach photos if helpful."
                ),
                "ideal_minutes": 45,
                "limit_minutes": 180,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Reflux Condenser Cleanup and Storage",
        "category": "Equipment Cleanup",
        "description": body(
            "Sample equipment workflow for testing cooldown, water-line checks, cleaning, and storage notes for a reflux column and condenser. It does not include operation or product collection instructions.",
            "Reflux condenser storage reference",
            "reflux,condenser,glassware"
        ),
        "public": False,
        "steps": [
            {
                "title": "Confirm cooldown and water shutoff",
                "instructions": body(
                    "Confirm the equipment is cool enough to handle and the cooling water is shut off. Check after about thirty minutes if there is any uncertainty; the ideal next action is disassembly after one hour."
                ),
                "ideal_minutes": 60,
                "check_minutes": 30,
                "limit_minutes": 120,
            },
            {
                "title": "Disconnect condenser lines",
                "instructions": body(
                    "Disconnect condenser water lines carefully and drain trapped water into a safe container. Inspect tubing and fittings before cleaning."
                ),
                "ideal_minutes": 15,
                "limit_minutes": 45,
            },
            {
                "title": "Clean and dry column parts",
                "instructions": body(
                    "Clean the column and condenser using your normal safe equipment-cleaning method. Let parts air dry; check after two hours for water trapped in the condenser jacket or fittings."
                ),
                "ideal_minutes": 4 * 60,
                "check_minutes": 2 * 60,
                "limit_minutes": 8 * 60,
                "risk_minutes": 24 * 60,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Broccoli Microgreens Tray",
        "category": "Microgreens",
        "description": body(
            "Sample broccoli microgreen Protocol for testing blackout timing, daily moisture checks, light transition, and harvest readiness.",
            "Broccoli microgreens tray reference",
            "broccoli,microgreens,tray"
        ),
        "public": True,
        "steps": [
            {
                "title": "Seed broccoli tray and start blackout",
                "instructions": body(
                    "Prepare the tray, moisten the media, spread broccoli seed evenly, and cover or stack for blackout. Check tomorrow for moisture and germination; the ideal next action is the first tray check after one day."
                ),
                "ideal_minutes": 24 * 60,
                "check_minutes": 18 * 60,
                "limit_minutes": 36 * 60,
            },
            {
                "title": "Continue blackout if germination is uneven",
                "instructions": body(
                    "Check moisture and germination. Mist only if needed and continue blackout if the tray is not pushing evenly. Check again tomorrow; the ideal next action is the light-transition decision."
                ),
                "ideal_minutes": 24 * 60,
                "check_minutes": 18 * 60,
                "limit_minutes": 36 * 60,
            },
            {
                "title": "Move tray into light",
                "instructions": body(
                    "Move the tray into light when the crop is ready. Water as needed and watch for dry corners. Check after two days; the ideal next action is harvest-readiness review."
                ),
                "ideal_minutes": 2 * 24 * 60,
                "check_minutes": 24 * 60,
                "limit_minutes": 3 * 24 * 60,
            },
            {
                "title": "Review harvest readiness",
                "instructions": body(
                    "Evaluate height, color, and cotyledon development. Harvest if ready, or continue another day and record why."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 180,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Radish Microgreens Tray",
        "category": "Microgreens",
        "description": body(
            "Fast microgreen sample Protocol for radish trays with a shorter harvest window.",
            "Radish microgreens tray reference",
            "radish,microgreens,tray"
        ),
        "public": True,
        "steps": [
            {
                "title": "Seed radish tray and cover",
                "instructions": body(
                    "Prepare the tray and spread radish seed evenly. Cover or stack for blackout. Check tomorrow for moisture and fast germination; the ideal next action is the first tray check after one day."
                ),
                "ideal_minutes": 24 * 60,
                "check_minutes": 18 * 60,
                "limit_minutes": 36 * 60,
            },
            {
                "title": "Move to light or continue blackout",
                "instructions": body(
                    "Inspect germination and crop push. Move to light if the tray is ready, otherwise continue blackout briefly. Check tomorrow for color and height development."
                ),
                "ideal_minutes": 24 * 60,
                "check_minutes": 18 * 60,
                "limit_minutes": 48 * 60,
            },
            {
                "title": "Harvest-readiness check",
                "instructions": body(
                    "Evaluate color, texture, and height. Radish often moves quickly, so harvest if ready or continue one more day with a note."
                ),
                "ideal_minutes": 3 * 24 * 60,
                "check_minutes": 2 * 24 * 60,
                "limit_minutes": 5 * 24 * 60,
                "risk_minutes": 7 * 24 * 60,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Sunflower Microgreens Tray",
        "category": "Microgreens",
        "description": body(
            "Sunflower microgreen Protocol for testing soak, blackout, hull monitoring, light transition, and harvest timing.",
            "Sunflower microgreens tray reference",
            "sunflower,microgreens,tray"
        ),
        "public": True,
        "steps": [
            {
                "title": "Soak sunflower seed",
                "instructions": body(
                    "Start the sunflower seed soak using your normal container. Leave it overnight; check in the morning that the seed is hydrated and ready to drain."
                ),
                "ideal_minutes": 12 * 60,
                "check_minutes": 10 * 60,
                "limit_minutes": 16 * 60,
                "risk_minutes": 24 * 60,
            },
            {
                "title": "Drain seed and start tray",
                "instructions": body(
                    "Drain soaked seed, prepare the tray, spread seed evenly, and cover or stack. Check after two days for rooting and moisture; the ideal next action is blackout review."
                ),
                "ideal_minutes": 2 * 24 * 60,
                "check_minutes": 36 * 60,
                "limit_minutes": 3 * 24 * 60,
            },
            {
                "title": "Move sunflower tray into light",
                "instructions": body(
                    "Move to light when roots are established and the tray is ready. Watch for hull shedding and moisture. Check after three days for harvest readiness."
                ),
                "ideal_minutes": 3 * 24 * 60,
                "check_minutes": 2 * 24 * 60,
                "limit_minutes": 5 * 24 * 60,
            },
            {
                "title": "Harvest or continue briefly",
                "instructions": body(
                    "Evaluate height, hulls, and leaf quality. Harvest if ready, or continue one more day with notes."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 180,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Lettuce Leafy Greens Tray",
        "category": "Leafy Greens",
        "description": body(
            "Sample leafy greens tray Protocol for testing longer indoor greens timing beyond quick microgreens.",
            "Lettuce leafy greens tray reference",
            "lettuce,leafy,greens,tray"
        ),
        "public": True,
        "steps": [
            {
                "title": "Seed lettuce tray",
                "instructions": body(
                    "Prepare the tray, seed lettuce evenly, and water gently. Check in three days for germination and surface moisture; the ideal next action is germination review."
                ),
                "ideal_minutes": 3 * 24 * 60,
                "check_minutes": 2 * 24 * 60,
                "limit_minutes": 5 * 24 * 60,
            },
            {
                "title": "Thin or space seedlings if needed",
                "instructions": body(
                    "Review germination and thin crowded areas if needed. Keep the tray evenly moist. Check in one week for growth and light balance."
                ),
                "ideal_minutes": 7 * 24 * 60,
                "check_minutes": 5 * 24 * 60,
                "limit_minutes": 10 * 24 * 60,
            },
            {
                "title": "Review baby-leaf harvest point",
                "instructions": body(
                    "Evaluate leaf size and quality. Harvest baby greens if ready, or continue another few days and record the reason."
                ),
                "ideal_minutes": 14 * 24 * 60,
                "check_minutes": 10 * 24 * 60,
                "limit_minutes": 21 * 24 * 60,
                "risk_minutes": 28 * 24 * 60,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Potato Plants in Raised Bed",
        "category": "Garden Beds",
        "description": body(
            "Sample raised-bed potato Protocol for testing garden timing, hilling intervals, and harvest windows.",
            "Potato plants raised bed reference",
            "potato,plants,raised,bed,garden"
        ),
        "public": True,
        "steps": [
            {
                "title": "Plant seed potatoes in raised bed",
                "instructions": body(
                    "Plant seed potatoes in the prepared raised bed using your normal spacing and depth. Check in two weeks for emergence; the ideal next action is emergence review."
                ),
                "ideal_minutes": 14 * 24 * 60,
                "check_minutes": 10 * 24 * 60,
                "limit_minutes": 21 * 24 * 60,
            },
            {
                "title": "First hilling when plants are established",
                "instructions": body(
                    "When plants are established and tall enough, hill soil or loose mulch around the stems while leaving the top growth exposed. Check in two weeks for the next hilling need."
                ),
                "ideal_minutes": 14 * 24 * 60,
                "check_minutes": 10 * 24 * 60,
                "limit_minutes": 21 * 24 * 60,
            },
            {
                "title": "Second hilling and bed inspection",
                "instructions": body(
                    "Hill again if plants are still growing actively. Inspect moisture, weeds, and exposed tubers. The ideal next action is the pre-harvest review after about six weeks."
                ),
                "ideal_minutes": 42 * 24 * 60,
                "check_minutes": 35 * 24 * 60,
                "limit_minutes": 56 * 24 * 60,
            },
            {
                "title": "Harvest-window review",
                "instructions": body(
                    "Review foliage condition, variety timing, and bed moisture. Harvest if appropriate or schedule a later harvest note."
                ),
                "ideal_minutes": 30,
                "limit_minutes": 180,
            },
        ],
    },
    {
        "kind": "protocol",
        "title": "Sample Protocol - Onion Sets in Raised Bed",
        "category": "Garden Beds",
        "description": body(
            "Sample raised-bed onion Protocol for testing long plant-to-harvest timing and curing notes.",
            "Onion sets raised bed reference",
            "onion,sets,raised,bed,garden"
        ),
        "public": True,
        "steps": [
            {
                "title": "Plant onion sets",
                "instructions": body(
                    "Plant onion sets in the prepared raised bed with tips up and spacing appropriate to the bed plan. Check in two weeks for establishment and moisture."
                ),
                "ideal_minutes": 14 * 24 * 60,
                "check_minutes": 10 * 24 * 60,
                "limit_minutes": 21 * 24 * 60,
            },
            {
                "title": "Early growth and weed review",
                "instructions": body(
                    "Review growth, moisture, and weeds. Keep the bed evenly watered and open. Check again in one month for bulbing progress."
                ),
                "ideal_minutes": 30 * 24 * 60,
                "check_minutes": 21 * 24 * 60,
                "limit_minutes": 45 * 24 * 60,
            },
            {
                "title": "Bulbing and harvest timing review",
                "instructions": body(
                    "Review bulb development and foliage condition. The ideal next action is harvest readiness review in about six weeks, but check earlier if tops begin falling over."
                ),
                "ideal_minutes": 42 * 24 * 60,
                "check_minutes": 28 * 24 * 60,
                "limit_minutes": 60 * 24 * 60,
            },
            {
                "title": "Harvest and cure onions",
                "instructions": body(
                    "Harvest when the crop is ready and begin curing in a dry, ventilated location. Check after one week and continue curing if necks or skins are not ready for storage."
                ),
                "ideal_minutes": 7 * 24 * 60,
                "check_minutes": 5 * 24 * 60,
                "limit_minutes": 14 * 24 * 60,
                "risk_minutes": 21 * 24 * 60,
            },
        ],
    },
]


def find_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, cwd.parent, Path("/volume1/docker/pf67")]:
        if (candidate / "app").is_dir():
            return candidate
    print("ERROR: Could not find PF67 project root.")
    sys.exit(1)


ROOT = find_root()
BACKUP_DIR = ROOT / "_pf67_patch_backups" / ("seed_014c3_" + STAMP)
REPORT_PATH = ROOT / ("pf67_seed_014c3_report_" + STAMP + ".txt")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def candidate_db_files() -> list[Path]:
    out = []
    for pattern in ["*.db", "*.sqlite", "*.sqlite3"]:
        out.extend(ROOT.rglob(pattern))
    blocked = {".git", "__pycache__", "_pf67_patch_backups", "node_modules"}
    clean = []
    for path in out:
        if blocked & set(path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.stat().st_size > 0:
            clean.append(path)
    return sorted(clean, key=lambda p: p.stat().st_mtime, reverse=True)


def tables_for(db: Path) -> list[str]:
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def choose_db(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            print("ERROR: DB not found:", path)
            sys.exit(1)
        return path
    scored = []
    for path in candidate_db_files():
        tables = [t.lower() for t in tables_for(path)]
        score = 0
        for t in tables:
            if t in ("protocol_templates", "protocol_template", "templates", "template", "protocols", "protocol"):
                score += 10
            if "step" in t:
                score += 6
            if t in ("jobs", "job", "flows", "flow"):
                score += 3
        scored.append((score, path))
    if not scored:
        print("ERROR: Could not find DB. Pass --db.")
        sys.exit(1)
    scored.sort(key=lambda x: (x[0], x[1].stat().st_mtime), reverse=True)
    return scored[0][1]


def table_info(conn, table):
    rows = conn.execute("pragma table_info(" + quote_ident(table) + ")").fetchall()
    return [{"name": r[1], "type": r[2] or "", "notnull": bool(r[3]), "default": r[4], "pk": bool(r[5])} for r in rows]


def first(cols: set[str], names: list[str]) -> str | None:
    for name in names:
        if name in cols:
            return name
    return None


def detect(conn):
    tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()]
    lower = {t.lower(): t for t in tables}
    p_table = first(set(lower), ["protocol_templates", "protocol_template", "protocols", "protocol", "templates", "template"])
    if p_table:
        p_table = lower[p_table]
    else:
        print("ERROR: no protocol/template table. Tables:", ", ".join(tables))
        print("Expected one of: protocol_templates, protocol_template, protocols, protocol, templates, template")
        sys.exit(1)
    s_table = first(set(lower), ["step_templates", "step_template", "protocol_steps", "protocol_step", "template_steps", "template_step", "steps"])
    if s_table:
        s_table = lower[s_table]
    else:
        for t in tables:
            if "step" in t.lower():
                s_table = t
                break
    if not s_table:
        print("ERROR: no step table. Tables:", ", ".join(tables))
        print("Expected one of: step_templates, step_template, protocol_steps, protocol_step, template_steps, template_step, steps")
        sys.exit(1)
    p_cols = table_info(conn, p_table)
    s_cols = table_info(conn, s_table)
    return p_table, s_table, p_cols, s_cols


def map_protocol(cols):
    names = {c["name"] for c in cols}
    return {
        "id": first(names, ["id", "protocol_template_id", "template_id", "protocol_id"]),
        "title": first(names, ["title", "name", "template_name", "protocol_name", "label"]),
        "description": first(names, ["description", "body", "content", "instructions", "notes", "summary", "details"]),
        "category": first(names, ["category", "subject", "group_name", "type"]),
        "kind": first(names, ["kind", "record_type", "template_type", "protocol_type", "type", "mode"]),
        "is_procedure": first(names, ["is_procedure", "procedure", "is_subprotocol", "is_sub_protocol"]),
        "is_public": first(names, ["is_public", "public", "is_visible_publicly", "show_public"]),
        "created_at": first(names, ["created_at", "date_created"]),
        "updated_at": first(names, ["updated_at", "modified_at", "date_modified"]),
        "duration": first(names, ["duration_minutes", "estimated_duration_minutes", "total_duration_minutes"]),
    }


def map_step(cols):
    names = {c["name"] for c in cols}
    return {
        "id": first(names, ["id", "step_id"]),
        "parent_id": first(names, ["protocol_template_id", "template_id", "protocol_id", "parent_id"]),
        "title": first(names, ["title", "name", "step_title", "step_name", "label"]),
        "instructions": first(names, ["instructions", "description", "body", "content", "notes", "html", "details"]),
        "sequence": first(names, ["sequence", "sort_order", "position", "step_order", "order_index", "display_order", "sort_index"]),
        "created_at": first(names, ["created_at", "date_created"]),
        "updated_at": first(names, ["updated_at", "modified_at", "date_modified"]),
        "check": first(names, ["check_minutes", "min_minutes", "minimum_minutes", "safe_check_minutes", "earliest_minutes"]),
        "ideal": first(names, ["ideal_minutes", "wait_minutes", "duration_minutes", "offset_minutes", "minutes"]),
        "limit": first(names, ["limit_minutes", "late_minutes", "latest_minutes"]),
        "risk": first(names, ["risk_minutes", "detrimental_minutes"]),
        "failure": first(names, ["failure_minutes", "fail_minutes", "spoilage_minutes"]),
    }


def default_value(col):
    name = col["name"].lower()
    typ = col["type"].lower()
    if col["pk"]:
        return None
    if name in ("created_at", "updated_at", "modified_at", "date_created", "date_modified"):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if name in ("title", "name"):
        return "Sample"
    if name in ("description", "instructions", "body", "content", "notes", "summary"):
        return ""
    if name in ("category", "subject", "type", "kind"):
        return "Sample"
    if "int" in typ or "real" in typ or "numeric" in typ:
        return 0
    return ""


def build_values(cols, desired):
    names = {c["name"] for c in cols}
    by = {c["name"]: c for c in cols}
    vals = {}
    for k, v in desired.items():
        if k in names:
            vals[k] = int(v) if isinstance(v, bool) else v
    for col in cols:
        name = col["name"]
        if name not in vals and not col["pk"] and col["notnull"] and col["default"] is None:
            vals[name] = default_value(col)
    return vals


def insert_row(conn, table, vals):
    keys = list(vals)
    sql = "insert into " + quote_ident(table) + " (" + ", ".join(quote_ident(k) for k in keys) + ") values (" + ", ".join("?" for _ in keys) + ")"
    cur = conn.execute(sql, [vals[k] for k in keys])
    return int(cur.lastrowid)


def fetch_parent_value(conn, table, rowid, id_col):
    if id_col:
        row = conn.execute("select " + quote_ident(id_col) + " from " + quote_ident(table) + " where rowid = ?", (rowid,)).fetchone()
        if row:
            return row[0]
    return rowid


def existing_record(conn, table, title_col, title, id_col):
    select = "rowid"
    if id_col:
        select += ", " + quote_ident(id_col)
    row = conn.execute("select " + select + " from " + quote_ident(table) + " where " + quote_ident(title_col) + " = ? limit 1", (title,)).fetchone()
    if not row:
        return None
    return {"rowid": row[0], "id": row[1] if id_col else row[0]}


def update_record(conn, table, rowid, vals):
    keys = [k for k in vals if k.lower() not in ("id", "rowid")]
    if not keys:
        return
    sql = "update " + quote_ident(table) + " set " + ", ".join(quote_ident(k) + " = ?" for k in keys) + " where rowid = ?"
    conn.execute(sql, [vals[k] for k in keys] + [rowid])


def delete_steps(conn, table, parent_col, parent_value):
    conn.execute("delete from " + quote_ident(table) + " where " + quote_ident(parent_col) + " = ?", (parent_value,))


def total_duration(sample):
    return sum(int(step.get("ideal_minutes") or 0) for step in sample["steps"])


def seed(db_path: Path, dry_run=False):
    conn = sqlite3.connect(str(db_path))
    p_table, s_table, p_cols, s_cols = detect(conn)
    pm = map_protocol(p_cols)
    sm = map_step(s_cols)
    if not pm["title"] or not sm["parent_id"] or not sm["title"]:
        raise RuntimeError("Could not detect required title/parent columns.")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    db_backup = BACKUP_DIR / db_path.name
    shutil.copy2(db_path, db_backup)

    created = []
    updated = []

    for sample in SAMPLES:
        desired = {pm["title"]: sample["title"]}
        if pm["description"]:
            desired[pm["description"]] = sample["description"]
        if pm["category"]:
            desired[pm["category"]] = sample["category"]
        if pm["kind"]:
            desired[pm["kind"]] = sample["kind"]
        if pm["is_procedure"]:
            desired[pm["is_procedure"]] = sample["kind"] == "procedure"
        if pm["is_public"]:
            desired[pm["is_public"]] = bool(sample["public"])
        if pm["created_at"]:
            desired[pm["created_at"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pm["updated_at"]:
            desired[pm["updated_at"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pm["duration"]:
            desired[pm["duration"]] = total_duration(sample)

        vals = build_values(p_cols, desired)
        existing = existing_record(conn, p_table, pm["title"], sample["title"], pm["id"])

        if existing:
            update_record(conn, p_table, existing["rowid"], vals)
            parent_value = existing["id"]
            delete_steps(conn, s_table, sm["parent_id"], parent_value)
            updated.append(sample["title"])
        else:
            rowid = insert_row(conn, p_table, vals)
            parent_value = fetch_parent_value(conn, p_table, rowid, pm["id"])
            created.append(sample["title"])

        for idx, step in enumerate(sample["steps"], start=1):
            sd = {sm["parent_id"]: parent_value, sm["title"]: step["title"]}
            if sm["instructions"]:
                sd[sm["instructions"]] = step["instructions"]
            if sm["sequence"]:
                sd[sm["sequence"]] = idx
            if sm["created_at"]:
                sd[sm["created_at"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if sm["updated_at"]:
                sd[sm["updated_at"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for source, dest in [
                ("check_minutes", sm["check"]),
                ("ideal_minutes", sm["ideal"]),
                ("limit_minutes", sm["limit"]),
                ("risk_minutes", sm["risk"]),
                ("failure_minutes", sm["failure"]),
            ]:
                if dest and source in step:
                    sd[dest] = int(step[source])
            insert_row(conn, s_table, build_values(s_cols, sd))

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    return {
        "database": str(db_path),
        "backup": str(db_backup),
        "protocol_table": p_table,
        "step_table": s_table,
        "created": created,
        "updated": updated,
        "protocol_columns": pm,
        "step_columns": sm,
    }


def write_report(report, dry_run):
    REPORT_PATH.write_text(
        "PF67 Seed 014C3 report\n"
        "Dry run: " + str(dry_run) + "\n"
        "Database: " + report["database"] + "\n"
        "Backup: " + report["backup"] + "\n"
        "Protocol table: " + report["protocol_table"] + "\n"
        "Step table: " + report["step_table"] + "\n\n"
        "Created:\n" + ("\n".join("- " + x for x in report["created"]) if report["created"] else "- None") + "\n\n"
        "Updated/rebuilt:\n" + ("\n".join("- " + x for x in report["updated"]) if report["updated"] else "- None") + "\n\n"
        "Protocol columns:\n" + json.dumps(report["protocol_columns"], indent=2) + "\n\n"
        "Step columns:\n" + json.dumps(report["step_columns"], indent=2) + "\n",
        encoding="utf-8"
    )
    print("Wrote", REPORT_PATH.relative_to(ROOT))


def restart(no_restart, dry_run):
    if no_restart or dry_run:
        print("Skipping Docker restart.")
        return
    try:
        r = subprocess.run(["docker", "restart", "pf67"], text=True, capture_output=True)
        if r.returncode == 0:
            print("Restarted Docker container: pf67")
        else:
            print("WARNING: docker restart failed")
            print((r.stderr or r.stdout or "").strip())
    except Exception as exc:
        print("WARNING: docker restart failed:", exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    print("PF67 Seed 014C3 starting")
    db = choose_db(args.db)
    print("Database:", db)
    report = seed(db, dry_run=args.dry_run)
    write_report(report, args.dry_run)
    print("Created:", len(report["created"]))
    print("Updated/rebuilt:", len(report["updated"]))
    restart(args.no_restart, args.dry_run)
    print("PF67 Seed 014C3 complete")


if __name__ == "__main__":
    main()
