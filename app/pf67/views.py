import sqlite3
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from functools import wraps
import re
import json
import hashlib

from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app, session, send_file
from werkzeug.utils import secure_filename

from .models import db, ProtocolTemplate, StepTemplate, Job, JobStep, JobNote
from .services import (
    minutes_from_form,
    human_minutes,
    calculate_step_status,
    status_label,
    status_rank,
    create_job_from_template,
    time_after
)

views_bp = Blueprint("views", __name__)


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp"
}


READY_STATUSES = {
    "checkable",
    "ideal",
    "due",
    "late",
    "risky",
    "critical"
}


STEP_URGENCY = {
    "critical": 1,
    "risky": 2,
    "late": 3,
    "due": 4,
    "ideal": 5,
    "checkable": 6,
    "waiting": 7,
    "completed": 8,
    "skipped": 9,
    "failed": 10
}


DASHBOARD_LIMIT = 10

ACTIVE_JOB_STATUSES = {
    "active"
}

PENDING_JOB_STATUSES = {
    "pending"
}

ARCHIVE_JOB_STATUSES = {
    "complete",
    "cancelled",
    "failed"
}


@views_bp.app_context_processor
def inject_globals():
    return {
        "pf67_revision": current_app.config.get("APP_REVISION", "Unknown Rev"),
        "can_edit": can_edit()
    }


@views_bp.app_template_filter("human_minutes")
def human_minutes_filter(value):
    return human_minutes(value)


@views_bp.app_template_filter("step_status")
def step_status_filter(step):
    return calculate_step_status(step)


@views_bp.app_template_filter("status_label")
def status_label_filter(status):
    return status_label(status)


def can_edit():
    return session.get("pf67_logged_in") is True


def edit_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not can_edit():
            return redirect(url_for("views.login", next=request.path))

        return func(*args, **kwargs)

    return wrapper


def display_datetime(value):
    if value is None:
        return ""

    return value.strftime("%Y-%m-%d %I:%M %p")


def display_short_datetime(value):
    if value is None:
        return ""

    return value.strftime("%b %d, %I:%M %p")


def display_exact_datetime(value):
    if value is None:
        return ""

    return value.strftime("%b %d, %Y %I:%M %p")


def human_day_label(value, now=None):
    if value is None:
        return ""

    if now is None:
        now = datetime.utcnow()

    today = now.date()
    target = value.date()
    delta_days = (target - today).days

    if delta_days == 0:
        return "Today"

    if delta_days == 1:
        return "Tomorrow"

    if delta_days == -1:
        return "Yesterday"

    if 2 <= delta_days <= 6:
        return value.strftime("%A")

    if -6 <= delta_days <= -2:
        return "Last " + value.strftime("%A")

    if value.year == now.year:
        return value.strftime("%b %d")

    return value.strftime("%b %d, %Y")


def datetime_local_value(value):
    if value is None:
        return ""

    return value.strftime("%Y-%m-%dT%H:%M")


def parse_datetime_local(value):
    value = (value or "").strip()

    if not value:
        return datetime.utcnow()

    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return datetime.utcnow()


def minutes_until(value):
    if value is None:
        return None

    delta = value - datetime.utcnow()
    return int(delta.total_seconds() // 60)


def next_ready_time(step):
    if step.minimum_minutes is not None:
        return time_after(step.anchor_time, step.minimum_minutes)

    if step.ideal_minutes is not None:
        return time_after(step.anchor_time, step.ideal_minutes)

    return step.anchor_time


def step_boundary_times(step):
    return {
        "ready": time_after(step.anchor_time, step.minimum_minutes),
        "ideal": time_after(step.anchor_time, step.ideal_minutes),
        "limit": time_after(step.anchor_time, step.limit_minutes),
        "risk": time_after(step.anchor_time, step.detrimental_minutes),
        "failure": time_after(step.anchor_time, step.failure_minutes)
    }


def useful_step_time(step, status=None):
    if status is None:
        status = calculate_step_status(step)

    if step.completed_at:
        return step.completed_at

    boundaries = step_boundary_times(step)

    if status == "waiting":
        return boundaries["ready"] or boundaries["ideal"] or step.anchor_time

    if status == "checkable":
        return boundaries["ready"] or boundaries["ideal"] or step.anchor_time

    if status == "ideal":
        return boundaries["ideal"] or boundaries["ready"] or step.anchor_time

    if status == "late":
        return boundaries["limit"] or boundaries["ideal"] or step.anchor_time

    if status == "risky":
        return boundaries["risk"] or boundaries["limit"] or step.anchor_time

    if status == "critical":
        return boundaries["failure"] or boundaries["risk"] or step.anchor_time

    return boundaries["ready"] or boundaries["ideal"] or step.anchor_time


def step_compact_time_label(step, status=None):
    if status is None:
        status = calculate_step_status(step)

    if step.completed_at:
        return human_day_label(step.completed_at)

    target = useful_step_time(step, status)

    if not target:
        return ""

    return human_day_label(target)


def step_compact_time_title(step, status=None):
    target = useful_step_time(step, status)

    if not target:
        return ""

    return display_exact_datetime(target)


def step_timing_box(step):
    status = calculate_step_status(step)

    if step.completed_at:
        return {
            "status": "Completed",
            "rows": [
                {
                    "label": "Completed",
                    "display": human_day_label(step.completed_at),
                    "title": display_exact_datetime(step.completed_at),
                    "class": "pf67-time-completed"
                }
            ]
        }

    boundaries = step_boundary_times(step)

    rows = []

    labels = [
        ("Check", boundaries["ready"], "pf67-time-ready"),
        ("Ideal", boundaries["ideal"], "pf67-time-ideal"),
        ("Limit", boundaries["limit"], "pf67-time-limit"),
        ("Risk", boundaries["risk"], "pf67-time-risk"),
        ("Failure", boundaries["failure"], "pf67-time-failure")
    ]

    for label, value, class_name in labels:
        if value is not None:
            rows.append({
                "label": label,
                "display": human_day_label(value),
                "title": display_exact_datetime(value),
                "class": class_name
            })

    return {
        "status": status_label(status),
        "rows": rows
    }


def extract_image_urls(html):
    if not html:
        return []

    urls = []

    src_matches = re.findall(r'<img[^>]+src=["\\\']([^"\\\']+)["\\\']', html, flags=re.IGNORECASE)

    for src in src_matches:
        cleaned = src.strip()

        if cleaned and cleaned not in urls:
            urls.append(cleaned)

    return urls


def plain_text_from_html(html):
    text = html or ""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def trim_text(value, limit=100):
    value = value or ""

    if len(value) > limit:
        return value[:limit].rstrip() + "..."

    return value


def note_preview_text(note):
    return trim_text(plain_text_from_html(note.note_html), 100)


def auto_preview_image(note):
    if note.preview_image_url:
        return note.preview_image_url

    images = extract_image_urls(note.note_html)

    if images:
        return images[0]

    return ""


def timeline_kind_label(kind):
    labels = {
        "job_started": "Started",
        "job_note": "Job Note",
        "job_status": "Status",
        "step_completed": "Completed",
        "note": "Note",
        "completion_note": "Completed",
        "edited": "Edited",
        "image": "Image",
        "skipped": "Skipped",
        "failed": "Failed"
    }

    return labels.get(kind, kind.replace("_", " ").title())


def timeline_css_class(kind):
    if kind == "job_started":
        return "pf67-timeline-job"

    if kind == "job_note":
        return "pf67-timeline-job-note"

    if kind == "job_status":
        return "pf67-timeline-status"

    if kind == "step_completed":
        return "pf67-timeline-completed"

    if kind == "completion_note":
        return "pf67-timeline-completion-note"

    if kind == "note":
        return "pf67-timeline-note"

    if kind in ["failed", "critical"]:
        return "pf67-timeline-critical"

    return "pf67-timeline-default"


def get_general_job_note(job):
    note = JobNote.query.filter_by(
        job_id=job.id,
        job_step_id=None,
        note_type="job_note"
    ).order_by(JobNote.event_at.asc(), JobNote.created_at.asc()).first()

    if note:
        return note

    note = JobNote.query.filter_by(
        job_id=job.id,
        job_step_id=None,
        note_type="note"
    ).order_by(JobNote.event_at.asc(), JobNote.created_at.asc()).first()

    return note


def make_job_timeline(job):
    items = []

    general_note = get_general_job_note(job)

    if general_note:
        title = general_note.title or "General Job Note"

        items.append({
            "time": general_note.event_at or general_note.created_at,
            "kind": "job_note",
            "kind_label": "Job Note",
            "watermark": "JOB NOTE",
            "css_class": timeline_css_class("job_note"),
            "title": trim_text(title, 100),
            "preview_text": note_preview_text(general_note),
            "preview_image_url": auto_preview_image(general_note),
            "body_html": general_note.note_html,
            "step": None,
            "step_id": "",
            "note": general_note,
            "is_note": True,
            "url": url_for("views.edit_note", note_id=general_note.id) if can_edit() else url_for("views.job_detail", job_id=job.id)
        })

    else:
        items.append({
            "time": job.started_at,
            "kind": "job_started",
            "kind_label": "Started",
            "watermark": "STARTED",
            "css_class": timeline_css_class("job_started"),
            "title": job.name,
            "preview_text": "",
            "preview_image_url": "",
            "body_html": "",
            "step": None,
            "step_id": "",
            "note": None,
            "is_note": False,
            "url": url_for("views.job_detail", job_id=job.id)
        })

    if job.status != "active":
        items.append({
            "time": job.completed_at or datetime.utcnow(),
            "kind": "job_status",
            "kind_label": "Status",
            "watermark": job.status.upper(),
            "css_class": timeline_css_class("job_status"),
            "title": "Job moved to " + job.status.title(),
            "preview_text": "",
            "preview_image_url": "",
            "body_html": "",
            "step": None,
            "step_id": "",
            "note": None,
            "is_note": False,
            "url": url_for("views.job_detail", job_id=job.id)
        })

    for step in job.steps:
        if step.completed_at:
            items.append({
                "time": step.completed_at,
                "kind": "step_completed",
                "kind_label": "Completed",
                "watermark": "COMPLETED",
                "css_class": timeline_css_class("step_completed"),
                "title": step.name,
                "preview_text": "",
                "preview_image_url": "",
                "body_html": "",
                "step": step,
                "step_id": str(step.id),
                "note": None,
                "is_note": False,
                "url": url_for("views.job_detail", job_id=job.id) + "#step-" + str(step.id)
            })

    if job.status == "active":
        for note in job.notes:
            if general_note and note.id == general_note.id:
                continue

            title = note.title

            if not title:
                title = note_preview_text(note)

            if not title:
                if note.step:
                    title = note.step.name
                else:
                    title = "Job note"

            event_time = note.event_at or note.created_at
            note_type = note.note_type or "note"

            if note.step:
                url = url_for("views.job_detail", job_id=job.id) + "?edit_note=" + str(note.id) + "#step-" + str(note.step.id)
                step_id = str(note.step.id)
            else:
                url = url_for("views.job_detail", job_id=job.id)
                step_id = ""

            items.append({
                "time": event_time,
                "kind": note_type,
                "kind_label": timeline_kind_label(note_type),
                "watermark": timeline_kind_label(note_type).upper(),
                "css_class": timeline_css_class(note_type),
                "title": trim_text(title, 100),
                "preview_text": note_preview_text(note),
                "preview_image_url": auto_preview_image(note),
                "body_html": note.note_html,
                "step": note.step,
                "step_id": step_id,
                "note": note,
                "is_note": True,
                "url": url
            })

    fixed_first = []
    remainder = []

    for item in items:
        if item["kind"] == "job_note" or item["kind"] == "job_started":
            fixed_first.append(item)
        else:
            remainder.append(item)

    remainder.sort(key=lambda item: item["time"])

    return fixed_first + remainder


def list_job_images(job_id):
    image_dir = current_app.config["UPLOAD_DIR"] / "jobs" / str(job_id)

    if not image_dir.exists():
        return []

    images = []

    for path in image_dir.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append({
                "filename": path.name,
                "url": "/static/uploads/jobs/" + str(job_id) + "/" + path.name,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime)
            })

    images.sort(key=lambda item: item["modified_at"], reverse=True)

    return images




def display_note_title(note, fallback_number):
    title = (note.title or "").strip()

    if title:
        return title

    if note.note_type == "completion_note":
        return "Completion Note " + str(fallback_number)

    return "Note " + str(fallback_number)


def get_step_notes(step, edit_note_id=None):
    notes = []
    fallback_number = 1

    for note in step.timeline_notes:
        event_time = note.event_at or note.created_at

        notes.append({
            "note": note,
            "event_at": event_time,
            "event_at_value": datetime_local_value(event_time),
            "event_at_display": display_short_datetime(event_time),
            "title": display_note_title(note, fallback_number),
            "body_html": note.note_html,
            "preview_image_url": auto_preview_image(note),
            "is_editing": edit_note_id is not None and note.id == edit_note_id,
            "edit_url": url_for("views.job_detail", job_id=step.job_id) + "?edit_note=" + str(note.id) + "#step-" + str(step.id)
        })

        fallback_number += 1

    notes.sort(key=lambda item: item["event_at"])

    return notes


def get_default_step_id(job, edit_note_id=None):
    if edit_note_id:
        note = JobNote.query.get(edit_note_id)

        if note and note.job_id == job.id and note.job_step_id:
            return note.job_step_id

    best_step = None
    best_rank = 999

    for step in job.steps:
        status = calculate_step_status(step)
        rank = STEP_URGENCY.get(status, 999)

        if best_step is None or rank < best_rank:
            best_step = step
            best_rank = rank

    if best_step:
        return best_step.id

    return None


def build_recent_movements():
    items = []

    jobs = Job.query.order_by(Job.started_at.desc()).limit(100).all()

    for job in jobs:
        if job.status == "active":
            items.append({
                "time": job.started_at,
                "type": "Job started",
                "title": job.name,
                "subtitle": "Started " + display_datetime(job.started_at),
                "url": url_for("views.job_detail", job_id=job.id)
            })

            for step in job.steps:
                if step.completed_at:
                    items.append({
                        "time": step.completed_at,
                        "type": "Step completed",
                        "title": step.name,
                        "subtitle": job.name,
                        "url": url_for("views.job_detail", job_id=job.id) + "#step-" + str(step.id)
                    })

            for note in job.notes:
                title = note.title or note_preview_text(note) or "Job note"

                if note.step:
                    subtitle = job.name + " / " + note.step.name
                    url = url_for("views.job_detail", job_id=job.id) + "?edit_note=" + str(note.id) + "#step-" + str(note.step.id)
                else:
                    subtitle = job.name
                    url = url_for("views.edit_note", note_id=note.id) if can_edit() else url_for("views.job_detail", job_id=job.id)

                items.append({
                    "time": note.event_at or note.created_at,
                    "type": timeline_kind_label(note.note_type or "note"),
                    "title": trim_text(title, 100),
                    "subtitle": subtitle,
                    "url": url
                })
        else:
            items.append({
                "time": job.completed_at or job.started_at,
                "type": "Job status",
                "title": job.name + " moved to " + job.status.title(),
                "subtitle": "Job no longer active",
                "url": url_for("views.job_detail", job_id=job.id)
            })

    items.sort(key=lambda item: item["time"], reverse=True)

    return items


def build_ready_tasks():
    rows = []

    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    for step in steps:
        status = calculate_step_status(step)

        if status in READY_STATUSES:
            rows.append({
                "step": step,
                "job": step.job,
                "status": status,
                "status_label": status_label(status),
                "rank": status_rank(status),
                "time_label": step_compact_time_label(step, status),
                "time_title": step_compact_time_title(step, status),
                "ready_at": useful_step_time(step, status),
                "url": url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id)
            })

    rows.sort(key=lambda item: (item["rank"], item["job"].name, item["step"].sort_order))

    return rows


def build_upcoming_tasks():
    rows = []

    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    for step in steps:
        status = calculate_step_status(step)

        if status == "waiting":
            ready_at = next_ready_time(step)
            minutes = minutes_until(ready_at)

            rows.append({
                "title": step.name,
                "job_name": step.job.name,
                "context": step.context_tag or "",
                "ready_at": ready_at,
                "time_label": step_compact_time_label(step, status) or "Later",
                "time_title": step_compact_time_title(step, status) or "",
                "url": url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id),
                "ready_in_display": human_minutes(minutes) if minutes is not None and minutes > 0 else "soon",
                "step_id": step.id,
                "job_id": step.job_id,
                "status": status
            })

    rows.sort(key=lambda item: item["ready_at"] or datetime.max)

    return rows


def build_upcoming_board():
    now = datetime.utcnow()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    horizon_start = today + timedelta(days=7)

    board = {
        "today": {
            "title": "Today",
            "items": []
        },
        "tomorrow": {
            "title": "Tomorrow",
            "items": []
        },
        "next_five": {
            "title": "Next 5 Days",
            "items": []
        },
        "horizon": {
            "title": "Horizon",
            "items": []
        }
    }

    for item in build_upcoming_tasks():
        ready_at = item.get("ready_at")

        if ready_at is None:
            board["horizon"]["items"].append(item)
            continue

        ready_date = ready_at.date()

        if ready_date == today:
            board["today"]["items"].append(item)
        elif ready_date == tomorrow:
            board["tomorrow"]["items"].append(item)
        elif ready_date < horizon_start:
            board["next_five"]["items"].append(item)
        else:
            board["horizon"]["items"].append(item)

    visible_columns = []

    for key in ["today", "tomorrow", "next_five", "horizon"]:
        if board[key]["items"]:
            visible_columns.append(board[key])

    return visible_columns


def upcoming_board_fingerprint():
    rows = []

    for item in build_upcoming_tasks():
        ready_at = item.get("ready_at")

        rows.append({
            "step_id": item.get("step_id"),
            "job_id": item.get("job_id"),
            "title": item.get("title"),
            "job_name": item.get("job_name"),
            "ready_at": ready_at.isoformat() if ready_at else "",
            "status": item.get("status")
        })

    rows.sort(key=lambda row: (row["ready_at"], row["job_id"], row["step_id"]))

    payload = json.dumps(rows, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def job_color_class(job):
    colors = [
        "pf67-job-color-1",
        "pf67-job-color-2",
        "pf67-job-color-3",
        "pf67-job-color-4",
        "pf67-job-color-5",
        "pf67-job-color-6"
    ]

    return colors[job.id % len(colors)]


def all_calendar_jobs():
    return Job.query.filter(Job.status.in_(["active", "pending"])).order_by(Job.started_at.desc()).all()




def calendar_status_prefix(status, completed=False):
    if completed:
        return "✓ "

    labels = {
        "waiting": "[Wait] ",
        "checkable": "[Check] ",
        "ideal": "[Ideal] ",
        "due": "[Due] ",
        "late": "[Late] ",
        "risky": "[Risk] ",
        "critical": "[Fail] "
    }

    return labels.get(status, "")

def calendar_events_for_jobs(job_ids, include_completed=True):
    events = []

    if not job_ids:
        return events

    jobs = Job.query.filter(Job.id.in_(job_ids)).all()

    for job in jobs:
        if job.status not in ["active", "pending"]:
            continue

        for step in job.steps:
            status = calculate_step_status(step)

            if step.completed_at:
                if not include_completed:
                    continue

                event_time = step.completed_at
                event_title = calendar_status_prefix(status, completed=True) + step.name
                color_class = "completed"
            else:
                event_time = useful_step_time(step, status)
                event_title = calendar_status_prefix(status) + step.name
                color_class = status

            if not event_time:
                continue

            events.append({
                "id": "step-" + str(step.id),
                "title": event_title,
                "start": event_time.isoformat(),
                "url": url_for("views.job_detail", job_id=job.id) + "#step-" + str(step.id),
                "className": [job_color_class(job), "pf67-cal-status-" + color_class],
                "extendedProps": {
                    "job_id": job.id,
                    "job_name": job.name,
                    "step_id": step.id,
                    "status": status,
                    "context": step.context_tag or ""
                }
            })

    return events


def update_job_status_if_all_steps_done(job):
    if job.status != "active":
        return

    if not job.steps:
        return

    for step in job.steps:
        if not step.completed_at:
            return

    job.status = "pending"
    job.completed_at = datetime.utcnow()


def normalize_template_step_order(template):
    steps = StepTemplate.query.filter_by(template_id=template.id).order_by(StepTemplate.sort_order, StepTemplate.id).all()

    order = 1

    for step in steps:
        step.sort_order = order
        order += 1


# === PF67 REV19 LANDING HELPERS ===

def build_landing_jobs(limit=18):
    jobs = Job.query.filter(Job.status.in_(["active", "pending"])).order_by(Job.started_at.desc()).limit(limit).all()

    rows = []

    for job in jobs:
        rows.append({
            "job": job,
            "url": url_for("views.job_detail", job_id=job.id),
            "status_label": job.status.title(),
            "started_display": display_short_datetime(job.started_at)
        })

    return rows


def build_needs_attention_tasks(limit=8):
    rows = []

    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    for step in steps:
        status = calculate_step_status(step)

        if status in ["late", "risky", "critical"]:
            rows.append({
                "step": step,
                "job": step.job,
                "status": status,
                "status_label": status_label(status),
                "rank": status_rank(status),
                "time_label": step_compact_time_label(step, status),
                "time_title": step_compact_time_title(step, status),
                "url": url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id)
            })

    rows.sort(key=lambda item: (item["rank"], item["job"].name, item["step"].sort_order))

    return rows[:limit]



@views_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == current_app.config.get("EDIT_PASSWORD", "pf67"):
            session["pf67_logged_in"] = True
            next_url = request.args.get("next") or url_for("views.index")
            return redirect(next_url)

        return render_template("login.html", error="Invalid password.")

    return render_template("login.html", error="")


@views_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("views.index"))


@views_bp.route("/")
def index():
    recent_all = build_recent_movements()
    ready_all = build_ready_tasks()
    upcoming_all = build_upcoming_tasks()
    attention_all = build_needs_attention_tasks(8)
    landing_jobs = build_landing_jobs(18)

    return render_template(
        "index.html",
        landing_jobs=landing_jobs,
        recent_movements=recent_all[:DASHBOARD_LIMIT],
        ready_tasks=ready_all[:6],
        upcoming_tasks=upcoming_all[:6],
        attention_tasks=attention_all,
        recent_more_count=max(0, len(recent_all) - DASHBOARD_LIMIT),
        ready_more_count=max(0, len(ready_all) - 6),
        upcoming_more_count=max(0, len(upcoming_all) - 6)
    )


@views_bp.route("/movements")
def movements():
    return render_template(
        "movements.html",
        movements=build_recent_movements()
    )


@views_bp.route("/tasks/ready")
def ready_tasks_page():
    return render_template(
        "tasks_list.html",
        title="Ready Tasks",
        description="Tasks that are checkable, ideal, due, late, risky, or critical.",
        tasks=build_ready_tasks(),
        task_mode="ready"
    )


@views_bp.route("/tasks/upcoming")
def upcoming_tasks_page():
    return render_template(
        "upcoming_board.html",
        board_columns=build_upcoming_board(),
        fingerprint=upcoming_board_fingerprint()
    )


@views_bp.route("/api/tasks/upcoming/fingerprint")
def api_upcoming_fingerprint():
    return jsonify({
        "fingerprint": upcoming_board_fingerprint()
    })


@views_bp.route("/templates")
def templates_page():
    templates = ProtocolTemplate.query.order_by(ProtocolTemplate.name).all()

    return render_template("templates_list.html", templates=templates)


@views_bp.route("/templates/new", methods=["GET", "POST"])
@edit_required
def new_template():
    if request.method == "POST":
        template = ProtocolTemplate(
            name=request.form.get("name", "").strip(),
            description=request.form.get("description", "").strip()
        )

        db.session.add(template)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    return render_template("template_form.html")


@views_bp.route("/templates/<int:template_id>")
def template_detail(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    return render_template("template_detail.html", template=template, today_suffix=datetime.utcnow().strftime("%y%m%d"))


@views_bp.route("/templates/<int:template_id>/steps/new", methods=["POST"])
@edit_required
def add_step(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    next_order = len(template.steps) + 1

    step = StepTemplate(
        template_id=template.id,
        sort_order=next_order,
        name=request.form.get("name", "").strip(),
        step_type=request.form.get("step_type", "action"),
        context_tag=request.form.get("context_tag", "").strip(),
        instructions_html=request.form.get("instructions_html", "").strip(),
        minimum_minutes=minutes_from_form(request.form.get("minimum_value"), request.form.get("minimum_unit")),
        ideal_minutes=minutes_from_form(request.form.get("ideal_value"), request.form.get("ideal_unit")),
        limit_minutes=minutes_from_form(request.form.get("limit_value"), request.form.get("limit_unit")),
        detrimental_minutes=minutes_from_form(request.form.get("detrimental_value"), request.form.get("detrimental_unit")),
        failure_minutes=minutes_from_form(request.form.get("failure_value"), request.form.get("failure_unit")),
        estimated_duration_minutes=minutes_from_form(request.form.get("estimated_value"), request.form.get("estimated_unit"))
    )

    db.session.add(step)
    normalize_template_step_order(template)
    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/templates/<int:template_id>/steps/<int:step_id>/edit", methods=["POST"])
@edit_required
def edit_template_step(template_id, step_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    step = StepTemplate.query.get_or_404(step_id)

    if step.template_id != template.id:
        return redirect(url_for("views.template_detail", template_id=template.id))

    step.name = request.form.get("name", "").strip()
    step.step_type = request.form.get("step_type", "action")
    step.context_tag = request.form.get("context_tag", "").strip()
    step.instructions_html = request.form.get("instructions_html", "").strip()
    step.minimum_minutes = minutes_from_form(request.form.get("minimum_value"), request.form.get("minimum_unit"))
    step.ideal_minutes = minutes_from_form(request.form.get("ideal_value"), request.form.get("ideal_unit"))
    step.limit_minutes = minutes_from_form(request.form.get("limit_value"), request.form.get("limit_unit"))
    step.detrimental_minutes = minutes_from_form(request.form.get("detrimental_value"), request.form.get("detrimental_unit"))
    step.failure_minutes = minutes_from_form(request.form.get("failure_value"), request.form.get("failure_unit"))
    step.estimated_duration_minutes = minutes_from_form(request.form.get("estimated_value"), request.form.get("estimated_unit"))

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id) + "#step-template-" + str(step.id))


@views_bp.route("/templates/<int:template_id>/steps/<int:step_id>/move/<direction>", methods=["POST"])
@edit_required
def move_template_step(template_id, step_id, direction):
    template = ProtocolTemplate.query.get_or_404(template_id)
    step = StepTemplate.query.get_or_404(step_id)

    if step.template_id != template.id:
        return redirect(url_for("views.template_detail", template_id=template.id))

    normalize_template_step_order(template)
    db.session.flush()

    if direction == "up":
        other = StepTemplate.query.filter(
            StepTemplate.template_id == template.id,
            StepTemplate.sort_order == step.sort_order - 1
        ).first()
    elif direction == "down":
        other = StepTemplate.query.filter(
            StepTemplate.template_id == template.id,
            StepTemplate.sort_order == step.sort_order + 1
        ).first()
    else:
        other = None

    if other:
        old_order = step.sort_order
        step.sort_order = other.sort_order
        other.sort_order = old_order

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id) + "#step-template-" + str(step.id))


@views_bp.route("/templates/<int:template_id>/start", methods=["POST"])
@edit_required
def start_job(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    job_name = request.form.get("job_name", "").strip()

    if not job_name:
        job_name = template.name + " Job_" + datetime.utcnow().strftime("%y%m%d")

    job = create_job_from_template(template, job_name)

    return redirect(url_for("views.job_detail", job_id=job.id))


@views_bp.route("/jobs")
def jobs_page():
    view = request.args.get("view", "active")

    if view == "pending":
        jobs = Job.query.filter(Job.status.in_(list(PENDING_JOB_STATUSES))).order_by(Job.started_at.desc()).all()
    elif view == "archive":
        jobs = Job.query.filter(Job.status.in_(list(ARCHIVE_JOB_STATUSES))).order_by(Job.started_at.desc()).all()
    else:
        view = "active"
        jobs = Job.query.filter(Job.status.in_(list(ACTIVE_JOB_STATUSES))).order_by(Job.started_at.desc()).all()

    return render_template("jobs_list.html", jobs=jobs, view=view)


@views_bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = Job.query.get_or_404(job_id)
    edit_note_raw = request.args.get("edit_note", "").strip()
    edit_note_id = int(edit_note_raw) if edit_note_raw.isdigit() else None

    timeline = make_job_timeline(job)
    images = list_job_images(job.id)
    selected_step_id = get_default_step_id(job, edit_note_id)
    general_note = get_general_job_note(job)

    step_notes = {}
    step_timing = {}

    for step in job.steps:
        status = calculate_step_status(step)
        step_notes[step.id] = get_step_notes(step, edit_note_id)
        step_timing[step.id] = {
            "status": status,
            "status_label": status_label(status),
            "compact_label": step_compact_time_label(step, status),
            "compact_title": step_compact_time_title(step, status),
            "box": step_timing_box(step)
        }

    return render_template(
        "job_detail.html",
        job=job,
        timeline=timeline,
        images=images,
        selected_step_id=selected_step_id,
        step_notes=step_notes,
        step_timing=step_timing,
        general_note=general_note,
        edit_note_id=edit_note_id,
        now_value=datetime_local_value(datetime.utcnow()),
        now_display=display_short_datetime(datetime.utcnow())
    )


@views_bp.route("/jobs/<int:job_id>/status", methods=["POST"])
@edit_required
def update_job_status(job_id):
    job = Job.query.get_or_404(job_id)
    new_status = request.form.get("status", "").strip().lower()

    allowed = ["active", "pending", "complete", "cancelled", "failed"]

    if new_status in allowed:
        job.status = new_status

        if new_status in ["pending", "complete", "cancelled", "failed"]:
            job.completed_at = datetime.utcnow()
        else:
            job.completed_at = None

        db.session.commit()

    return redirect(url_for("views.job_detail", job_id=job.id))


@views_bp.route("/jobs/<int:job_id>/notes/new", methods=["POST"])
@edit_required
def add_job_note(job_id):
    job = Job.query.get_or_404(job_id)
    note_html = request.form.get("note_html", "").strip()
    event_at = parse_datetime_local(request.form.get("event_at"))
    preview_image_url = request.form.get("preview_image_url", "").strip()

    note = get_general_job_note(job)

    if not note:
        note = JobNote(
            job_id=job.id,
            job_step_id=None,
            note_type="job_note",
            created_at=datetime.utcnow()
        )
        db.session.add(note)

    note.note_type = "job_note"
    note.title = request.form.get("title", "").strip()
    note.note_html = note_html
    note.event_at = event_at
    note.updated_at = datetime.utcnow()
    note.preview_image_url = preview_image_url

    if not note.preview_image_url:
        images = extract_image_urls(note.note_html)

        if images:
            note.preview_image_url = images[0]

    db.session.commit()

    return redirect(url_for("views.job_detail", job_id=job.id))




def next_step_note_title(step, note_type):
    notes = JobNote.query.filter_by(
        job_id=step.job_id,
        job_step_id=step.id
    ).all()

    if note_type == "completion_note":
        count = 0

        for note in notes:
            if note.note_type == "completion_note":
                count += 1

        return "Completion Note " + str(count + 1)

    count = 0

    for note in notes:
        if note.note_type != "completion_note":
            count += 1

    return "Note " + str(count + 1)


@views_bp.route("/steps/<int:step_id>/notes/new", methods=["POST"])
@edit_required
def add_step_note(step_id):
    step = JobStep.query.get_or_404(step_id)
    note_html = request.form.get("note_html", "").strip()
    event_at = parse_datetime_local(request.form.get("event_at"))
    preview_image_url = request.form.get("preview_image_url", "").strip()
    title = request.form.get("title", "").strip()

    if not title:
        title = next_step_note_title(step, "note")

    note = JobNote(
        job_id=step.job_id,
        job_step_id=step.id,
        note_type="note",
        title=title,
        note_html=note_html,
        created_at=datetime.utcnow(),
        event_at=event_at,
        updated_at=datetime.utcnow(),
        preview_image_url=preview_image_url
    )

    if not note.preview_image_url:
        images = extract_image_urls(note.note_html)

        if images:
            note.preview_image_url = images[0]

    db.session.add(note)
    db.session.commit()

    return redirect(url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id))


@views_bp.route("/notes/<int:note_id>/inline", methods=["POST"])
@edit_required
def inline_update_note(note_id):
    note = JobNote.query.get_or_404(note_id)

    if note.note_type == "job_note" or note.job_step_id is None:
        return redirect(url_for("views.edit_note", note_id=note.id))

    note.title = request.form.get("title", "").strip()
    note.note_html = request.form.get("note_html", "").strip()
    note.event_at = parse_datetime_local(request.form.get("event_at"))
    note.preview_image_url = request.form.get("preview_image_url", "").strip()
    note.updated_at = datetime.utcnow()

    if not note.preview_image_url:
        images = extract_image_urls(note.note_html)

        if images:
            note.preview_image_url = images[0]

    job_id = note.job_id
    step_id = note.job_step_id

    db.session.commit()

    return redirect(url_for("views.job_detail", job_id=job_id) + "#step-" + str(step_id))


@views_bp.route("/notes/<int:note_id>/time", methods=["POST"])
@edit_required
def inline_update_note_time(note_id):
    note = JobNote.query.get_or_404(note_id)

    if note.note_type == "job_note" or note.job_step_id is None:
        return jsonify({
            "ok": False,
            "message": "General job note time is edited with the general note form."
        }), 400

    note.event_at = parse_datetime_local(request.form.get("event_at"))
    note.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify({
        "ok": True,
        "display": display_short_datetime(note.event_at),
        "value": datetime_local_value(note.event_at)
    })



@views_bp.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
def edit_note(note_id):
    note = JobNote.query.get_or_404(note_id)

    if not can_edit():
        return redirect(url_for("views.job_detail", job_id=note.job_id))

    if request.method == "POST":
        note.title = request.form.get("title", "").strip()
        note.note_html = request.form.get("note_html", "").strip()
        note.event_at = parse_datetime_local(request.form.get("event_at"))
        note.preview_image_url = request.form.get("preview_image_url", "").strip()
        note.updated_at = datetime.utcnow()

        if not note.preview_image_url:
            images = extract_image_urls(note.note_html)

            if images:
                note.preview_image_url = images[0]

        db.session.commit()

        return redirect(url_for("views.job_detail", job_id=note.job_id))

    job = Job.query.get_or_404(note.job_id)
    note_images = extract_image_urls(note.note_html)

    return render_template(
        "note_edit.html",
        note=note,
        job=job,
        note_images=note_images,
        event_at_value=datetime_local_value(note.event_at or note.created_at),
        event_at_display=display_short_datetime(note.event_at or note.created_at),
        images=list_job_images(job.id)
    )


@views_bp.route("/notes/<int:note_id>/delete", methods=["POST"])
@edit_required
def delete_note(note_id):
    note = JobNote.query.get_or_404(note_id)
    job_id = note.job_id
    step_id = note.job_step_id

    if note.note_type == "job_note" or note.job_step_id is None:
        return redirect(url_for("views.edit_note", note_id=note.id))

    db.session.delete(note)
    db.session.commit()

    if step_id:
        return redirect(url_for("views.job_detail", job_id=job_id) + "#step-" + str(step_id))

    return redirect(url_for("views.job_detail", job_id=job_id))


@views_bp.route("/steps/<int:step_id>/complete", methods=["POST"])
@edit_required
def complete_step(step_id):
    step = JobStep.query.get_or_404(step_id)

    step.completed_at = datetime.utcnow()

    note_html = request.form.get("note_html", "").strip()
    note_title = request.form.get("title", "").strip()
    event_at = parse_datetime_local(request.form.get("event_at"))
    preview_image_url = request.form.get("preview_image_url", "").strip()

    if note_html or note_title:
        if not note_title:
            note_title = next_step_note_title(step, "completion_note")

        note = JobNote(
            job_id=step.job_id,
            job_step_id=step.id,
            note_type="completion_note",
            title=note_title,
            note_html=note_html,
            created_at=datetime.utcnow(),
            event_at=event_at,
            updated_at=datetime.utcnow(),
            preview_image_url=preview_image_url
        )

        if not note.preview_image_url:
            images = extract_image_urls(note.note_html)

            if images:
                note.preview_image_url = images[0]

        db.session.add(note)

    update_job_status_if_all_steps_done(step.job)

    db.session.commit()

    return redirect(url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id))


@views_bp.route("/steps/<int:step_id>/uncomplete", methods=["POST"])
@edit_required
def uncomplete_step(step_id):
    step = JobStep.query.get_or_404(step_id)

    step.completed_at = None

    if step.job.status == "pending":
        step.job.status = "active"
        step.job.completed_at = None

    db.session.commit()

    return redirect(url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id))


@views_bp.route("/uploads/jobs/<int:job_id>/image", methods=["POST"])
@edit_required
def upload_job_image(job_id):
    job = Job.query.get_or_404(job_id)

    upload = None

    if "file" in request.files:
        upload = request.files["file"]

    if upload is None and "image" in request.files:
        upload = request.files["image"]

    if upload is None:
        return jsonify({
            "error": "No file uploaded."
        }), 400

    original_filename = secure_filename(upload.filename or "")

    if not original_filename:
        return jsonify({
            "error": "Missing filename."
        }), 400

    suffix = Path(original_filename).suffix.lower()

    if suffix not in IMAGE_EXTENSIONS:
        return jsonify({
            "error": "Unsupported image type."
        }), 400

    upload_dir = current_app.config["UPLOAD_DIR"] / "jobs" / str(job.id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = datetime.utcnow().strftime("%Y%m%d_%H%M%S_") + uuid4().hex + suffix
    storage_path = upload_dir / stored_filename

    upload.save(storage_path)

    relative_url = "/static/uploads/jobs/" + str(job.id) + "/" + stored_filename

    return jsonify({
        "location": relative_url
    })



@views_bp.route("/jobs/<int:job_id>/delete", methods=["POST"])
@edit_required
def delete_job(job_id):
    job = Job.query.get_or_404(job_id)

    JobNote.query.filter_by(job_id=job.id).delete()
    JobStep.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()

    return redirect(url_for("views.jobs_page"))




def pdf_plain_text(value):
    return plain_text_from_html(value or "")


def report_image_paths_from_html(html):
    paths = []

    for url in extract_image_urls(html):
        if url.startswith("/static/"):
            relative_path = url.replace("/static/", "", 1)
            image_path = Path(current_app.static_folder) / relative_path

            if image_path.exists():
                paths.append(str(image_path))

    return paths


@views_bp.route("/jobs/<int:job_id>/report.pdf")
def job_report_pdf(job_id):
    job = Job.query.get_or_404(job_id)
    timeline = make_job_timeline(job)

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocProtocol, Paragraph, Spacer, PageBreak, Image, Table, TableStyle
    except Exception:
        return "ReportLab is required. Install it with: py -m pip install reportlab", 500

    buffer = BytesIO()

    doc = SimpleDocProtocol(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="PF67Title",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        spaceAfter=12
    ))
    styles.add(ParagraphStyle(
        name="PF67Small",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#555555")
    ))
    styles.add(ParagraphStyle(
        name="PF67EventTitle",
        parent=styles["Heading3"],
        fontSize=12,
        leading=14,
        spaceBefore=8,
        spaceAfter=4
    ))

    story = []

    story.append(Paragraph("PF67 Job Report", styles["PF67Title"]))
    story.append(Paragraph(job.name, styles["Heading1"]))

    meta_rows = [
        ["Status", job.status.title()],
        ["Protocol Version", str(job.template_version)],
        ["Started", display_exact_datetime(job.started_at)],
        ["Completed", display_exact_datetime(job.completed_at) if job.completed_at else ""],
        ["Generated", display_exact_datetime(datetime.utcnow())]
    ]

    meta_table = Table(meta_rows, colWidths=[1.45 * inch, 5.2 * inch])
    meta_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 5)
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Index", styles["Heading2"]))

    index_rows = [["#", "Date", "Event"]]

    for idx, item in enumerate(timeline, start=1):
        event_time = item.get("time")
        index_rows.append([
            str(idx),
            display_short_datetime(event_time),
            item.get("title", "")
        ])

    index_table = Table(index_rows, colWidths=[0.35 * inch, 1.35 * inch, 4.95 * inch])
    index_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    story.append(index_table)
    story.append(PageBreak())

    story.append(Paragraph("Chronological Project Story", styles["Heading2"]))

    for idx, item in enumerate(timeline, start=1):
        event_time = item.get("time")
        title = item.get("title") or "Event"
        kind = item.get("kind_label") or item.get("kind") or ""

        story.append(Paragraph(str(idx) + ". " + title, styles["PF67EventTitle"]))
        story.append(Paragraph(display_exact_datetime(event_time) + " - " + kind, styles["PF67Small"]))

        step = item.get("step")

        if step:
            story.append(Paragraph("Step: " + step.name, styles["PF67Small"]))

            if step.context_tag:
                story.append(Paragraph("Context: " + step.context_tag, styles["PF67Small"]))

        body_text = pdf_plain_text(item.get("body_html") or "")

        if body_text:
            story.append(Spacer(1, 0.06 * inch))
            story.append(Paragraph(body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles["Normal"]))

        note = item.get("note")

        if note:
            for image_path in report_image_paths_from_html(note.note_html):
                try:
                    image = Image(image_path)
                    image._restrictSize(5.8 * inch, 3.8 * inch)
                    story.append(Spacer(1, 0.1 * inch))
                    story.append(image)
                except Exception:
                    pass

        story.append(Spacer(1, 0.18 * inch))

    def page_number(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(7.85 * inch, 0.35 * inch, "Page " + str(doc_obj.page))
        canvas.drawString(0.65 * inch, 0.35 * inch, "PF67 - " + job.name[:60])
        canvas.restoreState()

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)

    buffer.seek(0)

    filename = "pf67_job_" + str(job.id) + "_report.pdf"

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )



@views_bp.route("/calendar-view")
def calendar_view():
    jobs = all_calendar_jobs()

    job_rows = []

    for job in jobs:
        job_rows.append({
            "job": job,
            "color_class": job_color_class(job)
        })

    return render_template("calendar_view.html", jobs=jobs, job_rows=job_rows)


@views_bp.route("/api/calendar/events")
def api_calendar_events():
    job_ids_text = request.args.get("job_ids", "").strip()
    include_completed = request.args.get("include_completed", "1").strip() != "0"
    job_ids = []

    if job_ids_text:
        for part in job_ids_text.split(","):
            part = part.strip()

            if part.isdigit():
                job_ids.append(int(part))

    return jsonify(calendar_events_for_jobs(job_ids, include_completed=include_completed))

# === PF67 REV18 STABILIZATION OVERRIDES ===

def next_step_note_title(step, note_type):
    notes = JobNote.query.filter_by(
        job_id=step.job_id,
        job_step_id=step.id
    ).all()

    if note_type == "completion_note":
        count = 0

        for note in notes:
            if note.note_type == "completion_note":
                count += 1

        return "Completion Note " + str(count + 1)

    count = 0

    for note in notes:
        if note.note_type != "completion_note":
            count += 1

    return "Note " + str(count + 1)


def display_note_title(note, fallback_number):
    title = (note.title or "").strip()

    if title:
        return title

    if note.note_type == "completion_note":
        return "Completion Note " + str(fallback_number)

    return "Note " + str(fallback_number)


def calendar_status_prefix(status, completed=False):
    if completed:
        return "✓ "

    labels = {
        "waiting": "[Wait] ",
        "checkable": "[Check] ",
        "ideal": "[Ideal] ",
        "due": "[Due] ",
        "late": "[Late] ",
        "risky": "[Risk] ",
        "critical": "[Fail] "
    }

    return labels.get(status, "")


def pdf_plain_text(value):
    return plain_text_from_html(value or "")


def report_image_paths_from_html(html):
    paths = []

    for url in extract_image_urls(html):
        if url.startswith("/static/"):
            relative_path = url.replace("/static/", "", 1)
            image_path = Path(current_app.static_folder) / relative_path

            if image_path.exists():
                paths.append(str(image_path))

    return paths


def step_timing_box(step):
    status = calculate_step_status(step)

    if step.completed_at:
        return {
            "status": "Completed",
            "rows": [
                {
                    "label": "Completed",
                    "display": human_day_label(step.completed_at),
                    "title": display_exact_datetime(step.completed_at),
                    "class": "pf67-time-completed"
                }
            ]
        }

    boundaries = step_boundary_times(step)

    rows = []

    labels = [
        ("Check", boundaries["ready"], "pf67-time-ready"),
        ("Ideal", boundaries["ideal"], "pf67-time-ideal"),
        ("Limit", boundaries["limit"], "pf67-time-limit"),
        ("Risk", boundaries["risk"], "pf67-time-risk"),
        ("Failure", boundaries["failure"], "pf67-time-failure")
    ]

    for label, value, class_name in labels:
        if value is not None:
            rows.append({
                "label": label,
                "display": human_day_label(value),
                "title": display_exact_datetime(value),
                "class": class_name
            })

    return {
        "status": status_label(status),
        "rows": rows
    }


def get_step_notes(step, edit_note_id=None):
    notes = []
    rows = JobNote.query.filter_by(
        job_id=step.job_id,
        job_step_id=step.id
    ).order_by(JobNote.event_at.asc(), JobNote.created_at.asc(), JobNote.id.asc()).all()

    fallback_number = 1

    for note in rows:
        event_time = note.event_at or note.created_at

        notes.append({
            "note": note,
            "event_at": event_time,
            "event_at_value": datetime_local_value(event_time),
            "event_at_display": display_short_datetime(event_time),
            "title": display_note_title(note, fallback_number),
            "body_html": note.note_html,
            "preview_image_url": auto_preview_image(note),
            "is_editing": edit_note_id is not None and note.id == edit_note_id,
            "edit_url": url_for("views.job_detail", job_id=step.job_id) + "?edit_note=" + str(note.id) + "#step-" + str(step.id)
        })

        fallback_number += 1

    return notes


def build_upcoming_tasks():
    rows = []

    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    for step in steps:
        status = calculate_step_status(step)

        if status == "waiting":
            ready_at = next_ready_time(step)
            minutes = minutes_until(ready_at)

            rows.append({
                "title": step.name,
                "job_name": step.job.name,
                "context": step.context_tag or "",
                "ready_at": ready_at,
                "time_label": step_compact_time_label(step, status) or "Later",
                "time_title": step_compact_time_title(step, status) or "",
                "url": url_for("views.job_detail", job_id=step.job_id) + "#step-" + str(step.id),
                "ready_in_display": human_minutes(minutes) if minutes is not None and minutes > 0 else "soon",
                "step_id": step.id,
                "job_id": step.job_id,
                "status": status
            })

    rows.sort(key=lambda item: item["ready_at"] or datetime.max)

    return rows


def build_upcoming_board():
    now = datetime.utcnow()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    horizon_start = today + timedelta(days=7)

    board = {
        "today": {
            "title": "Today",
            "items": []
        },
        "tomorrow": {
            "title": "Tomorrow",
            "items": []
        },
        "next_five": {
            "title": "Next 5 Days",
            "items": []
        },
        "horizon": {
            "title": "Horizon",
            "items": []
        }
    }

    for item in build_upcoming_tasks():
        ready_at = item.get("ready_at")

        if ready_at is None:
            board["horizon"]["items"].append(item)
            continue

        ready_date = ready_at.date()

        if ready_date == today:
            board["today"]["items"].append(item)
        elif ready_date == tomorrow:
            board["tomorrow"]["items"].append(item)
        elif ready_date < horizon_start:
            board["next_five"]["items"].append(item)
        else:
            board["horizon"]["items"].append(item)

    visible_columns = []

    for key in ["today", "tomorrow", "next_five", "horizon"]:
        if board[key]["items"]:
            visible_columns.append(board[key])

    return visible_columns


def upcoming_board_fingerprint():
    rows = []

    for item in build_upcoming_tasks():
        ready_at = item.get("ready_at")

        rows.append({
            "step_id": item.get("step_id"),
            "job_id": item.get("job_id"),
            "title": item.get("title"),
            "job_name": item.get("job_name"),
            "ready_at": ready_at.isoformat() if ready_at else "",
            "status": item.get("status")
        })

    rows.sort(key=lambda row: (row["ready_at"], row["job_id"], row["step_id"]))

    payload = json.dumps(rows, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calendar_events_for_jobs(job_ids, include_completed=True):
    events = []

    if not job_ids:
        return events

    jobs = Job.query.filter(Job.id.in_(job_ids)).all()

    for job in jobs:
        if job.status not in ["active", "pending"]:
            continue

        for step in job.steps:
            status = calculate_step_status(step)

            if step.completed_at:
                if not include_completed:
                    continue

                event_time = step.completed_at
                event_title = calendar_status_prefix(status, completed=True) + step.name
                color_class = "completed"
            else:
                event_time = useful_step_time(step, status)
                event_title = calendar_status_prefix(status) + step.name
                color_class = status

            if not event_time:
                continue

            events.append({
                "id": "step-" + str(step.id),
                "title": event_title,
                "start": event_time.isoformat(),
                "url": url_for("views.job_detail", job_id=job.id) + "#step-" + str(step.id),
                "className": [job_color_class(job), "pf67-cal-status-" + color_class],
                "extendedProps": {
                    "job_id": job.id,
                    "job_name": job.name,
                    "step_id": step.id,
                    "status": status,
                    "context": step.context_tag or ""
                }
            })

    return events

# === PF67 REV19 CALENDAR OVERRIDE ===

def calendar_timing_tooltip(step):
    lines = []

    boundaries = step_boundary_times(step)

    label_map = [
        ("Check", boundaries["ready"]),
        ("Ideal", boundaries["ideal"]),
        ("Limit", boundaries["limit"]),
        ("Risk", boundaries["risk"]),
        ("Failure", boundaries["failure"])
    ]

    for label, value in label_map:
        if value is not None:
            lines.append(label + ": " + human_day_label(value) + " (" + display_exact_datetime(value) + ")")

    return lines


def calendar_events_for_jobs(job_ids, include_completed=True):
    events = []

    if not job_ids:
        return events

    jobs = Job.query.filter(Job.id.in_(job_ids)).all()

    for job in jobs:
        if job.status not in ["active", "pending"]:
            continue

        for step in job.steps:
            status = calculate_step_status(step)

            if step.completed_at:
                if not include_completed:
                    continue

                event_time = step.completed_at
                event_title = calendar_status_prefix(status, completed=True) + step.name
                color_class = "completed"
            else:
                event_time = useful_step_time(step, status)
                event_title = calendar_status_prefix(status) + step.name
                color_class = status

            if not event_time:
                continue

            tooltip_lines = [
                step.name,
                "Job: " + job.name
            ]

            if step.context_tag:
                tooltip_lines.append("Context: " + step.context_tag)

            timing_lines = calendar_timing_tooltip(step)

            if timing_lines:
                tooltip_lines.append("")
                tooltip_lines.extend(timing_lines)

            events.append({
                "id": "step-" + str(step.id),
                "title": event_title,
                "start": event_time.isoformat(),
                "url": url_for("views.job_detail", job_id=job.id) + "#step-" + str(step.id),
                "className": [job_color_class(job), "pf67-cal-status-" + color_class],
                "extendedProps": {
                    "job_id": job.id,
                    "job_name": job.name,
                    "step_id": step.id,
                    "status": status,
                    "context": step.context_tag or "",
                    "tooltip": "\\n".join(tooltip_lines)
                }
            })

    return events

# === PF67 REV22 BACKLOG ===

BACKLOG_TYPES = ["Bug", "New", "Improve", "Question"]
BACKLOG_PRIORITIES = ["None", "Low", "Medium", "High", "Critical"]
BACKLOG_AREAS = ["Calendar", "Jobs", "Notes", "Protocols", "PDF", "API", "Dev", "UI", "System", "Database"]
BACKLOG_STATUSES = ["New Addition", "Accepted", "In Progress", "Testing", "Done", "Deferred", "Rejected"]


def backlog_db_path():
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")

    if uri.startswith("sqlite:///"):
        return uri.replace("sqlite:///", "", 1)

    return str(current_app.config.get("DB_DIR", current_app.config["BASE_DIR"] / "db") / "pf67.sqlite3")


def backlog_connect():
    connection = sqlite3.connect(backlog_db_path())
    connection.row_factory = sqlite3.Row
    return connection


def backlog_now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def ensure_backlog_schema():
    connection = backlog_connect()

    try:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backlog_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pin INTEGER NOT NULL DEFAULT 0,
                type TEXT NOT NULL DEFAULT 'Improve',
                priority TEXT NOT NULL DEFAULT 'Medium',
                area TEXT NOT NULL DEFAULT 'UI',
                status TEXT NOT NULL DEFAULT 'New Addition',
                note TEXT NOT NULL DEFAULT '',
                ai_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                resolved_in_rev TEXT NOT NULL DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backlog_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO backlog_settings (key, value)
            VALUES ('ai_write_enabled', '0')
        """)

        connection.commit()
    finally:
        connection.close()


def backlog_setting(key, default_value=""):
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        row = connection.execute(
            "SELECT value FROM backlog_settings WHERE key = ?",
            (key,)
        ).fetchone()

        if not row:
            return default_value

        return row["value"]
    finally:
        connection.close()


def backlog_ai_write_enabled():
    return backlog_setting("ai_write_enabled", "0") == "1"


def set_backlog_setting(key, value):
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        cursor = connection.execute(
            "UPDATE backlog_settings SET value = ? WHERE key = ?",
            (value, key)
        )

        if cursor.rowcount == 0:
            connection.execute(
                "INSERT INTO backlog_settings (key, value) VALUES (?, ?)",
                (key, value)
            )

        connection.commit()
    finally:
        connection.close()


def normalize_backlog_value(field, value):
    value = (value or "").strip()

    if field == "type":
        return value if value in BACKLOG_TYPES else "Improve"

    if field == "priority":
        return value if value in BACKLOG_PRIORITIES else "Medium"

    if field == "area":
        return value if value in BACKLOG_AREAS else "UI"

    if field == "status":
        return value if value in BACKLOG_STATUSES else "New Addition"

    if field == "pin":
        return "1" if str(value).lower() in ["1", "true", "yes", "on"] else "0"

    return value


def backlog_rows(include_done=True):
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        where = ""

        if not include_done:
            where = "WHERE status NOT IN ('Done', 'Rejected')"

        rows = connection.execute(
            """
            SELECT *
            FROM backlog_items
            """ + where + """
            ORDER BY
                pin DESC,
                CASE priority
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Medium' THEN 3
                    WHEN 'Low' THEN 4
                    ELSE 5
                END,
                datetime(modified_at) DESC,
                datetime(created_at) DESC,
                id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        connection.close()


def backlog_item(item_id):
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        row = connection.execute(
            "SELECT * FROM backlog_items WHERE id = ?",
            (item_id,)
        ).fetchone()

        if not row:
            return None

        return dict(row)
    finally:
        connection.close()


def backlog_item_to_api(row):
    return {
        "id": row["id"],
        "pin": bool(row["pin"]),
        "type": row["type"],
        "priority": row["priority"],
        "area": row["area"],
        "status": row["status"],
        "note": row["note"],
        "ai_summary": row["ai_summary"],
        "created_at": row["created_at"],
        "modified_at": row["modified_at"],
        "resolved_in_rev": row["resolved_in_rev"]
    }


def update_backlog_item(item_id, field_values):
    allowed = {
        "pin",
        "type",
        "priority",
        "area",
        "status",
        "note",
        "ai_summary",
        "resolved_in_rev"
    }

    updates = []
    params = []

    for field, value in field_values.items():
        if field not in allowed:
            continue

        updates.append(field + " = ?")
        params.append(normalize_backlog_value(field, value))

    if not updates:
        return

    updates.append("modified_at = ?")
    params.append(backlog_now())
    params.append(item_id)

    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        connection.execute(
            "UPDATE backlog_items SET " + ", ".join(updates) + " WHERE id = ?",
            params
        )
        connection.commit()
    finally:
        connection.close()


def create_backlog_item(field_values):
    now = backlog_now()

    item_type = normalize_backlog_value("type", field_values.get("type", "Improve"))
    priority = normalize_backlog_value("priority", field_values.get("priority", "Medium"))
    area = normalize_backlog_value("area", field_values.get("area", "UI"))
    status = normalize_backlog_value("status", field_values.get("status", "New Addition"))
    note = (field_values.get("note") or "").strip()
    ai_summary = (field_values.get("ai_summary") or "").strip()
    pin = int(normalize_backlog_value("pin", field_values.get("pin", "0")))
    resolved_in_rev = (field_values.get("resolved_in_rev") or "").strip()

    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        cursor = connection.execute(
            """
            INSERT INTO backlog_items (
                pin,
                type,
                priority,
                area,
                status,
                note,
                ai_summary,
                created_at,
                modified_at,
                resolved_in_rev
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pin,
                item_type,
                priority,
                area,
                status,
                note,
                ai_summary,
                now,
                now,
                resolved_in_rev
            )
        )
        connection.commit()

        return cursor.lastrowid
    finally:
        connection.close()


def backlog_brief_text():
    rows = backlog_rows(include_done=False)

    lines = []
    lines.append("PF67 Backlog Brief")
    lines.append("Generated: " + display_exact_datetime(datetime.utcnow()))
    lines.append("")

    if not rows:
        lines.append("No open backlog items.")
        return "\n".join(lines)

    current_type = None

    for row in rows:
        if row["type"] != current_type:
            current_type = row["type"]
            lines.append("")
            lines.append(current_type + ":")

        summary = row["ai_summary"] or row["note"]

        if len(summary) > 220:
            summary = summary[:217].rstrip() + "..."

        prefix = "- "
        if row["pin"]:
            prefix = "- [PIN] "

        lines.append(
            prefix
            + "[" + row["priority"] + "]"
            + "[" + row["area"] + "] "
            + summary
            + " (Status: " + row["status"] + ", ID: " + str(row["id"]) + ")"
        )

    return "\n".join(lines).strip()


@views_bp.route("/backlog")
@edit_required
def backlog_page():
    ensure_backlog_schema()

    return render_template(
        "backlog.html",
        backlog_items=backlog_rows(include_done=True),
        backlog_types=BACKLOG_TYPES,
        backlog_priorities=BACKLOG_PRIORITIES,
        backlog_areas=BACKLOG_AREAS,
        backlog_statuses=BACKLOG_STATUSES,
        ai_write_enabled=backlog_ai_write_enabled()
    )


@views_bp.route("/backlog/add", methods=["POST"])
@edit_required
def backlog_add():
    create_backlog_item({
        "pin": request.form.get("pin", "0"),
        "type": request.form.get("type", "Improve"),
        "priority": request.form.get("priority", "Medium"),
        "area": request.form.get("area", "UI"),
        "status": request.form.get("status", "New Addition"),
        "note": request.form.get("note", ""),
        "ai_summary": request.form.get("ai_summary", ""),
        "resolved_in_rev": request.form.get("resolved_in_rev", "")
    })

    return redirect(url_for("views.backlog_page"))


@views_bp.route("/backlog/item/<int:item_id>/update", methods=["POST"])
@edit_required
def backlog_update(item_id):
    fields = {}

    for field in [
        "pin",
        "type",
        "priority",
        "area",
        "status",
        "note",
        "ai_summary",
        "resolved_in_rev"
    ]:
        if field in request.form:
            fields[field] = request.form.get(field, "")

    update_backlog_item(item_id, fields)

    return redirect(url_for("views.backlog_page") + "#backlog-item-" + str(item_id))


@views_bp.route("/backlog/item/<int:item_id>/delete", methods=["POST"])
@edit_required
def backlog_delete(item_id):
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        connection.execute(
            "DELETE FROM backlog_items WHERE id = ?",
            (item_id,)
        )
        connection.commit()
    finally:
        connection.close()

    return redirect(url_for("views.backlog_page"))


@views_bp.route("/backlog/settings/ai-write", methods=["POST"])
@edit_required
def backlog_ai_write_setting():
    enabled = "1" if request.form.get("ai_write_enabled") == "1" else "0"
    set_backlog_setting("ai_write_enabled", enabled)

    return redirect(url_for("views.backlog_page"))


@views_bp.route("/api/backlog")
def api_backlog():
    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "items": [backlog_item_to_api(row) for row in backlog_rows(include_done=True)]
    })


@views_bp.route("/api/backlog/open")
def api_backlog_open():
    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "items": [backlog_item_to_api(row) for row in backlog_rows(include_done=False)]
    })


@views_bp.route("/api/backlog/item/<int:item_id>")
def api_backlog_item(item_id):
    row = backlog_item(item_id)

    if not row:
        return jsonify({
            "error": "Not found"
        }), 404

    return jsonify(backlog_item_to_api(row))


@views_bp.route("/api/backlog/brief")
def api_backlog_brief():
    return current_app.response_class(
        backlog_brief_text(),
        mimetype="text/plain"
    )


@views_bp.route("/api/backlog/add", methods=["POST"])
def api_backlog_add():
    if not backlog_ai_write_enabled():
        return jsonify({
            "error": "AI write is disabled"
        }), 403

    payload = request.get_json(silent=True) or {}
    item_id = create_backlog_item(payload)

    row = backlog_item(item_id)

    return jsonify({
        "ok": True,
        "item": backlog_item_to_api(row)
    })


@views_bp.route("/api/backlog/item/<int:item_id>/update", methods=["POST"])
def api_backlog_update(item_id):
    if not backlog_ai_write_enabled():
        return jsonify({
            "error": "AI write is disabled"
        }), 403

    payload = request.get_json(silent=True) or {}
    update_backlog_item(item_id, payload)

    row = backlog_item(item_id)

    if not row:
        return jsonify({
            "error": "Not found"
        }), 404

    return jsonify({
        "ok": True,
        "item": backlog_item_to_api(row)
    })

# === PF67 REV23 AI SUMMARY GET WRITER ===

@views_bp.route("/api/ai/backlog/summary")
def api_ai_backlog_summary_get():
    """
    Narrow AI-write endpoint.

    This intentionally accepts GET because ChatGPT can reliably open GET URLs.
    It only writes the ai_summary field and only works when the Backlog AI Write
    switch is enabled.
    """
    if not backlog_ai_write_enabled():
        return jsonify({
            "ok": False,
            "error": "AI write is disabled"
        }), 403

    item_id_text = request.args.get("id", "").strip()
    summary = request.args.get("summary", "").strip()

    if not item_id_text.isdigit():
        return jsonify({
            "ok": False,
            "error": "Missing or invalid id"
        }), 400

    if not summary:
        return jsonify({
            "ok": False,
            "error": "Missing summary"
        }), 400

    if len(summary) > 500:
        return jsonify({
            "ok": False,
            "error": "Summary is too long. Maximum is 500 characters."
        }), 400

    item_id = int(item_id_text)
    row = backlog_item(item_id)

    if not row:
        return jsonify({
            "ok": False,
            "error": "Not found"
        }), 404

    update_backlog_item(item_id, {
        "ai_summary": summary
    })

    row = backlog_item(item_id)

    return jsonify({
        "ok": True,
        "message": "AI Summary updated",
        "item": backlog_item_to_api(row)
    })


@views_bp.route("/api/ai/backlog/write-status")
def api_ai_backlog_write_status():
    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "supported_get_writes": [
            {
                "field": "ai_summary",
                "endpoint": "/api/ai/backlog/summary",
                "args": ["id", "summary"],
                "max_summary_chars": 500
            }
        ]
    })

# === PF67 REV24 AI BACKLOG WORKBENCH ===

def safe_backlog_ai_write_enabled():
    try:
        return backlog_ai_write_enabled()
    except Exception:
        return False


@views_bp.app_context_processor
def pf67_global_context():
    return {
        "global_ai_write_enabled": safe_backlog_ai_write_enabled()
    }


def ensure_backlog_ai_audit_schema():
    ensure_backlog_schema()
    connection = backlog_connect()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS backlog_ai_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backlog_item_id INTEGER,
                action TEXT NOT NULL,
                field TEXT NOT NULL DEFAULT '',
                old_value TEXT NOT NULL DEFAULT '',
                new_value TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        connection.commit()
    finally:
        connection.close()


def backlog_ai_audit(item_id, action, field="", old_value="", new_value=""):
    ensure_backlog_ai_audit_schema()
    connection = backlog_connect()

    try:
        connection.execute(
            """
            INSERT INTO backlog_ai_audit (
                backlog_item_id,
                action,
                field,
                old_value,
                new_value,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                action,
                field,
                str(old_value or ""),
                str(new_value or ""),
                backlog_now()
            )
        )
        connection.commit()
    finally:
        connection.close()


def backlog_ai_update_field(item_id, field, value, action):
    if not backlog_ai_write_enabled():
        return {
            "ok": False,
            "status": 403,
            "error": "AI write is disabled"
        }

    allowed_fields = ["ai_summary", "status", "priority", "resolved_in_rev"]

    if field not in allowed_fields:
        return {
            "ok": False,
            "status": 400,
            "error": "Field is not AI-writable"
        }

    row = backlog_item(item_id)

    if not row:
        return {
            "ok": False,
            "status": 404,
            "error": "Not found"
        }

    old_value = row.get(field, "")
    update_backlog_item(item_id, {
        field: value
    })

    backlog_ai_audit(item_id, action, field, old_value, value)

    row = backlog_item(item_id)

    return {
        "ok": True,
        "status": 200,
        "item": backlog_item_to_api(row)
    }


def backlog_url_for(endpoint, **kwargs):
    return url_for(endpoint, _external=True, **kwargs)


def backlog_ai_action_urls(row):
    item_id = row["id"]

    return {
        "set_status_in_progress": backlog_url_for("views.api_ai_backlog_status_get", id=item_id, status="In Progress"),
        "set_status_testing": backlog_url_for("views.api_ai_backlog_status_get", id=item_id, status="Testing"),
        "set_status_done": backlog_url_for("views.api_ai_backlog_status_get", id=item_id, status="Done"),
        "set_status_deferred": backlog_url_for("views.api_ai_backlog_status_get", id=item_id, status="Deferred"),
        "set_priority_none": backlog_url_for("views.api_ai_backlog_priority_get", id=item_id, priority="None"),
        "set_priority_low": backlog_url_for("views.api_ai_backlog_priority_get", id=item_id, priority="Low"),
        "set_priority_medium": backlog_url_for("views.api_ai_backlog_priority_get", id=item_id, priority="Medium"),
        "set_priority_high": backlog_url_for("views.api_ai_backlog_priority_get", id=item_id, priority="High"),
        "set_priority_critical": backlog_url_for("views.api_ai_backlog_priority_get", id=item_id, priority="Critical"),
        "summarize_from_note": backlog_url_for("views.api_ai_backlog_command_get", id=item_id, action="summarize_from_note"),
        "acknowledge_test": backlog_url_for("views.api_ai_backlog_command_get", id=item_id, action="acknowledge_test"),
        "mark_done": backlog_url_for("views.api_ai_backlog_command_get", id=item_id, action="mark_done"),
        "custom_summary_endpoint": backlog_url_for("views.api_ai_backlog_summary_get", id=item_id, summary="__SUMMARY__"),
        "resolved_endpoint": backlog_url_for("views.api_ai_backlog_resolved_get", id=item_id, rev="__REV__")
    }


def backlog_ai_summary_from_note(row):
    note = (row.get("note") or "").strip()

    if not note:
        return "Backlog item requires review; original note is blank."

    summary = note

    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."

    return summary


@views_bp.route("/api/ai/backlog/workbench")
def api_ai_backlog_workbench():
    rows = backlog_rows(include_done=False)

    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "allowed_ai_write_fields": [
            "ai_summary",
            "status",
            "priority",
            "resolved_in_rev"
        ],
        "disallowed_ai_write_fields": [
            "pin",
            "note",
            "type",
            "area",
            "delete",
            "created_at",
            "modified_at"
        ],
        "items": [
            {
                "item": backlog_item_to_api(row),
                "needs_ai_summary": not bool((row.get("ai_summary") or "").strip()),
                "actions": backlog_ai_action_urls(row)
            }
            for row in rows
        ]
    })


@views_bp.route("/api/ai/backlog/status")
def api_ai_backlog_status_get():
    item_id_text = request.args.get("id", "").strip()
    status = request.args.get("status", "").strip()

    if not item_id_text.isdigit():
        return jsonify({
            "ok": False,
            "error": "Missing or invalid id"
        }), 400

    if status not in BACKLOG_STATUSES:
        return jsonify({
            "ok": False,
            "error": "Invalid status"
        }), 400

    result = backlog_ai_update_field(
        int(item_id_text),
        "status",
        status,
        "set_status"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Status updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/priority")
def api_ai_backlog_priority_get():
    item_id_text = request.args.get("id", "").strip()
    priority = request.args.get("priority", "").strip()

    if not item_id_text.isdigit():
        return jsonify({
            "ok": False,
            "error": "Missing or invalid id"
        }), 400

    if priority not in BACKLOG_PRIORITIES:
        return jsonify({
            "ok": False,
            "error": "Invalid priority"
        }), 400

    result = backlog_ai_update_field(
        int(item_id_text),
        "priority",
        priority,
        "set_priority"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Priority updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/resolved")
def api_ai_backlog_resolved_get():
    item_id_text = request.args.get("id", "").strip()
    rev = request.args.get("rev", "").strip()

    if not item_id_text.isdigit():
        return jsonify({
            "ok": False,
            "error": "Missing or invalid id"
        }), 400

    if len(rev) > 40:
        return jsonify({
            "ok": False,
            "error": "Resolved rev is too long"
        }), 400

    result = backlog_ai_update_field(
        int(item_id_text),
        "resolved_in_rev",
        rev,
        "set_resolved_in_rev"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Resolved revision updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/command")
def api_ai_backlog_command_get():
    item_id_text = request.args.get("id", "").strip()
    action = request.args.get("action", "").strip()

    if not item_id_text.isdigit():
        return jsonify({
            "ok": False,
            "error": "Missing or invalid id"
        }), 400

    item_id = int(item_id_text)
    row = backlog_item(item_id)

    if not row:
        return jsonify({
            "ok": False,
            "error": "Not found"
        }), 404

    if action == "summarize_from_note":
        summary = backlog_ai_summary_from_note(row)

        result = backlog_ai_update_field(
            item_id,
            "ai_summary",
            summary,
            "summarize_from_note"
        )

    elif action == "acknowledge_test":
        summary = "AI access confirmed: the assistant can read this Backlog item and update allowed fields when AI Write is enabled."

        result = backlog_ai_update_field(
            item_id,
            "ai_summary",
            summary,
            "acknowledge_test"
        )

    elif action == "mark_done":
        result = backlog_ai_update_field(
            item_id,
            "status",
            "Done",
            "mark_done"
        )

    else:
        return jsonify({
            "ok": False,
            "error": "Unsupported command"
        }), 400

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Command applied",
        "action": action,
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/audit")
def api_ai_backlog_audit():
    ensure_backlog_ai_audit_schema()
    connection = backlog_connect()

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM backlog_ai_audit
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 200
            """
        ).fetchall()

        return jsonify({
            "items": [dict(row) for row in rows]
        })
    finally:
        connection.close()

# === PF67 REV25 DEV UI / AI WORKBENCH URL OVERRIDE ===

def backlog_public_base_url():
    configured = current_app.config.get("PF67_PUBLIC_BASE_URL", "").strip()

    if configured:
        return configured.rstrip("/")

    host = request.host or "pf67.elx.dscloud.me"

    if "pf67.elx.dscloud.me" in host:
        return "https://pf67.elx.dscloud.me"

    # Prefer https for reverse-proxy use. Localhost remains http for direct LAN testing.
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return request.scheme + "://" + host

    return "https://" + host


def backlog_ai_url(path, args):
    from urllib.parse import urlencode

    return backlog_public_base_url() + path + "?" + urlencode(args)


def backlog_ai_action_urls(row):
    item_id = str(row["id"])

    return {
        "set_status_in_progress": backlog_ai_url("/api/ai/backlog/status", [("id", item_id), ("status", "In Progress")]),
        "set_status_testing": backlog_ai_url("/api/ai/backlog/status", [("id", item_id), ("status", "Testing")]),
        "set_status_done": backlog_ai_url("/api/ai/backlog/status", [("id", item_id), ("status", "Done")]),
        "set_status_deferred": backlog_ai_url("/api/ai/backlog/status", [("id", item_id), ("status", "Deferred")]),
        "set_priority_none": backlog_ai_url("/api/ai/backlog/priority", [("id", item_id), ("priority", "None")]),
        "set_priority_low": backlog_ai_url("/api/ai/backlog/priority", [("id", item_id), ("priority", "Low")]),
        "set_priority_medium": backlog_ai_url("/api/ai/backlog/priority", [("id", item_id), ("priority", "Medium")]),
        "set_priority_high": backlog_ai_url("/api/ai/backlog/priority", [("id", item_id), ("priority", "High")]),
        "set_priority_critical": backlog_ai_url("/api/ai/backlog/priority", [("id", item_id), ("priority", "Critical")]),
        "summarize_from_note": backlog_ai_url("/api/ai/backlog/command", [("id", item_id), ("action", "summarize_from_note")]),
        "acknowledge_test": backlog_ai_url("/api/ai/backlog/command", [("id", item_id), ("action", "acknowledge_test")]),
        "mark_done": backlog_ai_url("/api/ai/backlog/command", [("id", item_id), ("action", "mark_done")]),
        "custom_summary_endpoint": backlog_ai_url("/api/ai/backlog/summary", [("id", item_id), ("summary", "__SUMMARY__")]),
        "resolved_endpoint": backlog_ai_url("/api/ai/backlog/resolved", [("id", item_id), ("rev", "__REV__")])
    }


@views_bp.route("/dev")
@edit_required
def dev_page():
    return redirect(url_for("views.backlog_page"))

# === PF67 REV26 AI WORKBENCH CANONICAL URLS ===

def backlog_public_base_url_rev26():
    configured = current_app.config.get("PF67_PUBLIC_BASE_URL", "").strip()

    if configured:
        return configured.rstrip("/")

    host = request.host or "pf67.elx.dscloud.me"

    if "pf67.elx.dscloud.me" in host:
        return "https://pf67.elx.dscloud.me"

    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return request.scheme + "://" + host

    return "https://" + host


def backlog_ai_path_url(path):
    return backlog_public_base_url_rev26() + path


def backlog_status_from_slug(slug):
    values = {
        "new-addition": "New Addition",
        "accepted": "Accepted",
        "in-progress": "In Progress",
        "testing": "Testing",
        "done": "Done",
        "deferred": "Deferred",
        "rejected": "Rejected"
    }

    return values.get(slug)


def backlog_priority_from_slug(slug):
    values = {
        "none": "None",
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "critical": "Critical"
    }

    return values.get(slug)


def backlog_ai_action_urls(row):
    item_id = str(row["id"])

    return {
        "set_status_in_progress": backlog_ai_path_url("/api/ai/backlog/status/in-progress/" + item_id),
        "set_status_testing": backlog_ai_path_url("/api/ai/backlog/status/testing/" + item_id),
        "set_status_done": backlog_ai_path_url("/api/ai/backlog/status/done/" + item_id),
        "set_status_deferred": backlog_ai_path_url("/api/ai/backlog/status/deferred/" + item_id),
        "set_priority_none": backlog_ai_path_url("/api/ai/backlog/priority/none/" + item_id),
        "set_priority_low": backlog_ai_path_url("/api/ai/backlog/priority/low/" + item_id),
        "set_priority_medium": backlog_ai_path_url("/api/ai/backlog/priority/medium/" + item_id),
        "set_priority_high": backlog_ai_path_url("/api/ai/backlog/priority/high/" + item_id),
        "set_priority_critical": backlog_ai_path_url("/api/ai/backlog/priority/critical/" + item_id),
        "summarize_from_note": backlog_ai_path_url("/api/ai/backlog/command/summarize-from-note/" + item_id),
        "acknowledge_test": backlog_ai_path_url("/api/ai/backlog/command/acknowledge-test/" + item_id),
        "mark_done": backlog_ai_path_url("/api/ai/backlog/command/mark-done/" + item_id),
        "custom_summary_endpoint": backlog_ai_path_url("/api/ai/backlog/summary/" + item_id + "/__SUMMARY__"),
        "resolved_rev_26": backlog_ai_path_url("/api/ai/backlog/resolved/rev-26/" + item_id),
        "resolved_rev_27": backlog_ai_path_url("/api/ai/backlog/resolved/rev-27/" + item_id)
    }


@views_bp.route("/api/ai/backlog/command/<action_slug>/<int:item_id>")
def api_ai_backlog_command_path(action_slug, item_id):
    action_map = {
        "summarize-from-note": "summarize_from_note",
        "acknowledge-test": "acknowledge_test",
        "mark-done": "mark_done"
    }

    action = action_map.get(action_slug)

    if not action:
        return jsonify({
            "ok": False,
            "error": "Unsupported command"
        }), 400

    row = backlog_item(item_id)

    if not row:
        return jsonify({
            "ok": False,
            "error": "Not found"
        }), 404

    if action == "summarize_from_note":
        summary = backlog_ai_summary_from_note(row)

        result = backlog_ai_update_field(
            item_id,
            "ai_summary",
            summary,
            "summarize_from_note"
        )

    elif action == "acknowledge_test":
        summary = "AI access confirmed: the assistant can read this Dev item and update allowed fields when AI Write is enabled."

        result = backlog_ai_update_field(
            item_id,
            "ai_summary",
            summary,
            "acknowledge_test"
        )

    elif action == "mark_done":
        result = backlog_ai_update_field(
            item_id,
            "status",
            "Done",
            "mark_done"
        )

    else:
        return jsonify({
            "ok": False,
            "error": "Unsupported command"
        }), 400

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Command applied",
        "action": action,
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/status/<status_slug>/<int:item_id>")
def api_ai_backlog_status_path(status_slug, item_id):
    status = backlog_status_from_slug(status_slug)

    if not status:
        return jsonify({
            "ok": False,
            "error": "Invalid status"
        }), 400

    result = backlog_ai_update_field(
        item_id,
        "status",
        status,
        "set_status"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Status updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/priority/<priority_slug>/<int:item_id>")
def api_ai_backlog_priority_path(priority_slug, item_id):
    priority = backlog_priority_from_slug(priority_slug)

    if not priority:
        return jsonify({
            "ok": False,
            "error": "Invalid priority"
        }), 400

    result = backlog_ai_update_field(
        item_id,
        "priority",
        priority,
        "set_priority"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Priority updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/resolved/<rev_slug>/<int:item_id>")
def api_ai_backlog_resolved_path(rev_slug, item_id):
    rev_map = {
        "rev-26": "Rev 26",
        "rev-27": "Rev 27",
        "rev-28": "Rev 28",
        "rev-29": "Rev 29",
        "rev-30": "Rev 30"
    }

    rev = rev_map.get(rev_slug)

    if not rev:
        return jsonify({
            "ok": False,
            "error": "Invalid resolved revision"
        }), 400

    result = backlog_ai_update_field(
        item_id,
        "resolved_in_rev",
        rev,
        "set_resolved_in_rev"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Resolved revision updated",
        "item": result["item"]
    })


@views_bp.route("/api/ai/backlog/summary/<int:item_id>/<path:summary>")
def api_ai_backlog_summary_path(item_id, summary):
    from urllib.parse import unquote

    summary = unquote(summary or "").strip()

    if not summary:
        return jsonify({
            "ok": False,
            "error": "Missing summary"
        }), 400

    if len(summary) > 500:
        return jsonify({
            "ok": False,
            "error": "Summary is too long. Maximum is 500 characters."
        }), 400

    result = backlog_ai_update_field(
        item_id,
        "ai_summary",
        summary,
        "set_ai_summary"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "AI Summary updated",
        "item": result["item"]
    })

# === PF67 REV27 AI BATCH SUMMARY ===

def backlog_ai_compact_summary(row):
    item_type = (row.get("type") or "").strip()
    area = (row.get("area") or "").strip()
    note = (row.get("note") or "").strip()

    if not note:
        return "Dev item requires review; original note is blank."

    clean_note = " ".join(note.split())

    prefixes = {
        "Bug": "Bug report",
        "New": "New feature",
        "Improve": "Improvement request",
        "Question": "Question"
    }

    prefix = prefixes.get(item_type, "Dev item")

    if area:
        summary = prefix + " for " + area + ": " + clean_note
    else:
        summary = prefix + ": " + clean_note

    if len(summary) > 300:
        summary = summary[:297].rstrip() + "..."

    return summary


def backlog_missing_ai_summary_rows():
    rows = []

    for row in backlog_rows(include_done=False):
        ai_summary = (row.get("ai_summary") or "").strip()

        if not ai_summary:
            rows.append(row)

    return rows


@views_bp.route("/api/ai/backlog/command/summarize-all-missing")
def api_ai_backlog_summarize_all_missing():
    """
    Batch AI command.

    This intentionally uses GET because ChatGPT can reliably open exact GET URLs
    that the user provides. The endpoint is intentionally narrow:
    - Requires Backlog/Dev AI Write to be enabled.
    - Only updates blank ai_summary fields.
    - Only touches open items.
    - Does not modify Pin, Note, Type, Area, Delete, or dates directly.
    """
    if not backlog_ai_write_enabled():
        return jsonify({
            "ok": False,
            "error": "AI write is disabled"
        }), 403

    updated_items = []
    skipped_items = []

    for row in backlog_missing_ai_summary_rows():
        item_id = row["id"]
        summary = backlog_ai_compact_summary(row)

        result = backlog_ai_update_field(
            item_id,
            "ai_summary",
            summary,
            "summarize_all_missing"
        )

        if result["ok"]:
            updated_items.append(result["item"])
        else:
            skipped_items.append({
                "id": item_id,
                "error": result.get("error", "Unknown error")
            })

    return jsonify({
        "ok": True,
        "message": "Missing AI summaries processed",
        "updated_count": len(updated_items),
        "skipped_count": len(skipped_items),
        "updated_items": updated_items,
        "skipped_items": skipped_items
    })


@views_bp.route("/api/ai/backlog/command/preview-summarize-all-missing")
def api_ai_backlog_preview_summarize_all_missing():
    """
    Read-only preview of what summarize-all-missing would write.
    Useful for checking before running the batch command.
    """
    preview_items = []

    for row in backlog_missing_ai_summary_rows():
        preview_items.append({
            "id": row["id"],
            "type": row["type"],
            "priority": row["priority"],
            "area": row["area"],
            "status": row["status"],
            "note": row["note"],
            "proposed_ai_summary": backlog_ai_compact_summary(row)
        })

    return jsonify({
        "ok": True,
        "ai_write_enabled": backlog_ai_write_enabled(),
        "missing_summary_count": len(preview_items),
        "items": preview_items,
        "run_url": backlog_public_base_url_rev26() + "/api/ai/backlog/command/summarize-all-missing"
    })


# Override workbench one more time to expose batch command URLs clearly.
@views_bp.route("/api/ai/backlog/workbench-v3")
def api_ai_backlog_workbench_v3():
    rows = backlog_rows(include_done=False)

    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "allowed_ai_write_fields": [
            "ai_summary",
            "status",
            "priority",
            "resolved_in_rev"
        ],
        "disallowed_ai_write_fields": [
            "pin",
            "note",
            "type",
            "area",
            "delete",
            "created_at",
            "modified_at"
        ],
        "batch_actions": {
            "preview_summarize_all_missing": backlog_public_base_url_rev26() + "/api/ai/backlog/command/preview-summarize-all-missing",
            "summarize_all_missing": backlog_public_base_url_rev26() + "/api/ai/backlog/command/summarize-all-missing"
        },
        "items": [
            {
                "item": backlog_item_to_api(row),
                "needs_ai_summary": not bool((row.get("ai_summary") or "").strip()),
                "actions": backlog_ai_action_urls(row)
            }
            for row in rows
        ]
    })

# === PF67 REV29 AI STATUS BATCH ===

def backlog_parse_id_list(raw_value):
    ids = []

    for part in (raw_value or "").replace(";", ",").split(","):
        part = part.strip()

        if part.isdigit():
            ids.append(int(part))

    unique_ids = []
    seen = set()

    for item_id in ids:
        if item_id not in seen:
            seen.add(item_id)
            unique_ids.append(item_id)

    return unique_ids


def backlog_ai_batch_update_status(item_ids, status, action):
    if not backlog_ai_write_enabled():
        return {
            "ok": False,
            "status": 403,
            "error": "AI write is disabled"
        }

    if status not in BACKLOG_STATUSES:
        return {
            "ok": False,
            "status": 400,
            "error": "Invalid status"
        }

    updated_items = []
    skipped_items = []

    for item_id in item_ids:
        row = backlog_item(item_id)

        if not row:
            skipped_items.append({
                "id": item_id,
                "error": "Not found"
            })
            continue

        result = backlog_ai_update_field(
            item_id,
            "status",
            status,
            action
        )

        if result["ok"]:
            updated_items.append(result["item"])
        else:
            skipped_items.append({
                "id": item_id,
                "error": result.get("error", "Unknown error")
            })

    return {
        "ok": True,
        "status": 200,
        "updated_count": len(updated_items),
        "skipped_count": len(skipped_items),
        "updated_items": updated_items,
        "skipped_items": skipped_items
    }


@views_bp.route("/api/ai/backlog/status/testing/items/<path:item_ids_text>")
def api_ai_backlog_status_testing_items(item_ids_text):
    """
    Batch status endpoint for AI service patches.

    Example:
    /api/ai/backlog/status/testing/items/7,8,4,2

    AI Write must be enabled.
    Only updates Status.
    Does not touch Pin, Note, Type, Area, Delete, or dates directly.
    """
    item_ids = backlog_parse_id_list(item_ids_text)

    if not item_ids:
        return jsonify({
            "ok": False,
            "error": "No valid item IDs supplied"
        }), 400

    result = backlog_ai_batch_update_status(
        item_ids,
        "Testing",
        "batch_set_status_testing"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Items moved to Testing",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })


@views_bp.route("/api/ai/backlog/status/done/items/<path:item_ids_text>")
def api_ai_backlog_status_done_items(item_ids_text):
    """
    Batch status endpoint for closing tested/completed Dev items.

    Example:
    /api/ai/backlog/status/done/items/7,8,4,2

    AI Write must be enabled.
    Only updates Status.
    """
    item_ids = backlog_parse_id_list(item_ids_text)

    if not item_ids:
        return jsonify({
            "ok": False,
            "error": "No valid item IDs supplied"
        }), 400

    result = backlog_ai_batch_update_status(
        item_ids,
        "Done",
        "batch_set_status_done"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Items moved to Done",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })


@views_bp.route("/api/ai/backlog/status/in-progress/items/<path:item_ids_text>")
def api_ai_backlog_status_in_progress_items(item_ids_text):
    """
    Batch status endpoint for moving accepted work to In Progress.

    Example:
    /api/ai/backlog/status/in-progress/items/7,8,4,2

    AI Write must be enabled.
    Only updates Status.
    """
    item_ids = backlog_parse_id_list(item_ids_text)

    if not item_ids:
        return jsonify({
            "ok": False,
            "error": "No valid item IDs supplied"
        }), 400

    result = backlog_ai_batch_update_status(
        item_ids,
        "In Progress",
        "batch_set_status_in_progress"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Items moved to In Progress",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })


@views_bp.route("/api/ai/backlog/status/deferred/items/<path:item_ids_text>")
def api_ai_backlog_status_deferred_items(item_ids_text):
    """
    Batch status endpoint for deferring Dev items.

    Example:
    /api/ai/backlog/status/deferred/items/7,8

    AI Write must be enabled.
    Only updates Status.
    """
    item_ids = backlog_parse_id_list(item_ids_text)

    if not item_ids:
        return jsonify({
            "ok": False,
            "error": "No valid item IDs supplied"
        }), 400

    result = backlog_ai_batch_update_status(
        item_ids,
        "Deferred",
        "batch_set_status_deferred"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Items moved to Deferred",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })


@views_bp.route("/api/ai/backlog/service/rev-28-testing")
def api_ai_backlog_service_rev_28_testing():
    """
    Convenience service endpoint for Rev 28 addressed items:
    7 = AI Write indicator placement
    8 = AI Write indicator visible while logged out
    4 = duplicate submit protection
    2 = Dev table cleanup

    AI Write must be enabled.
    Only updates Status to Testing.
    """
    item_ids = [7, 8, 4, 2]

    result = backlog_ai_batch_update_status(
        item_ids,
        "Testing",
        "service_rev_28_set_testing"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Rev 28 addressed items moved to Testing",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })


@views_bp.route("/api/ai/backlog/status-batch-help")
def api_ai_backlog_status_batch_help():
    base = backlog_public_base_url_rev26()

    return jsonify({
        "ai_write_enabled": backlog_ai_write_enabled(),
        "allowed_batch_status_actions": {
            "testing": base + "/api/ai/backlog/status/testing/items/7,8,4,2",
            "done": base + "/api/ai/backlog/status/done/items/7,8,4,2",
            "in_progress": base + "/api/ai/backlog/status/in-progress/items/7,8,4,2",
            "deferred": base + "/api/ai/backlog/status/deferred/items/7,8,4,2"
        },
        "service_actions": {
            "rev_28_testing": base + "/api/ai/backlog/service/rev-28-testing"
        },
        "notes": [
            "AI Write must be enabled.",
            "These endpoints only update Status.",
            "Pin, Note, Type, Area, Delete, and dates are not writable through these endpoints.",
            "Use comma-separated item IDs in the path for custom batches."
        ]
    })

# === PF67 REV30 DEV LIVE LIST ===

def backlog_rows_fingerprint():
    import hashlib
    import json

    rows = backlog_rows(include_done=True)

    payload_rows = []

    for row in rows:
        payload_rows.append({
            "id": row.get("id"),
            "pin": row.get("pin"),
            "type": row.get("type"),
            "priority": row.get("priority"),
            "area": row.get("area"),
            "status": row.get("status"),
            "note": row.get("note"),
            "ai_summary": row.get("ai_summary"),
            "created_at": row.get("created_at"),
            "modified_at": row.get("modified_at"),
            "resolved_in_rev": row.get("resolved_in_rev")
        })

    payload = json.dumps(payload_rows, sort_keys=True)

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@views_bp.route("/api/backlog/fingerprint")
def api_backlog_fingerprint():
    return jsonify({
        "fingerprint": backlog_rows_fingerprint(),
        "server_time": backlog_now(),
        "item_count": len(backlog_rows(include_done=True)),
        "open_item_count": len(backlog_rows(include_done=False))
    })


@views_bp.route("/api/ai/backlog/service/rev-30-testing")
def api_ai_backlog_service_rev_30_testing():
    """
    Convenience service endpoint for Rev 30 addressed items:
    14 = live/auto-refresh Dev list
    13 = hide Add Dev form and improve Dev editor/action layout
    12 = list filtering/search/sort management
    11 = rename Backlog area to Dev

    AI Write must be enabled.
    Only updates Status to Testing.
    """
    item_ids = [14, 13, 12, 11]

    result = backlog_ai_batch_update_status(
        item_ids,
        "Testing",
        "service_rev_30_set_testing"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Rev 30 addressed items moved to Testing",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })

# === PF67 REV31 DEV ACTIONS / USER-ONLY DONE ===

@views_bp.before_app_request
def pf67_block_ai_done_endpoints():
    """
    User-only Done protection.

    AI/service endpoints may move Dev items to Testing/In Progress/Deferred, but
    may not mark records Done. Final Done confirmation belongs to the user.
    """
    path = request.path or ""

    blocked_prefixes = [
        "/api/ai/backlog/status/done",
        "/api/ai/backlog/command/mark-done"
    ]

    for prefix in blocked_prefixes:
        if path.startswith(prefix):
            return jsonify({
                "ok": False,
                "error": "Done is user-only. AI/service endpoints may not mark Dev items Done."
            }), 403

    return None


@views_bp.route("/api/ai/backlog/control")
def api_ai_backlog_control():
    """
    Stable Dev AI control surface.

    This is the single endpoint the assistant should check first when asked to
    inspect Dev. It exposes current open items and safe common action URLs.
    """
    rows = backlog_rows(include_done=False)
    base = backlog_public_base_url_rev26()

    status_counts = {}

    for row in rows:
        status = row.get("status") or ""
        status_counts[status] = status_counts.get(status, 0) + 1

    open_ids = [row["id"] for row in rows]
    missing_summary_ids = [
        row["id"]
        for row in rows
        if not (row.get("ai_summary") or "").strip()
    ]

    return jsonify({
        "app_revision": current_app.config.get("APP_REVISION", ""),
        "server_time": backlog_now(),
        "ai_write_enabled": backlog_ai_write_enabled(),
        "open_count": len(rows),
        "open_ids": open_ids,
        "missing_ai_summary_ids": missing_summary_ids,
        "status_counts": status_counts,
        "allowed_ai_write_fields": [
            "ai_summary",
            "status",
            "priority",
            "resolved_in_rev"
        ],
        "user_only_fields_or_actions": [
            "pin",
            "note",
            "type",
            "area",
            "delete",
            "done"
        ],
        "common_actions": {
            "read_open": base + "/api/backlog/open",
            "workbench": base + "/api/ai/backlog/workbench-v3",
            "audit": base + "/api/ai/backlog/audit",
            "summarize_all_missing": base + "/api/ai/backlog/command/summarize-all-missing",
            "preview_summarize_all_missing": base + "/api/ai/backlog/command/preview-summarize-all-missing",
            "move_open_to_testing": base + "/api/ai/backlog/status/testing/items/" + ",".join(str(item_id) for item_id in open_ids),
            "move_open_to_in_progress": base + "/api/ai/backlog/status/in-progress/items/" + ",".join(str(item_id) for item_id in open_ids),
            "move_open_to_deferred": base + "/api/ai/backlog/status/deferred/items/" + ",".join(str(item_id) for item_id in open_ids)
        },
        "items": [
            backlog_item_to_api(row)
            for row in rows
        ]
    })


@views_bp.route("/api/ai/backlog/service/rev-31-testing")
def api_ai_backlog_service_rev_31_testing():
    """
    Convenience endpoint for Rev 31 addressed items.

    13 = Move Note editor Save/Cancel into row action area and make buttons uniform.
    Also covers visible ID column and user-only Done protection.

    AI Write must be enabled.
    Only updates Status to Testing.
    """
    item_ids = [13]

    result = backlog_ai_batch_update_status(
        item_ids,
        "Testing",
        "service_rev_31_set_testing"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Rev 31 addressed items moved to Testing",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })

# === PF67 REV32 ITEM 13 SERVICE ===

@views_bp.before_app_request
def pf67_rev32_block_ai_done_endpoints():
    """
    Final Done protection.

    AI/service endpoints may move Dev items to Testing/In Progress/Deferred,
    but may not mark records Done. Done belongs to the user only.
    """
    path = request.path or ""

    blocked_prefixes = [
        "/api/ai/backlog/status/done",
        "/api/ai/backlog/command/mark-done"
    ]

    for prefix in blocked_prefixes:
        if path.startswith(prefix):
            return jsonify({
                "ok": False,
                "error": "Done is user-only. AI/service endpoints may not mark Dev items Done."
            }), 403

    return None


@views_bp.route("/api/ai/backlog/service/rev-32-testing")
def api_ai_backlog_service_rev_32_testing():
    """
    Convenience endpoint for Rev 32 addressed item:
    13 = Dev action/editor cleanup.

    AI Write must be enabled.
    Only updates Status to Testing.
    """
    item_ids = [13]

    result = backlog_ai_batch_update_status(
        item_ids,
        "Testing",
        "service_rev_32_set_testing"
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"]
        }), result["status"]

    return jsonify({
        "ok": True,
        "message": "Rev 32 addressed item moved to Testing",
        "requested_ids": item_ids,
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
        "updated_items": result["updated_items"],
        "skipped_items": result["skipped_items"]
    })

# === PF67 PATCH 002: Protocol category save fallback ===
from flask import request, redirect, url_for
from .models import db, ProtocolTemplate, StepTemplate

try:
    _pf67_patch_002_edit_required = edit_required
except NameError:
    def _pf67_patch_002_edit_required(func):
        return func

@views_bp.route("/templates/<int:template_id>/category", methods=["POST"])
@_pf67_patch_002_edit_required
def save_template_category_detail_v105c(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    category = (request.form.get("category") or "").strip()

    if hasattr(template, "category"):
        template.category = category

    db.session.commit()

    try:
        return redirect(url_for("views.template_detail", template_id=template.id))
    except Exception:
        return redirect("/templates/" + str(template.id))



# === PF67 PATCH 002: Protocol timing-only edit fallback ===
def _pf67_patch_002_minutes_from_form(prefix):
    raw_value = (request.form.get(prefix + "_value") or "").strip()

    if raw_value == "":
        return None

    try:
        value = int(float(raw_value))
    except Exception:
        return None

    if value < 0:
        value = 0

    return value

@views_bp.route("/templates/<int:template_id>/steps/<int:step_id>/timing-v107", methods=["POST"])
@_pf67_patch_002_edit_required
def update_template_step_timing_v107(template_id, step_id):
    step = StepTemplate.query.filter_by(id=step_id, template_id=template_id).first_or_404()

    step.minimum_minutes = _pf67_patch_002_minutes_from_form("minimum")
    step.ideal_minutes = _pf67_patch_002_minutes_from_form("ideal")
    step.limit_minutes = _pf67_patch_002_minutes_from_form("limit")
    step.detrimental_minutes = _pf67_patch_002_minutes_from_form("detrimental")
    step.failure_minutes = _pf67_patch_002_minutes_from_form("failure")

    db.session.commit()

    try:
        return redirect(url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step_id))
    except Exception:
        return redirect("/templates/" + str(template_id) + "#template-step-" + str(step_id))


# === PF67 PATCH 003 PROTOCOL DOCUMENT EDITOR ===
# UI says Protocols, but internal names remain ProtocolTemplate/StepTemplate for stability.
import os as _pf67_p003_os
import sqlite3 as _pf67_p003_sqlite3
import uuid as _pf67_p003_uuid
from datetime import datetime as _pf67_p003_datetime
from pathlib import Path as _pf67_p003_Path
from flask import request as _pf67_p003_request, redirect as _pf67_p003_redirect, url_for as _pf67_p003_url_for, current_app as _pf67_p003_current_app, render_template as _pf67_p003_render_template
from werkzeug.utils import secure_filename as _pf67_p003_secure_filename
from .models import db as _pf67_p003_db, ProtocolTemplate as _PF67P003ProtocolTemplate, StepTemplate as _PF67P003StepTemplate

try:
    _pf67_p003_edit_required = edit_required
except NameError:
    def _pf67_p003_edit_required(func):
        return func


def _pf67_p003_int_or_none(value):
    value = (value or "").strip()
    if value == "":
        return None
    try:
        number = int(float(value))
    except Exception:
        return None
    if number < 0:
        number = 0
    return number


def _pf67_p003_db_path():
    uri = _pf67_p003_current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if uri.startswith("sqlite:///"):
        return uri.replace("sqlite:///", "", 1)
    base_dir = _pf67_p003_current_app.config.get("BASE_DIR", _pf67_p003_current_app.root_path)
    return str(_pf67_p003_Path(base_dir) / "db" / "pf67.sqlite3")


def _pf67_p003_connect():
    connection = _pf67_p003_sqlite3.connect(_pf67_p003_db_path())
    connection.row_factory = _pf67_p003_sqlite3.Row
    return connection


def _pf67_p003_ensure_protocol_attachment_schema():
    connection = _pf67_p003_connect()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS protocol_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                attachment_type TEXT NOT NULL DEFAULT 'Reference',
                sort_order INTEGER NOT NULL DEFAULT 1,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
    finally:
        connection.close()


@views_bp.app_template_global("pf67_protocol_categories")
def pf67_protocol_categories_patch003():
    categories = []
    try:
        if hasattr(_PF67P003ProtocolTemplate, "category"):
            rows = _PF67P003ProtocolTemplate.query.with_entities(_PF67P003ProtocolTemplate.category).all()
            for row in rows:
                value = (row[0] or "").strip()
                if value and value not in categories:
                    categories.append(value)
    except Exception:
        pass
    categories.sort(key=lambda value: value.lower())
    return categories


@views_bp.app_template_global("pf67_protocol_images")
def pf67_protocol_images_patch003(template_id):
    _pf67_p003_ensure_protocol_attachment_schema()
    connection = _pf67_p003_connect()
    try:
        rows = connection.execute(
            """
            SELECT * FROM protocol_attachments
            WHERE template_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (template_id,)
        ).fetchall()
        images = []
        for row in rows:
            data = dict(row)
            data["url"] = data.get("storage_path") or ""
            images.append(data)
        return images
    finally:
        connection.close()


@views_bp.app_template_global("pf67_protocol_cover_image")
def pf67_protocol_cover_image_patch003(template_id):
    images = pf67_protocol_images_patch003(template_id)
    if not images:
        return ""
    return images[0].get("url", "")


@views_bp.route("/protocols/new", methods=["GET", "POST"])
@_pf67_p003_edit_required
def new_protocol_patch003():
    if _pf67_p003_request.method == "POST":
        name = (_pf67_p003_request.form.get("name") or "").strip()
        description = (_pf67_p003_request.form.get("description") or "").strip()
        category = (_pf67_p003_request.form.get("category") or "").strip()

        if not name:
            name = "Untitled Protocol"

        template = _PF67P003ProtocolTemplate(name=name, description=description)
        if hasattr(template, "category"):
            template.category = category

        _pf67_p003_db.session.add(template)
        _pf67_p003_db.session.commit()
        return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template.id))

    return _pf67_p003_render_template("new_template.html")


@views_bp.route("/templates/<int:template_id>/protocol-info-p003", methods=["POST"])
@_pf67_p003_edit_required
def update_protocol_info_patch003(template_id):
    template = _PF67P003ProtocolTemplate.query.get_or_404(template_id)

    name = (_pf67_p003_request.form.get("name") or "").strip()
    description = (_pf67_p003_request.form.get("description") or "").strip()
    category = (_pf67_p003_request.form.get("category") or "").strip()

    if name:
        template.name = name
    template.description = description
    if hasattr(template, "category"):
        template.category = category

    _pf67_p003_db.session.commit()
    return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template.id))


@views_bp.route("/templates/<int:template_id>/steps/add-p003", methods=["POST"])
@_pf67_p003_edit_required
def add_protocol_step_patch003(template_id):
    template = _PF67P003ProtocolTemplate.query.get_or_404(template_id)
    after_step_id = (_pf67_p003_request.form.get("after_step_id") or "").strip()

    if after_step_id.isdigit():
        after_step = _PF67P003StepTemplate.query.filter_by(id=int(after_step_id), template_id=template_id).first()
    else:
        after_step = None

    if after_step:
        insert_order = (after_step.sort_order or 0) + 1
        later_steps = _PF67P003StepTemplate.query.filter(
            _PF67P003StepTemplate.template_id == template_id,
            _PF67P003StepTemplate.sort_order >= insert_order
        ).all()
        for later_step in later_steps:
            later_step.sort_order = (later_step.sort_order or 0) + 1
    else:
        existing_orders = [step.sort_order or 0 for step in template.steps]
        insert_order = (max(existing_orders) if existing_orders else 0) + 1

    name = (_pf67_p003_request.form.get("name") or "").strip()
    if not name:
        name = ""

    step = _PF67P003StepTemplate(
        template_id=template.id,
        sort_order=insert_order,
        name=name,
        step_type=(_pf67_p003_request.form.get("step_type") or "action").strip() or "action",
        context_tag=(_pf67_p003_request.form.get("context_tag") or "").strip(),
        instructions_html=(_pf67_p003_request.form.get("instructions_html") or "").strip(),
        minimum_minutes=_pf67_p003_int_or_none(_pf67_p003_request.form.get("minimum_minutes")),
        ideal_minutes=_pf67_p003_int_or_none(_pf67_p003_request.form.get("ideal_minutes")),
        limit_minutes=_pf67_p003_int_or_none(_pf67_p003_request.form.get("limit_minutes")),
        detrimental_minutes=_pf67_p003_int_or_none(_pf67_p003_request.form.get("detrimental_minutes")),
        failure_minutes=_pf67_p003_int_or_none(_pf67_p003_request.form.get("failure_minutes"))
    )

    _pf67_p003_db.session.add(step)
    _pf67_p003_db.session.commit()
    return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template.id) + "#template-step-" + str(step.id))


@views_bp.route("/templates/<int:template_id>/steps/<int:step_id>/edit-p003", methods=["POST"])
@_pf67_p003_edit_required
def update_protocol_step_patch003(template_id, step_id):
    step = _PF67P003StepTemplate.query.filter_by(id=step_id, template_id=template_id).first_or_404()

    step.name = (_pf67_p003_request.form.get("name") or "").strip()
    step.step_type = (_pf67_p003_request.form.get("step_type") or "action").strip() or "action"
    step.context_tag = (_pf67_p003_request.form.get("context_tag") or "").strip()
    step.instructions_html = (_pf67_p003_request.form.get("instructions_html") or "").strip()

    step.minimum_minutes = _pf67_p003_int_or_none(_pf67_p003_request.form.get("minimum_minutes"))
    step.ideal_minutes = _pf67_p003_int_or_none(_pf67_p003_request.form.get("ideal_minutes"))
    step.limit_minutes = _pf67_p003_int_or_none(_pf67_p003_request.form.get("limit_minutes"))
    step.detrimental_minutes = _pf67_p003_int_or_none(_pf67_p003_request.form.get("detrimental_minutes"))
    step.failure_minutes = _pf67_p003_int_or_none(_pf67_p003_request.form.get("failure_minutes"))

    _pf67_p003_db.session.commit()
    return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step.id))


@views_bp.route("/templates/<int:template_id>/protocol-image-p003", methods=["POST"])
@_pf67_p003_edit_required
def upload_protocol_image_patch003(template_id):
    _PF67P003ProtocolTemplate.query.get_or_404(template_id)
    _pf67_p003_ensure_protocol_attachment_schema()

    file_obj = _pf67_p003_request.files.get("image")
    if not file_obj or not file_obj.filename:
        return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template_id))

    original_name = _pf67_p003_secure_filename(file_obj.filename)
    suffix = _pf67_p003_Path(original_name).suffix.lower()
    if suffix not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        suffix = ".jpg"

    stored_name = _pf67_p003_uuid.uuid4().hex + suffix
    relative_folder = _pf67_p003_Path("uploads") / "protocols" / str(template_id)
    absolute_folder = _pf67_p003_Path(_pf67_p003_current_app.static_folder) / relative_folder
    absolute_folder.mkdir(parents=True, exist_ok=True)
    absolute_path = absolute_folder / stored_name
    file_obj.save(str(absolute_path))

    storage_path = "/static/" + str(relative_folder / stored_name).replace(_pf67_p003_os.sep, "/")
    title = (_pf67_p003_request.form.get("title") or "").strip()
    caption = (_pf67_p003_request.form.get("caption") or "").strip()
    attachment_type = (_pf67_p003_request.form.get("attachment_type") or "Reference").strip() or "Reference"

    connection = _pf67_p003_connect()
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM protocol_attachments WHERE template_id = ?",
            (template_id,)
        ).fetchone()
        sort_order = row[0] if row else 1
        connection.execute(
            """
            INSERT INTO protocol_attachments
                (template_id, filename, storage_path, mime_type, title, caption, attachment_type, sort_order, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                original_name or stored_name,
                storage_path,
                file_obj.mimetype or "",
                title,
                caption,
                attachment_type,
                sort_order,
                _pf67_p003_datetime.utcnow().isoformat(timespec="seconds")
            )
        )
        connection.commit()
    finally:
        connection.close()

    return _pf67_p003_redirect(_pf67_p003_url_for("views.template_detail", template_id=template_id) + "#protocol-image-upload-panel")
# === END PF67 PATCH 003 PROTOCOL DOCUMENT EDITOR ===

# === PF67 PATCH 006 PROTOCOL DOCUMENT EDITOR ===
import mimetypes
import uuid
from pathlib import Path as _Pf67Path
from flask import request, redirect, url_for, jsonify, current_app, render_template
from werkzeug.utils import secure_filename
from .models import db, ProtocolTemplate, StepTemplate

try:
    _pf67_patch006_edit_required = edit_required
except NameError:
    def _pf67_patch006_edit_required(func):
        return func


def _pf67_patch006_int_or_none(value):
    value = (value or "").strip()

    if value == "":
        return None

    try:
        parsed = int(float(value))
    except Exception:
        return None

    if parsed < 0:
        parsed = 0

    return parsed


def _pf67_patch006_form_minutes(name):
    return _pf67_patch006_int_or_none(request.form.get(name))


def _pf67_patch006_sort_order_for_new_step(template_id, after_step_id):
    if after_step_id:
        after_step = StepTemplate.query.filter_by(id=after_step_id, template_id=template_id).first()

        if after_step:
            base_order = after_step.sort_order or 0
            later_steps = (
                StepTemplate.query
                .filter(StepTemplate.template_id == template_id)
                .filter(StepTemplate.sort_order > base_order)
                .order_by(StepTemplate.sort_order.asc())
                .all()
            )

            for later_step in later_steps:
                later_step.sort_order = (later_step.sort_order or 0) + 10

            return base_order + 10

    max_order = 0
    for existing in StepTemplate.query.filter_by(template_id=template_id).all():
        if existing.sort_order and existing.sort_order > max_order:
            max_order = existing.sort_order

    return max_order + 10


def _pf67_patch006_protocol_upload_folder(template_id):
    static_folder = current_app.static_folder

    if not static_folder:
        static_folder = str(_Pf67Path(current_app.root_path) / "static")

    folder = _Pf67Path(static_folder) / "uploads" / "protocols" / str(template_id) / "editor"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _pf67_patch006_protocol_upload_url(template_id, filename):
    return url_for("static", filename="uploads/protocols/" + str(template_id) + "/editor/" + filename)


def _pf67_patch006_allowed_image(filename, mimetype):
    allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    suffix = _Pf67Path(filename or "").suffix.lower()

    if suffix not in allowed_extensions:
        return False

    if mimetype and not mimetype.startswith("image/"):
        guessed = mimetypes.guess_type(filename)[0] or ""
        return guessed.startswith("image/")

    return True


@views_bp.app_template_filter("pf67_patch006_human_minutes")
def pf67_patch006_human_minutes(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return ""

    if minutes <= 0:
        return ""

    days = minutes // 1440
    remainder = minutes % 1440
    hours = remainder // 60
    mins = remainder % 60

    parts = []

    if days:
        label = "day" if days == 1 else "days"
        parts.append(str(days) + " " + label)

    if hours:
        label = "hour" if hours == 1 else "hours"
        parts.append(str(hours) + " " + label)

    if mins or not parts:
        label = "minute" if mins == 1 else "minutes"
        parts.append(str(mins) + " " + label)

    return " ".join(parts)


@views_bp.app_template_global()
def pf67_patch006_protocol_categories():
    categories = []

    try:
        rows = ProtocolTemplate.query.order_by(ProtocolTemplate.category.asc()).all()
        seen = set()
        for row in rows:
            category = (getattr(row, "category", "") or "").strip()
            if category and category not in seen:
                seen.add(category)
                categories.append(category)
    except Exception:
        pass

    return categories


@views_bp.app_template_global()
def pf67_patch006_protocol_contexts():
    contexts = []

    try:
        rows = StepTemplate.query.order_by(StepTemplate.context_tag.asc()).all()
        seen = set()
        for row in rows:
            context = (getattr(row, "context_tag", "") or "").strip()
            if context and context not in seen:
                seen.add(context)
                contexts.append(context)
    except Exception:
        pass

    return contexts


@views_bp.route("/protocols/new", methods=["GET", "POST"])
@_pf67_patch006_edit_required
def pf67_patch006_new_protocol():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        description = request.form.get("description") or ""

        if not name:
            name = "Untitled Protocol"

        template = ProtocolTemplate(name=name, description=description)

        if hasattr(template, "category"):
            template.category = category

        db.session.add(template)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    return render_template("new_protocol_patch006.html")


@views_bp.route("/protocols/<int:template_id>/update-info", methods=["POST"])
@_pf67_patch006_edit_required
def pf67_patch006_update_protocol_info(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip()
    description = request.form.get("description") or ""

    if name:
        template.name = name

    if hasattr(template, "category"):
        template.category = category

    template.description = description

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/protocols/<int:template_id>/steps/<int:step_id>/update-inline", methods=["POST"])
@_pf67_patch006_edit_required
def pf67_patch006_update_protocol_step(template_id, step_id):
    step = StepTemplate.query.filter_by(id=step_id, template_id=template_id).first_or_404()

    step.name = (request.form.get("name") or "").strip()
    step.step_type = (request.form.get("step_type") or "action").strip()
    step.context_tag = (request.form.get("context_tag") or "").strip()
    step.instructions_html = request.form.get("instructions_html") or ""

    step.minimum_minutes = _pf67_patch006_form_minutes("minimum_minutes")
    step.ideal_minutes = _pf67_patch006_form_minutes("ideal_minutes")
    step.limit_minutes = _pf67_patch006_form_minutes("limit_minutes")
    step.detrimental_minutes = _pf67_patch006_form_minutes("detrimental_minutes")
    step.failure_minutes = _pf67_patch006_form_minutes("failure_minutes")

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step.id))


@views_bp.route("/protocols/<int:template_id>/steps/add-patch006", methods=["POST"])
@_pf67_patch006_edit_required
def pf67_patch006_add_protocol_step(template_id):
    ProtocolTemplate.query.get_or_404(template_id)

    after_step_id = _pf67_patch006_int_or_none(request.form.get("after_step_id"))
    sort_order = _pf67_patch006_sort_order_for_new_step(template_id, after_step_id)

    step = StepTemplate(
        template_id=template_id,
        name=(request.form.get("name") or "").strip(),
        step_type=(request.form.get("step_type") or "action").strip(),
        context_tag=(request.form.get("context_tag") or "").strip(),
        instructions_html=request.form.get("instructions_html") or "",
        minimum_minutes=_pf67_patch006_form_minutes("minimum_minutes"),
        ideal_minutes=_pf67_patch006_form_minutes("ideal_minutes"),
        limit_minutes=_pf67_patch006_form_minutes("limit_minutes"),
        detrimental_minutes=_pf67_patch006_form_minutes("detrimental_minutes"),
        failure_minutes=_pf67_patch006_form_minutes("failure_minutes"),
        sort_order=sort_order,
    )

    db.session.add(step)
    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step.id))


@views_bp.route("/protocols/<int:template_id>/editor-images/upload", methods=["POST"])
@_pf67_patch006_edit_required
def pf67_patch006_protocol_image_upload(template_id):
    ProtocolTemplate.query.get_or_404(template_id)

    uploaded = request.files.get("file")

    if not uploaded:
        return jsonify({"error": "No file uploaded"}), 400

    original = secure_filename(uploaded.filename or "image")
    if not _pf67_patch006_allowed_image(original, uploaded.mimetype or ""):
        return jsonify({"error": "Only image uploads are allowed"}), 400

    suffix = _Pf67Path(original).suffix.lower()
    if not suffix:
        suffix = ".jpg"

    safe_stem = _Pf67Path(original).stem or "image"
    filename = safe_stem[:40] + "_" + uuid.uuid4().hex[:12] + suffix

    folder = _pf67_patch006_protocol_upload_folder(template_id)
    destination = folder / filename
    uploaded.save(str(destination))

    return jsonify({
        "location": _pf67_patch006_protocol_upload_url(template_id, filename),
        "name": original,
    })


@views_bp.route("/protocols/<int:template_id>/editor-images/list", methods=["GET"])
@_pf67_patch006_edit_required
def pf67_patch006_protocol_image_list(template_id):
    ProtocolTemplate.query.get_or_404(template_id)

    folder = _pf67_patch006_protocol_upload_folder(template_id)
    images = []

    for path in sorted(folder.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue

        if path.suffix.lower() not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            continue

        images.append({
            "name": path.name,
            "url": _pf67_patch006_protocol_upload_url(template_id, path.name),
        })

    return jsonify({"images": images})

# === PF67 PATCH 006B LOCAL PROTOCOL EDITOR ===
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate, StepTemplate

try:
    _pf67_patch006b_edit_required = edit_required
except NameError:
    def _pf67_patch006b_edit_required(func):
        return func


def _pf67_patch006b_int_or_none(value):
    value = (value or "").strip()

    if value == "":
        return None

    try:
        parsed = int(float(value))
    except Exception:
        return None

    if parsed < 0:
        parsed = 0

    return parsed


def _pf67_patch006b_minutes_from_value_unit(value_name, unit_name):
    value = _pf67_patch006b_int_or_none(request.form.get(value_name))

    if value is None:
        return None

    unit = (request.form.get(unit_name) or "minutes").strip().lower()

    if unit == "days":
        return value * 1440

    if unit == "hours":
        return value * 60

    return value


def _pf67_patch006b_sort_order_for_new_step(template_id, after_step_id):
    if after_step_id:
        after_step = StepTemplate.query.filter_by(id=after_step_id, template_id=template_id).first()

        if after_step:
            base_order = after_step.sort_order or 0
            later_steps = (
                StepTemplate.query
                .filter(StepTemplate.template_id == template_id)
                .filter(StepTemplate.sort_order > base_order)
                .order_by(StepTemplate.sort_order.asc())
                .all()
            )

            for later_step in later_steps:
                later_step.sort_order = (later_step.sort_order or 0) + 10

            return base_order + 10

    max_order = 0
    for existing in StepTemplate.query.filter_by(template_id=template_id).all():
        if existing.sort_order and existing.sort_order > max_order:
            max_order = existing.sort_order

    return max_order + 10


@views_bp.app_template_filter("pf67_patch006b_human_minutes")
def pf67_patch006b_human_minutes(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return ""

    if minutes <= 0:
        return ""

    days = minutes // 1440
    remainder = minutes % 1440
    hours = remainder // 60
    mins = remainder % 60

    parts = []

    if days:
        parts.append(str(days) + (" day" if days == 1 else " days"))

    if hours:
        parts.append(str(hours) + (" hour" if hours == 1 else " hours"))

    if mins or not parts:
        parts.append(str(mins) + (" minute" if mins == 1 else " minutes"))

    return " ".join(parts)


@views_bp.app_template_filter("pf67_patch006b_duration_unit")
def pf67_patch006b_duration_unit(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return "minutes"

    if minutes > 0 and minutes % 1440 == 0:
        return "days"

    if minutes > 0 and minutes % 60 == 0:
        return "hours"

    return "minutes"


@views_bp.app_template_filter("pf67_patch006b_duration_value")
def pf67_patch006b_duration_value(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return ""

    if minutes <= 0:
        return ""

    if minutes % 1440 == 0:
        return str(minutes // 1440)

    if minutes % 60 == 0:
        return str(minutes // 60)

    return str(minutes)


@views_bp.route("/protocols/new-local", methods=["GET", "POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_new_protocol():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        description = request.form.get("description") or ""

        if not name:
            name = "Untitled Protocol"

        template = ProtocolTemplate(name=name, description=description)

        if hasattr(template, "category"):
            template.category = category

        db.session.add(template)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    return render_template("new_protocol_patch006b.html")


@views_bp.route("/protocols/<int:template_id>/title-local", methods=["POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_update_protocol_title(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    name = (request.form.get("name") or "").strip()

    if name:
        template.name = name
        db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/protocols/<int:template_id>/category-local", methods=["POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_update_protocol_category(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    category = (request.form.get("category") or "").strip()

    if hasattr(template, "category"):
        template.category = category
        db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/protocols/<int:template_id>/description-local", methods=["POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_update_protocol_description(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    template.description = request.form.get("description") or ""
    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/protocols/<int:template_id>/steps/<int:step_id>/update-local", methods=["POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_update_protocol_step(template_id, step_id):
    step = StepTemplate.query.filter_by(id=step_id, template_id=template_id).first_or_404()

    step.name = (request.form.get("name") or "").strip()
    step.step_type = (request.form.get("step_type") or "action").strip()
    step.context_tag = (request.form.get("context_tag") or "").strip()
    step.instructions_html = request.form.get("instructions_html") or ""

    if "minimum_value" in request.form or "minimum_unit" in request.form:
        step.minimum_minutes = _pf67_patch006b_minutes_from_value_unit("minimum_value", "minimum_unit")
    if "ideal_value" in request.form or "ideal_unit" in request.form:
        step.ideal_minutes = _pf67_patch006b_minutes_from_value_unit("ideal_value", "ideal_unit")
    if "limit_value" in request.form or "limit_unit" in request.form:
        step.limit_minutes = _pf67_patch006b_minutes_from_value_unit("limit_value", "limit_unit")
    if "detrimental_value" in request.form or "detrimental_unit" in request.form:
        step.detrimental_minutes = _pf67_patch006b_minutes_from_value_unit("detrimental_value", "detrimental_unit")
    if "failure_value" in request.form or "failure_unit" in request.form:
        step.failure_minutes = _pf67_patch006b_minutes_from_value_unit("failure_value", "failure_unit")

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step.id))


@views_bp.route("/protocols/<int:template_id>/steps/add-local", methods=["POST"])
@_pf67_patch006b_edit_required
def pf67_patch006b_add_protocol_step(template_id):
    ProtocolTemplate.query.get_or_404(template_id)

    after_step_id = _pf67_patch006b_int_or_none(request.form.get("after_step_id"))
    sort_order = _pf67_patch006b_sort_order_for_new_step(template_id, after_step_id)

    step = StepTemplate(
        template_id=template_id,
        name=(request.form.get("name") or "").strip(),
        step_type=(request.form.get("step_type") or "action").strip(),
        context_tag=(request.form.get("context_tag") or "").strip(),
        instructions_html=request.form.get("instructions_html") or "",
        minimum_minutes=_pf67_patch006b_minutes_from_value_unit("minimum_value", "minimum_unit"),
        ideal_minutes=_pf67_patch006b_minutes_from_value_unit("ideal_value", "ideal_unit"),
        limit_minutes=_pf67_patch006b_minutes_from_value_unit("limit_value", "limit_unit"),
        detrimental_minutes=_pf67_patch006b_minutes_from_value_unit("detrimental_value", "detrimental_unit"),
        failure_minutes=_pf67_patch006b_minutes_from_value_unit("failure_value", "failure_unit"),
        sort_order=sort_order,
    )

    db.session.add(step)
    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template_id) + "#template-step-" + str(step.id))

# === PF67 PATCH 007C TIMING WORDING FILTERS ===
@views_bp.app_template_filter("pf67_patch007c_duration_label")
def pf67_patch007c_duration_label(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return ""

    if minutes <= 0:
        return ""

    days = minutes // 1440
    remainder = minutes % 1440
    hours = remainder // 60
    mins = remainder % 60

    parts = []

    if days:
        parts.append(str(days) + (" day" if days == 1 else " days"))

    if hours:
        parts.append(str(hours) + (" hour" if hours == 1 else " hours"))

    if mins or not parts:
        parts.append(str(mins) + (" minute" if mins == 1 else " minutes"))

    return " ".join(parts)


@views_bp.app_template_filter("pf67_patch007c_wait_label")
def pf67_patch007c_wait_label(value):
    try:
        minutes = int(value or 0)
    except Exception:
        return ""

    if minutes <= 0:
        return ""

    label = pf67_patch007c_duration_label(minutes)

    # One day reads naturally as "In 1 day"; 2+ days reads better as a process wait.
    if minutes >= 2880:
        return "Wait " + label

    return "In " + label

# === PF67 PATCH 008A FLOW START DELETE CONTROLS ===
from datetime import datetime as _pf67_008_datetime, timedelta as _pf67_008_timedelta
from flask import request, redirect, url_for, flash
from .models import db, ProtocolTemplate, StepTemplate, Job, JobStep

try:
    _pf67_patch008_edit_required = edit_required
except NameError:
    def _pf67_patch008_edit_required(func):
        return func


def _pf67_patch008_parse_datetime_local(value):
    value = (value or "").strip()

    if not value:
        return None

    try:
        return _pf67_008_datetime.fromisoformat(value)
    except Exception:
        return None


def _pf67_patch008_normalize_priority(value):
    value = (value or "Normal").strip().title()
    allowed = {"Low", "Normal", "High", "Critical"}

    if value not in allowed:
        value = "Normal"

    return value


def _pf67_patch008_set_if_hasattr(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _pf67_patch008_create_flow_from_template(template, job_name, first_step_anchor, priority):
    if not job_name:
        job_name = template.name + " - Flow"

    job = Job()
    _pf67_patch008_set_if_hasattr(job, "template_id", template.id)
    _pf67_patch008_set_if_hasattr(job, "name", job_name)
    _pf67_patch008_set_if_hasattr(job, "template_version", getattr(template, "current_version", 1))
    _pf67_patch008_set_if_hasattr(job, "started_at", first_step_anchor)
    _pf67_patch008_set_if_hasattr(job, "priority", priority)
    _pf67_patch008_set_if_hasattr(job, "allow_ai_read", True)
    _pf67_patch008_set_if_hasattr(job, "allow_ai_suggest", True)
    _pf67_patch008_set_if_hasattr(job, "allow_ai_write", False)

    db.session.add(job)
    db.session.flush()

    anchor_time = first_step_anchor
    ordered_steps = list(pf67_patch011a2_sequence_flow_steps(template))

    for index, step_template in enumerate(ordered_steps):
        is_first_step = index == 0

        job_step = JobStep()
        _pf67_patch008_set_if_hasattr(job_step, "job_id", job.id)
        _pf67_patch008_set_if_hasattr(job_step, "source_step_template_id", step_template.id)
        _pf67_patch008_set_if_hasattr(job_step, "sort_order", getattr(step_template, "_pf67_flow_sort_order", (index + 1) * 10))
        _pf67_patch008_set_if_hasattr(job_step, "sequence_order", getattr(step_template, "_pf67_flow_sequence_order", (index + 1) * 1000))
        _pf67_patch008_set_if_hasattr(job_step, "name", step_template.name)
        _pf67_patch008_set_if_hasattr(job_step, "step_type", step_template.step_type)
        _pf67_patch008_set_if_hasattr(job_step, "instructions_html", step_template.instructions_html)
        _pf67_patch008_set_if_hasattr(job_step, "context_tag", step_template.context_tag)

        # Step 1 timing belongs to the Flow start dialog. Preserve Protocol timing
        # values on the Protocol step, but do not copy them into Flow Step 1.
        _pf67_patch008_set_if_hasattr(job_step, "minimum_minutes", None if is_first_step else step_template.minimum_minutes)
        _pf67_patch008_set_if_hasattr(job_step, "ideal_minutes", None if is_first_step else step_template.ideal_minutes)
        _pf67_patch008_set_if_hasattr(job_step, "limit_minutes", None if is_first_step else step_template.limit_minutes)
        _pf67_patch008_set_if_hasattr(job_step, "detrimental_minutes", None if is_first_step else step_template.detrimental_minutes)
        _pf67_patch008_set_if_hasattr(job_step, "failure_minutes", None if is_first_step else step_template.failure_minutes)

        _pf67_patch008_set_if_hasattr(job_step, "estimated_duration_minutes", getattr(step_template, "estimated_duration_minutes", None))
        _pf67_patch008_set_if_hasattr(job_step, "anchor_time", anchor_time)

        db.session.add(job_step)

        if not is_first_step and step_template.ideal_minutes is not None:
            anchor_time = anchor_time + _pf67_008_timedelta(minutes=step_template.ideal_minutes)

    db.session.commit()
    return job


@views_bp.route("/protocols/<int:template_id>/start-flow-patch008", methods=["POST"])
@_pf67_patch008_edit_required
def pf67_patch008_start_flow(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    job_name = (request.form.get("job_name") or "").strip()
    priority = _pf67_patch008_normalize_priority(request.form.get("priority"))
    start_mode = (request.form.get("start_mode") or "now").strip()
    selected_time = _pf67_patch008_parse_datetime_local(request.form.get("first_step_at"))

    if start_mode == "now":
        first_step_anchor = _pf67_008_datetime.utcnow()
    else:
        first_step_anchor = selected_time or _pf67_008_datetime.utcnow()

    job = _pf67_patch008_create_flow_from_template(template, job_name, first_step_anchor, priority)

    try:
        return redirect(url_for("views.job_detail", job_id=job.id))
    except Exception:
        return redirect(url_for("views.jobs_page", view="active"))


@views_bp.route("/protocols/<int:template_id>/steps/<int:step_id>/delete-patch008", methods=["POST"])
@_pf67_patch008_edit_required
def pf67_patch008_delete_protocol_step(template_id, step_id):
    step = StepTemplate.query.filter_by(id=step_id, template_id=template_id).first_or_404()

    db.session.delete(step)
    db.session.flush()

    remaining_steps = (
        StepTemplate.query
        .filter_by(template_id=template_id)
        .order_by(StepTemplate.sort_order.asc(), StepTemplate.id.asc())
        .all()
    )

    for index, remaining in enumerate(remaining_steps):
        remaining.sort_order = (index + 1) * 10

    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=template_id))


@views_bp.route("/protocols/<int:template_id>/delete-patch008", methods=["POST"])
@_pf67_patch008_edit_required
def pf67_patch008_delete_protocol(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    existing_flows = 0
    try:
        existing_flows = Job.query.filter_by(template_id=template_id).count()
    except Exception:
        existing_flows = 0

    if existing_flows:
        try:
            flash("This Protocol has existing Flows and was not deleted. Delete or archive related Flows first.", "warning")
        except Exception:
            pass

        return redirect(url_for("views.template_detail", template_id=template_id))

    StepTemplate.query.filter_by(template_id=template_id).delete()
    db.session.delete(template)
    db.session.commit()

    return redirect(url_for("views.templates_page"))

# === PF67 PATCH 008B PROTOCOL LIST COVER CARDS ===
import re as _pf67_008b_re
import html as _pf67_008b_html


@views_bp.app_template_filter("pf67_patch008b_first_img_src")
def pf67_patch008b_first_img_src(value):
    html_value = value or ""

    if not html_value:
        return ""

    pattern = r"<img\b[^>]*\bsrc\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^>\s]+))"
    match = _pf67_008b_re.search(
        pattern,
        html_value,
        flags=_pf67_008b_re.IGNORECASE,
    )

    if not match:
        return ""

    src = match.group(1) or match.group(2) or match.group(3) or ""
    return _pf67_008b_html.unescape(src.strip())


@views_bp.app_template_filter("pf67_patch008b_plain_summary")
def pf67_patch008b_plain_summary(value, max_length=240):
    html_value = value or ""

    if not html_value:
        return ""

    # Remove image tags completely so alt/src/html markup never leaks into the card text.
    html_value = _pf67_008b_re.sub(
        r"<img\b[^>]*>",
        " ",
        html_value,
        flags=_pf67_008b_re.IGNORECASE,
    )

    html_value = _pf67_008b_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_008b_re.IGNORECASE | _pf67_008b_re.DOTALL,
    )

    text_value = _pf67_008b_re.sub(r"<[^>]+>", " ", html_value)
    text_value = _pf67_008b_html.unescape(text_value)
    text_value = _pf67_008b_re.sub(r"\s+", " ", text_value).strip()

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 240

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "…"

    return text_value

# === PF67 PATCH 009A DASHBOARD PUBLIC VISIBILITY ===
from datetime import datetime as _pf67_009_datetime, timedelta as _pf67_009_timedelta
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate, StepTemplate, Job, JobStep


try:
    _pf67_patch009_edit_required = edit_required
except NameError:
    def _pf67_patch009_edit_required(func):
        return func


def _pf67_patch009_form_bool(name):
    return str(request.form.get(name, "")).lower() in ["1", "true", "yes", "on"]


def _pf67_patch009_set_if_hasattr(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _pf67_patch009_parse_datetime_local(value):
    value = (value or "").strip()

    if not value:
        return None

    try:
        return _pf67_009_datetime.fromisoformat(value)
    except Exception:
        return None


def _pf67_patch009_normalize_priority(value):
    value = (value or "Normal").strip().title()
    allowed = {"Low", "Normal", "High", "Critical"}

    if value not in allowed:
        value = "Normal"

    return value


def _pf67_patch009_job_is_public(job):
    if getattr(job, "is_private", False):
        return False

    template = getattr(job, "template", None)

    if template is not None and getattr(template, "is_private", False):
        return False

    return True


def _pf67_patch009_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009_status_label(status):
    try:
        return status_label(status)
    except Exception:
        return (status or "active").replace("_", " ").title()


def _pf67_patch009_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""
    return ""


def _pf67_patch009_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return ""

    value = max(times)

    try:
        return display_short_datetime(value)
    except Exception:
        return value.strftime("%Y-%m-%d %I:%M %p")


def _pf67_patch009_create_flow_from_template(template, job_name, first_step_anchor, priority, is_private):
    if not job_name:
        job_name = template.name + " - Flow"

    job = Job()
    _pf67_patch009_set_if_hasattr(job, "template_id", template.id)
    _pf67_patch009_set_if_hasattr(job, "name", job_name)
    _pf67_patch009_set_if_hasattr(job, "template_version", getattr(template, "current_version", 1))
    _pf67_patch009_set_if_hasattr(job, "started_at", first_step_anchor)
    _pf67_patch009_set_if_hasattr(job, "priority", priority)
    _pf67_patch009_set_if_hasattr(job, "is_private", bool(is_private))
    _pf67_patch009_set_if_hasattr(job, "allow_ai_read", True)
    _pf67_patch009_set_if_hasattr(job, "allow_ai_suggest", True)
    _pf67_patch009_set_if_hasattr(job, "allow_ai_write", False)

    db.session.add(job)
    db.session.flush()

    anchor_time = first_step_anchor
    ordered_steps = list(pf67_patch011a2_sequence_flow_steps(template))

    for index, step_template in enumerate(ordered_steps):
        is_first_step = index == 0

        job_step = JobStep()
        _pf67_patch009_set_if_hasattr(job_step, "job_id", job.id)
        _pf67_patch009_set_if_hasattr(job_step, "source_step_template_id", step_template.id)
        _pf67_patch009_set_if_hasattr(job_step, "sort_order", getattr(step_template, "_pf67_flow_sort_order", (index + 1) * 10))
        _pf67_patch009_set_if_hasattr(job_step, "sequence_order", getattr(step_template, "_pf67_flow_sequence_order", (index + 1) * 1000))
        _pf67_patch009_set_if_hasattr(job_step, "name", step_template.name)
        _pf67_patch009_set_if_hasattr(job_step, "step_type", step_template.step_type)
        _pf67_patch009_set_if_hasattr(job_step, "instructions_html", step_template.instructions_html)
        _pf67_patch009_set_if_hasattr(job_step, "context_tag", step_template.context_tag)
        _pf67_patch009_set_if_hasattr(job_step, "minimum_minutes", None if is_first_step else step_template.minimum_minutes)
        _pf67_patch009_set_if_hasattr(job_step, "ideal_minutes", None if is_first_step else step_template.ideal_minutes)
        _pf67_patch009_set_if_hasattr(job_step, "limit_minutes", None if is_first_step else step_template.limit_minutes)
        _pf67_patch009_set_if_hasattr(job_step, "detrimental_minutes", None if is_first_step else step_template.detrimental_minutes)
        _pf67_patch009_set_if_hasattr(job_step, "failure_minutes", None if is_first_step else step_template.failure_minutes)
        _pf67_patch009_set_if_hasattr(job_step, "estimated_duration_minutes", getattr(step_template, "estimated_duration_minutes", None))
        _pf67_patch009_set_if_hasattr(job_step, "anchor_time", anchor_time)

        db.session.add(job_step)

        if not is_first_step and step_template.ideal_minutes is not None:
            anchor_time = anchor_time + _pf67_009_timedelta(minutes=step_template.ideal_minutes)

    db.session.commit()
    return job


@views_bp.route("/protocols/new-patch009", methods=["GET", "POST"])
@_pf67_patch009_edit_required
def pf67_patch009_new_protocol():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        description = request.form.get("description") or ""
        is_private = _pf67_patch009_form_bool("is_private")

        if not name:
            name = "Untitled Protocol"

        template = ProtocolTemplate(name=name, description=description)

        _pf67_patch009_set_if_hasattr(template, "category", category)
        _pf67_patch009_set_if_hasattr(template, "is_private", is_private)

        db.session.add(template)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    return render_template("new_protocol_patch009.html")


@views_bp.route("/protocols/<int:template_id>/privacy-patch009", methods=["POST"])
@_pf67_patch009_edit_required
def pf67_patch009_update_protocol_privacy(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    is_private = _pf67_patch009_form_bool("is_private")
    _pf67_patch009_set_if_hasattr(template, "is_private", is_private)
    db.session.commit()
    return redirect(url_for("views.template_detail", template_id=template.id))


@views_bp.route("/protocols/<int:template_id>/start-flow-patch009", methods=["POST"])
@_pf67_patch009_edit_required
def pf67_patch009_start_flow(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    job_name = (request.form.get("job_name") or "").strip()
    priority = _pf67_patch009_normalize_priority(request.form.get("priority"))
    start_mode = (request.form.get("start_mode") or "now").strip()
    selected_time = _pf67_patch009_parse_datetime_local(request.form.get("first_step_at"))

    if start_mode == "now":
        first_step_anchor = _pf67_009_datetime.utcnow()
    else:
        first_step_anchor = selected_time or _pf67_009_datetime.utcnow()

    is_private = _pf67_patch009_form_bool("is_private") or bool(getattr(template, "is_private", False))
    job = _pf67_patch009_create_flow_from_template(template, job_name, first_step_anchor, priority, is_private)

    try:
        return redirect(url_for("views.job_detail", job_id=job.id))
    except Exception:
        return redirect(url_for("views.jobs_page", view="active"))


@views_bp.route("/public")
def pf67_patch009g_public_dashboard():
    return render_template("public_dashboard_patch009g.html")

# === PF67 PATCH 009B FLOW PRIVACY EDITOR ===
from flask import request, redirect, url_for
from .models import db, Job


try:
    _pf67_patch009b_edit_required = edit_required
except NameError:
    def _pf67_patch009b_edit_required(func):
        return func


def _pf67_patch009b_form_bool(name):
    return str(request.form.get(name, "")).lower() in ["1", "true", "yes", "on"]


@views_bp.route("/flows/<int:job_id>/privacy-patch009b", methods=["POST"])
@_pf67_patch009b_edit_required
def pf67_patch009b_update_flow_privacy(job_id):
    job = Job.query.get_or_404(job_id)

    if hasattr(job, "is_private"):
        job.is_private = _pf67_patch009b_form_bool("is_private")
        db.session.commit()

    try:
        return redirect(url_for("views.job_detail", job_id=job.id))
    except Exception:
        return redirect(url_for("views.jobs_page", view="active"))

# === PF67 PATCH 009C PUBLIC PROTOCOL VISIBILITY ===
from flask import render_template, url_for
from .models import ProtocolTemplate, Job


def _pf67_patch009c_public_protocol_list():
    rows = (
        ProtocolTemplate.query
        .order_by(ProtocolTemplate.name.asc())
        .all()
    )

    return [template for template in rows if not getattr(template, "is_private", False)]


def _pf67_patch009c_public_flow_items():
    try:
        rows = (
            Job.query
            .filter(Job.status.in_(["active", "pending"]))
            .order_by(Job.started_at.desc(), Job.id.desc())
            .all()
        )
    except Exception:
        rows = Job.query.order_by(Job.id.desc()).all()

    public_flows = []

    for job in rows:
        try:
            is_public = _pf67_patch009_job_is_public(job)
        except Exception:
            is_public = not getattr(job, "is_private", False)

        if not is_public:
            continue

        try:
            step = _pf67_patch009_current_open_step(job)
        except Exception:
            step = None

        try:
            status = _pf67_patch009_step_status(step)
        except Exception:
            status = "active"

        try:
            status_label_value = _pf67_patch009_status_label(status)
        except Exception:
            status_label_value = (status or "active").replace("_", " ").title()

        try:
            time_label_value = _pf67_patch009_time_label(step)
        except Exception:
            time_label_value = ""

        try:
            estimated_value = _pf67_patch009_estimated_completion(job)
        except Exception:
            estimated_value = ""

        template = getattr(job, "template", None)

        if template is not None and not getattr(template, "is_private", False):
            try:
                url = url_for("views.pf67_patch009c_public_protocol_detail", template_id=template.id)
            except Exception:
                url = "#"
        else:
            url = "#"

        public_flows.append({
            "job": job,
            "step": step,
            "status": status,
            "status_label": status_label_value,
            "time_label": time_label_value,
            "estimated_completion": estimated_value,
            "url": url,
        })

    return public_flows


@views_bp.route("/public/protocols")
def pf67_patch009g_public_protocols():
    return render_template("public_protocols_patch009g.html")

@views_bp.route("/public/protocols/<int:template_id>")
def pf67_patch009g_public_protocol_detail(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if getattr(template, "is_private", False):
        return render_template("public_protocols_patch009g.html"), 404

    return render_template(
        "public_protocol_detail_patch009g.html",
        template=template,
    )

# === PF67 PATCH 009D VISIBILITY PILL ROUTES ===
from flask import request, redirect, url_for
from .models import db, ProtocolTemplate, Job


try:
    _pf67_patch009d_edit_required = edit_required
except NameError:
    def _pf67_patch009d_edit_required(func):
        return func


def _pf67_patch009d_form_bool(name):
    return str(request.form.get(name, "")).lower() in ["1", "true", "yes", "on"]


def _pf67_patch009d_public_protocol_list():
    try:
        return _pf67_patch009c_public_protocol_list()
    except Exception:
        rows = ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        return [template for template in rows if not getattr(template, "is_private", False)]


@views_bp.route("/protocols/<int:template_id>/visibility-toggle-patch009d", methods=["POST"])
@_pf67_patch009d_edit_required
def pf67_patch009d_toggle_protocol_visibility(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if hasattr(template, "is_private"):
        template.is_private = _pf67_patch009d_form_bool("is_private")
        db.session.commit()

    return redirect(request.referrer or url_for("views.templates_page"))


@views_bp.route("/flows/<int:job_id>/visibility-toggle-patch009d", methods=["POST"])
@_pf67_patch009d_edit_required
def pf67_patch009d_toggle_flow_visibility(job_id):
    job = Job.query.get_or_404(job_id)

    if hasattr(job, "is_private"):
        job.is_private = _pf67_patch009d_form_bool("is_private")
        db.session.commit()

    try:
        return redirect(request.referrer or url_for("views.job_detail", job_id=job.id))
    except Exception:
        return redirect(url_for("views.jobs_page", view="active"))

# === PF67 PATCH 009E PUBLIC DASHBOARD AND PROGRESS ===
from datetime import datetime as _pf67_009e_datetime
from flask import render_template, request, redirect, url_for
from .models import db, ProtocolTemplate, Job


def _pf67_patch009e_public_protocol_list():
    rows = ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
    return [template for template in rows if not getattr(template, "is_private", False)]


def _pf67_patch009e_job_is_public(job):
    if getattr(job, "is_private", False):
        return False

    template = getattr(job, "template", None)

    if template is not None and getattr(template, "is_private", False):
        return False

    return True


def _pf67_patch009e_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009e_status_label(status):
    try:
        return status_label(status)
    except Exception:
        return (status or "active").replace("_", " ").title()


def _pf67_patch009e_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009e_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009e_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009e_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            from datetime import timedelta
            times.append(anchor + timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return ""

    value = max(times)

    try:
        return display_short_datetime(value)
    except Exception:
        return value.strftime("%Y-%m-%d %I:%M %p")


def _pf67_patch009e_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009e_datetime.utcnow()
    started = getattr(job, "started_at", None)
    completion_candidates = []

    for step in steps:
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)
        completed = getattr(step, "completed_at", None)

        if completed:
            completion_candidates.append(completed)
        elif anchor and ideal is not None:
            from datetime import timedelta
            completion_candidates.append(anchor + timedelta(minutes=ideal))
        elif anchor:
            completion_candidates.append(anchor)

    if started and completion_candidates:
        end = max(completion_candidates)

        if end and end > started:
            total = (end - started).total_seconds()
            elapsed = (now - started).total_seconds()
            percent = int(round(max(0, min(100, (elapsed / total) * 100))))
            return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009e_public_flow_items():
    try:
        rows = (
            Job.query
            .filter(Job.status.in_(["active", "pending"]))
            .order_by(Job.started_at.desc(), Job.id.desc())
            .all()
        )
    except Exception:
        rows = Job.query.order_by(Job.id.desc()).all()

    public_flows = []

    for job in rows:
        if not _pf67_patch009e_job_is_public(job):
            continue

        step = _pf67_patch009e_current_open_step(job)
        status = _pf67_patch009e_step_status(step)
        progress_percent, progress_basis = _pf67_patch009e_progress(job)
        template = getattr(job, "template", None)

        if template is not None and not getattr(template, "is_private", False):
            try:
                url = url_for("views.pf67_patch009c_public_protocol_detail", template_id=template.id)
            except Exception:
                url = "#"
        else:
            url = "#"

        public_flows.append({
            "job": job,
            "step": step,
            "status": status,
            "status_label": _pf67_patch009e_status_label(status),
            "time_label": _pf67_patch009e_time_label(step),
            "estimated_completion": _pf67_patch009e_estimated_completion(job),
            "progress_percent": progress_percent,
            "progress_basis": progress_basis,
            "url": url,
        })

    return public_flows


@views_bp.route("/public/flows")
def pf67_patch009g_public_flows():
    return render_template("public_flows_list_patch009g.html")

# === PF67 PATCH 009F PUBLIC ROUTE FIX HELPERS ===
from datetime import datetime as _pf67_009f_datetime, timedelta as _pf67_009f_timedelta
from flask import render_template, request, redirect, url_for
from .models import db, ProtocolTemplate, Job


def _pf67_patch009f_public_protocol_list():
    rows = ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
    return [template for template in rows if not getattr(template, "is_private", False)]


def _pf67_patch009f_job_template(job):
    template = getattr(job, "template", None)

    if template is not None:
        return template

    template_id = getattr(job, "template_id", None)

    if template_id:
        try:
            return ProtocolTemplate.query.get(template_id)
        except Exception:
            return None

    return None


def _pf67_patch009f_job_is_public(job):
    if bool(getattr(job, "is_private", False)):
        return False

    template = _pf67_patch009f_job_template(job)

    if template is not None and bool(getattr(template, "is_private", False)):
        return False

    return True


def _pf67_patch009f_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009f_status_label(status):
    try:
        return status_label(status)
    except Exception:
        return (status or "active").replace("_", " ").title()


def _pf67_patch009f_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009f_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009f_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009f_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009f_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return ""

    value = max(times)

    try:
        return display_short_datetime(value)
    except Exception:
        return value.strftime("%Y-%m-%d %I:%M %p")


def _pf67_patch009f_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009f_datetime.utcnow()
    started = getattr(job, "started_at", None)
    completion_candidates = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            completion_candidates.append(completed)
        elif anchor and ideal is not None:
            completion_candidates.append(anchor + _pf67_009f_timedelta(minutes=ideal))
        elif anchor:
            completion_candidates.append(anchor)

    if started and completion_candidates:
        end = max(completion_candidates)

        if end and end > started:
            total = (end - started).total_seconds()
            elapsed = (now - started).total_seconds()
            percent = int(round(max(0, min(100, (elapsed / total) * 100))))
            return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009f_flow_group(job):
    status = (getattr(job, "status", "") or "").lower()
    started = getattr(job, "started_at", None)
    now = _pf67_009f_datetime.utcnow()

    if status in ["completed", "archived", "done"]:
        return "completed", "Completed"

    if started and started > now:
        return "scheduled", "Scheduled"

    if status in ["pending", "scheduled"]:
        return "scheduled", "Scheduled"

    return "active", "Active"


def _pf67_patch009f_flow_url(job):
    template = _pf67_patch009f_job_template(job)

    if template is not None and not getattr(template, "is_private", False):
        try:
            return url_for("views.pf67_patch009f_public_protocol_detail", template_id=template.id)
        except Exception:
            pass

    return "#"


def _pf67_patch009f_public_flow_items():
    try:
        rows = (
            Job.query
            .order_by(Job.started_at.desc(), Job.id.desc())
            .all()
        )
    except Exception:
        rows = Job.query.order_by(Job.id.desc()).all()

    active = []
    scheduled = []
    completed = []

    for job in rows:
        if not _pf67_patch009f_job_is_public(job):
            continue

        step = _pf67_patch009f_current_open_step(job)
        status = _pf67_patch009f_step_status(step)
        progress_percent, progress_basis = _pf67_patch009f_progress(job)
        group_key, group_label = _pf67_patch009f_flow_group(job)

        item = {
            "job": job,
            "step": step,
            "status": status,
            "status_label": _pf67_patch009f_status_label(status),
            "time_label": _pf67_patch009f_time_label(step),
            "estimated_completion": _pf67_patch009f_estimated_completion(job),
            "progress_percent": progress_percent,
            "progress_basis": progress_basis,
            "group_key": group_key,
            "group_label": group_label,
            "url": _pf67_patch009f_flow_url(job),
        }

        if group_key == "completed":
            completed.append(item)
        elif group_key == "scheduled":
            scheduled.append(item)
        else:
            active.append(item)

    return active, scheduled, completed

# === PF67 PATCH 009G PUBLIC ROOT HELPERS ===
from datetime import datetime as _pf67_009g_datetime, timedelta as _pf67_009g_timedelta
from flask import render_template, request, redirect, url_for
from .models import db, ProtocolTemplate, Job


@views_bp.app_template_global("pf67_patch009g_public_protocol_list")
def pf67_patch009g_public_protocol_list():
    rows = ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
    return [template for template in rows if not bool(getattr(template, "is_private", False))]


def _pf67_patch009g_job_template(job):
    template = getattr(job, "template", None)

    if template is not None:
        return template

    template_id = getattr(job, "template_id", None)

    if template_id:
        try:
            return ProtocolTemplate.query.get(template_id)
        except Exception:
            return None

    return None


def _pf67_patch009g_job_is_public(job):
    if bool(getattr(job, "is_private", False)):
        return False

    template = _pf67_patch009g_job_template(job)

    if template is not None and bool(getattr(template, "is_private", False)):
        return False

    return True


def _pf67_patch009g_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009g_status_label(status):
    try:
        return status_label(status)
    except Exception:
        return (status or "active").replace("_", " ").title()


def _pf67_patch009g_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009g_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009g_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009g_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009g_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return ""

    value = max(times)

    try:
        return display_short_datetime(value)
    except Exception:
        return value.strftime("%Y-%m-%d %I:%M %p")


def _pf67_patch009g_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009g_datetime.utcnow()
    started = getattr(job, "started_at", None)
    completion_candidates = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            completion_candidates.append(completed)
        elif anchor and ideal is not None:
            completion_candidates.append(anchor + _pf67_009g_timedelta(minutes=ideal))
        elif anchor:
            completion_candidates.append(anchor)

    if started and completion_candidates:
        end = max(completion_candidates)

        if end and end > started:
            total = (end - started).total_seconds()
            elapsed = (now - started).total_seconds()
            percent = int(round(max(0, min(100, (elapsed / total) * 100))))
            return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009g_flow_group(job):
    status = (getattr(job, "status", "") or "").lower()
    started = getattr(job, "started_at", None)
    now = _pf67_009g_datetime.utcnow()

    if status in ["completed", "complete", "archived", "done"]:
        return "completed", "Completed"

    if started and started > now:
        return "scheduled", "Scheduled"

    if status in ["pending", "scheduled"]:
        return "scheduled", "Scheduled"

    return "active", "Active"


def _pf67_patch009g_flow_url(job):
    template = _pf67_patch009g_job_template(job)

    if template is not None and not getattr(template, "is_private", False):
        try:
            return url_for("views.pf67_patch009g_public_protocol_detail", template_id=template.id)
        except Exception:
            pass

    return "#"


def _pf67_patch009g_public_flow_groups():
    try:
        rows = (
            Job.query
            .order_by(Job.started_at.desc(), Job.id.desc())
            .all()
        )
    except Exception:
        rows = Job.query.order_by(Job.id.desc()).all()

    active = []
    scheduled = []
    completed = []

    for job in rows:
        if not _pf67_patch009g_job_is_public(job):
            continue

        step = _pf67_patch009g_current_open_step(job)
        status = _pf67_patch009g_step_status(step)
        progress_percent, progress_basis = _pf67_patch009g_progress(job)
        group_key, group_label = _pf67_patch009g_flow_group(job)

        item = {
            "job": job,
            "step": step,
            "status": status,
            "status_label": _pf67_patch009g_status_label(status),
            "time_label": _pf67_patch009g_time_label(step),
            "estimated_completion": _pf67_patch009g_estimated_completion(job),
            "progress_percent": progress_percent,
            "progress_basis": progress_basis,
            "group_key": group_key,
            "group_label": group_label,
            "url": _pf67_patch009g_flow_url(job),
        }

        if group_key == "completed":
            completed.append(item)
        elif group_key == "scheduled":
            scheduled.append(item)
        else:
            active.append(item)

    return active, scheduled, completed


@views_bp.app_template_global("pf67_patch009g_public_dashboard_data")
def pf67_patch009g_public_dashboard_data():
    active_flows, scheduled_flows, completed_flows = _pf67_patch009g_public_flow_groups()

    return {
        "active_flows": active_flows,
        "scheduled_flows": scheduled_flows,
        "completed_flows": completed_flows,
        "public_protocols": pf67_patch009g_public_protocol_list(),
    }

# === PF67 PATCH 009I ROBUST PROTOCOL THUMBNAILS ===
import re as _pf67_009i_re
import html as _pf67_009i_html


def _pf67_patch009i_find_first_img_src(value):
    html_value = value or ""

    if not html_value:
        return ""

    # The lightweight editor can leave normal HTML, while old previews/backups can leave escaped HTML.
    candidates = [
        str(html_value),
        _pf67_009i_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_009i_re.search(pattern, candidate, flags=_pf67_009i_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_009i_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch009i_first_img_src")
def pf67_patch009i_first_img_src(value):
    return _pf67_patch009i_find_first_img_src(value)


# Re-register the older 008B filter name too, because the Protocols list already calls it.
@views_bp.app_template_filter("pf67_patch008b_first_img_src")
def pf67_patch008b_first_img_src_009i(value):
    return _pf67_patch009i_find_first_img_src(value)


@views_bp.app_template_filter("pf67_patch009i_plain_summary")
def pf67_patch009i_plain_summary(value, max_length=240):
    html_value = _pf67_009i_html.unescape(str(value or ""))

    if not html_value:
        return ""

    html_value = _pf67_009i_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_009i_re.IGNORECASE | _pf67_009i_re.DOTALL,
    )

    html_value = _pf67_009i_re.sub(
        r"<img\b[^>]*>",
        " ",
        html_value,
        flags=_pf67_009i_re.IGNORECASE,
    )

    text_value = _pf67_009i_re.sub(r"<[^>]+>", " ", html_value)
    text_value = _pf67_009i_html.unescape(text_value)
    text_value = _pf67_009i_re.sub(r"\s+", " ", text_value).strip()

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 240

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "…"

    return text_value

# === PF67 PATCH 009J BACKEND FLOWS CONSOLE ===
from datetime import datetime as _pf67_009j_datetime, timedelta as _pf67_009j_timedelta
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate, Job


try:
    _pf67_patch009j_edit_required = edit_required
except NameError:
    def _pf67_patch009j_edit_required(func):
        return func


def _pf67_patch009j_form_bool(name):
    return str(request.form.get(name, "")).lower() in ["1", "true", "yes", "on"]


def _pf67_patch009j_job_template(job):
    template = getattr(job, "template", None)

    if template is not None:
        return template

    template_id = getattr(job, "template_id", None)

    if template_id:
        try:
            return ProtocolTemplate.query.get(template_id)
        except Exception:
            return None

    return None


def _pf67_patch009j_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009j_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009j_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009j_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009j_short_datetime(value):
    if not value:
        return ""

    try:
        return display_short_datetime(value)
    except Exception:
        try:
            return value.strftime("%Y-%m-%d %I:%M %p")
        except Exception:
            return str(value)


def _pf67_patch009j_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009j_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return None

    return max(times)


def _pf67_patch009j_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009j_datetime.utcnow()
    started = getattr(job, "started_at", None)
    end = _pf67_patch009j_estimated_completion(job)

    if started and end and end > started:
        total = (end - started).total_seconds()
        elapsed = (now - started).total_seconds()
        percent = int(round(max(0, min(100, (elapsed / total) * 100))))
        return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009j_flow_group(job):
    status = (getattr(job, "status", "") or "").lower()
    started = getattr(job, "started_at", None)
    now = _pf67_009j_datetime.utcnow()

    if status in ["completed", "complete", "archived", "done"]:
        return "completed", "Completed"

    if started and started > now:
        return "scheduled", "Scheduled"

    if status in ["pending", "scheduled"]:
        return "scheduled", "Scheduled"

    return "active", "Active"


def _pf67_patch009j_priority_rank(priority):
    priority = (priority or "Normal").strip().lower()

    if priority == "critical":
        return 4

    if priority == "high":
        return 3

    if priority == "normal":
        return 2

    if priority == "low":
        return 1

    return 2


def _pf67_patch009j_item(job):
    template = _pf67_patch009j_job_template(job)
    current_step = _pf67_patch009j_current_open_step(job)
    progress_percent, progress_basis = _pf67_patch009j_progress(job)
    estimated_completion = _pf67_patch009j_estimated_completion(job)
    group_key, group_label = _pf67_patch009j_flow_group(job)
    priority = getattr(job, "priority", None) or "Normal"

    try:
        detail_url = url_for("views.job_detail", job_id=job.id)
    except Exception:
        try:
            detail_url = url_for("views.jobs_page")
        except Exception:
            detail_url = "#"

    return {
        "job": job,
        "template": template,
        "current_step": current_step,
        "group_key": group_key,
        "group_label": group_label,
        "priority": priority,
        "priority_rank": _pf67_patch009j_priority_rank(priority),
        "is_private": bool(getattr(job, "is_private", False)),
        "progress_percent": progress_percent,
        "progress_basis": progress_basis,
        "started_value": getattr(job, "started_at", None),
        "started_display": _pf67_patch009j_short_datetime(getattr(job, "started_at", None)),
        "estimated_value": estimated_completion,
        "estimated_completion": _pf67_patch009j_short_datetime(estimated_completion),
        "time_label": _pf67_patch009j_time_label(current_step),
        "url": detail_url,
        "search_text": (
            (getattr(job, "name", "") or "") + " " +
            ((getattr(template, "name", "") or "") if template is not None else "")
        ).lower(),
    }


def _pf67_patch009j_apply_filters(items, filters):
    result = []

    for item in items:
        status_filter = filters["status"]
        visibility_filter = filters["visibility"]
        priority_filter = filters["priority"]
        query = filters["q"].strip().lower()

        if status_filter != "all" and item["group_key"] != status_filter:
            continue

        if visibility_filter == "public" and item["is_private"]:
            continue

        if visibility_filter == "private" and not item["is_private"]:
            continue

        if priority_filter != "all" and item["priority"].strip().lower() != priority_filter:
            continue

        if query and query not in item["search_text"]:
            continue

        result.append(item)

    return result


def _pf67_patch009j_sort_items(items, sort_value):
    if sort_value == "started_asc":
        return sorted(items, key=lambda item: item["started_value"] or _pf67_009j_datetime.min)

    if sort_value == "estimated":
        return sorted(items, key=lambda item: item["estimated_value"] or _pf67_009j_datetime.max)

    if sort_value == "priority":
        return sorted(items, key=lambda item: (item["priority_rank"], item["started_value"] or _pf67_009j_datetime.min), reverse=True)

    if sort_value == "progress":
        return sorted(items, key=lambda item: item["progress_percent"], reverse=True)

    if sort_value == "name":
        return sorted(items, key=lambda item: (item["job"].name or "").lower())

    return sorted(items, key=lambda item: item["started_value"] or _pf67_009j_datetime.min, reverse=True)


@views_bp.route("/flows-console-patch009j")
@_pf67_patch009j_edit_required
def pf67_patch009j_flows_console():
    filters = {
        "status": (request.args.get("status") or "active").strip().lower(),
        "visibility": (request.args.get("visibility") or "all").strip().lower(),
        "priority": (request.args.get("priority") or "all").strip().lower(),
        "sort": (request.args.get("sort") or "started_desc").strip().lower(),
        "q": (request.args.get("q") or "").strip(),
    }

    valid_statuses = {"all", "active", "scheduled", "completed"}
    valid_visibility = {"all", "public", "private"}
    valid_priority = {"all", "critical", "high", "normal", "low"}
    valid_sorts = {"started_desc", "started_asc", "estimated", "priority", "progress", "name"}

    if filters["status"] not in valid_statuses:
        filters["status"] = "active"

    if filters["visibility"] not in valid_visibility:
        filters["visibility"] = "all"

    if filters["priority"] not in valid_priority:
        filters["priority"] = "all"

    if filters["sort"] not in valid_sorts:
        filters["sort"] = "started_desc"

    rows = Job.query.order_by(Job.id.desc()).all()
    all_items = [_pf67_patch009j_item(job) for job in rows]

    counts = {
        "active": len([item for item in all_items if item["group_key"] == "active"]),
        "scheduled": len([item for item in all_items if item["group_key"] == "scheduled"]),
        "completed": len([item for item in all_items if item["group_key"] == "completed"]),
        "public": len([item for item in all_items if not item["is_private"]]),
        "private": len([item for item in all_items if item["is_private"]]),
    }

    filtered = _pf67_patch009j_apply_filters(all_items, filters)
    filtered = _pf67_patch009j_sort_items(filtered, filters["sort"])

    status_options = [
        ("active", "Active"),
        ("scheduled", "Scheduled"),
        ("completed", "Completed"),
        ("all", "All"),
    ]

    visibility_options = [
        ("all", "All"),
        ("public", "Public"),
        ("private", "Private"),
    ]

    priority_options = [
        ("all", "All"),
        ("critical", "Critical"),
        ("high", "High"),
        ("normal", "Normal"),
        ("low", "Low"),
    ]

    sort_options = [
        ("started_desc", "Started Date Descending"),
        ("started_asc", "Started Date Ascending"),
        ("estimated", "Estimated Completion"),
        ("priority", "Priority"),
        ("progress", "Progress"),
        ("name", "Name"),
    ]

    return render_template(
        "flows_console_patch009j.html",
        flows=filtered,
        filters=filters,
        counts=counts,
        total_count=len(filtered),
        status_options=status_options,
        visibility_options=visibility_options,
        priority_options=priority_options,
        sort_options=sort_options,
    )


@views_bp.route("/flows/<int:job_id>/visibility-toggle-patch009j", methods=["POST"])
@_pf67_patch009j_edit_required
def pf67_patch009j_toggle_flow_visibility(job_id):
    job = Job.query.get_or_404(job_id)

    if hasattr(job, "is_private"):
        job.is_private = _pf67_patch009j_form_bool("is_private")
        db.session.commit()

    return redirect(request.referrer or url_for("views.pf67_patch009j_flows_console"))

# === PF67 PATCH 009K PUBLIC PROTOCOL THUMBNAILS ===
import re as _pf67_009k_re
import html as _pf67_009k_html


def _pf67_patch009k_find_first_img_src(value):
    html_value = value or ""

    if not html_value:
        return ""

    candidates = [
        str(html_value),
        _pf67_009k_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_009k_re.search(pattern, candidate, flags=_pf67_009k_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_009k_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch009k_first_img_src")
def pf67_patch009k_first_img_src(value):
    return _pf67_patch009k_find_first_img_src(value)


@views_bp.app_template_filter("pf67_patch009k_plain_summary")
def pf67_patch009k_plain_summary(value, max_length=240):
    html_value = _pf67_009k_html.unescape(str(value or ""))

    if not html_value:
        return ""

    html_value = _pf67_009k_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_009k_re.IGNORECASE | _pf67_009k_re.DOTALL,
    )

    html_value = _pf67_009k_re.sub(
        r"<img\b[^>]*>",
        " ",
        html_value,
        flags=_pf67_009k_re.IGNORECASE,
    )

    text_value = _pf67_009k_re.sub(r"<[^>]+>", " ", html_value)
    text_value = _pf67_009k_html.unescape(text_value)
    text_value = _pf67_009k_re.sub(r"\s+", " ", text_value).strip()

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 240

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "…"

    return text_value

# === PF67 PATCH 009L BACKEND FLOW FILTER POLISH ===
from datetime import datetime as _pf67_009l_datetime, timedelta as _pf67_009l_timedelta
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate, Job


try:
    _pf67_patch009l_edit_required = edit_required
except NameError:
    def _pf67_patch009l_edit_required(func):
        return func


def _pf67_patch009l_job_template(job):
    template = getattr(job, "template", None)

    if template is not None:
        return template

    template_id = getattr(job, "template_id", None)

    if template_id:
        try:
            return ProtocolTemplate.query.get(template_id)
        except Exception:
            return None

    return None


def _pf67_patch009l_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009l_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009l_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009l_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009l_short_datetime(value):
    if not value:
        return ""

    try:
        return display_short_datetime(value)
    except Exception:
        try:
            return value.strftime("%Y-%m-%d %I:%M %p")
        except Exception:
            return str(value)


def _pf67_patch009l_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009l_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return None

    return max(times)


def _pf67_patch009l_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009l_datetime.utcnow()
    started = getattr(job, "started_at", None)
    end = _pf67_patch009l_estimated_completion(job)

    if started and end and end > started:
        total = (end - started).total_seconds()
        elapsed = (now - started).total_seconds()
        percent = int(round(max(0, min(100, (elapsed / total) * 100))))
        return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009l_flow_group(job):
    status = (getattr(job, "status", "") or "").lower()
    started = getattr(job, "started_at", None)
    now = _pf67_009l_datetime.utcnow()

    if status in ["completed", "complete", "archived", "done"]:
        return "completed", "Completed"

    if started and started > now:
        return "scheduled", "Scheduled"

    if status in ["pending", "scheduled"]:
        return "scheduled", "Scheduled"

    return "active", "Active"


def _pf67_patch009l_priority_rank(priority):
    priority = (priority or "Normal").strip().lower()

    if priority == "critical":
        return 4

    if priority == "high":
        return 3

    if priority == "normal":
        return 2

    if priority == "low":
        return 1

    return 2


def _pf67_patch009l_item(job):
    template = _pf67_patch009l_job_template(job)
    current_step = _pf67_patch009l_current_open_step(job)
    progress_percent, progress_basis = _pf67_patch009l_progress(job)
    estimated_completion = _pf67_patch009l_estimated_completion(job)
    group_key, group_label = _pf67_patch009l_flow_group(job)
    priority = getattr(job, "priority", None) or "Normal"

    try:
        detail_url = url_for("views.job_detail", job_id=job.id)
    except Exception:
        try:
            detail_url = url_for("views.jobs_page")
        except Exception:
            detail_url = "#"

    return {
        "job": job,
        "template": template,
        "current_step": current_step,
        "group_key": group_key,
        "group_label": group_label,
        "priority": priority,
        "priority_value": priority.strip().lower(),
        "priority_rank": _pf67_patch009l_priority_rank(priority),
        "is_private": bool(getattr(job, "is_private", False)),
        "progress_percent": progress_percent,
        "progress_basis": progress_basis,
        "started_value": getattr(job, "started_at", None),
        "started_display": _pf67_patch009l_short_datetime(getattr(job, "started_at", None)),
        "estimated_value": estimated_completion,
        "estimated_completion": _pf67_patch009l_short_datetime(estimated_completion),
        "time_label": _pf67_patch009l_time_label(current_step),
        "url": detail_url,
        "search_text": (
            (getattr(job, "name", "") or "") + " " +
            ((getattr(template, "name", "") or "") if template is not None else "")
        ).lower(),
    }


def _pf67_patch009l_selected_list(name, allowed_values, default_values):
    values = request.args.getlist(name)

    # Compatibility with older single-value query string such as ?status=active.
    single = request.args.get(name)

    if single and single not in values:
        values.append(single)

    cleaned = []

    for value in values:
        value = (value or "").strip().lower()

        if value in allowed_values and value not in cleaned:
            cleaned.append(value)

    if not cleaned:
        cleaned = list(default_values)

    if "all" in cleaned:
        cleaned = ["all"]

    return cleaned


def _pf67_patch009l_label_for_values(values, labels, all_label):
    if "all" in values:
        return all_label

    selected_labels = [labels.get(value, value.title()) for value in values]

    if len(selected_labels) == 1:
        return selected_labels[0]

    if len(selected_labels) == 2:
        return selected_labels[0] + " + " + selected_labels[1]

    return str(len(selected_labels)) + " selected"


def _pf67_patch009l_apply_filters(items, filters):
    result = []

    status_values = filters["status_values"]
    priority_values = filters["priority_values"]

    for item in items:
        visibility_filter = filters["visibility"]
        query = filters["q"].strip().lower()

        if "all" not in status_values and item["group_key"] not in status_values:
            continue

        if visibility_filter == "public" and item["is_private"]:
            continue

        if visibility_filter == "private" and not item["is_private"]:
            continue

        if "all" not in priority_values and item["priority_value"] not in priority_values:
            continue

        if query and query not in item["search_text"]:
            continue

        result.append(item)

    return result


def _pf67_patch009l_sort_items(items, sort_value):
    if sort_value == "started_asc":
        return sorted(items, key=lambda item: item["started_value"] or _pf67_009l_datetime.min)

    if sort_value == "estimated":
        return sorted(items, key=lambda item: item["estimated_value"] or _pf67_009l_datetime.max)

    if sort_value == "priority":
        return sorted(items, key=lambda item: (item["priority_rank"], item["started_value"] or _pf67_009l_datetime.min), reverse=True)

    if sort_value == "progress":
        return sorted(items, key=lambda item: item["progress_percent"], reverse=True)

    if sort_value == "name":
        return sorted(items, key=lambda item: (item["job"].name or "").lower())

    return sorted(items, key=lambda item: item["started_value"] or _pf67_009l_datetime.min, reverse=True)


@views_bp.route("/flows-console-patch009l")
@_pf67_patch009l_edit_required
def pf67_patch009l_flows_console():
    status_labels = {
        "active": "Active",
        "scheduled": "Scheduled",
        "completed": "Completed",
        "all": "All",
    }

    priority_labels = {
        "critical": "Critical",
        "high": "High",
        "normal": "Normal",
        "low": "Low",
        "all": "All",
    }

    filters = {
        "status_values": _pf67_patch009l_selected_list("status", {"active", "scheduled", "completed", "all"}, ["active"]),
        "visibility": (request.args.get("visibility") or "all").strip().lower(),
        "priority_values": _pf67_patch009l_selected_list("priority", {"critical", "high", "normal", "low", "all"}, ["all"]),
        "sort": (request.args.get("sort") or "started_desc").strip().lower(),
        "q": (request.args.get("q") or "").strip(),
    }

    valid_visibility = {"all", "public", "private"}
    valid_sorts = {"started_desc", "started_asc", "estimated", "priority", "progress", "name"}

    if filters["visibility"] not in valid_visibility:
        filters["visibility"] = "all"

    if filters["sort"] not in valid_sorts:
        filters["sort"] = "started_desc"

    rows = Job.query.order_by(Job.id.desc()).all()
    all_items = [_pf67_patch009l_item(job) for job in rows]

    counts = {
        "active": len([item for item in all_items if item["group_key"] == "active"]),
        "scheduled": len([item for item in all_items if item["group_key"] == "scheduled"]),
        "completed": len([item for item in all_items if item["group_key"] == "completed"]),
        "public": len([item for item in all_items if not item["is_private"]]),
        "private": len([item for item in all_items if item["is_private"]]),
    }

    filtered = _pf67_patch009l_apply_filters(all_items, filters)
    filtered = _pf67_patch009l_sort_items(filtered, filters["sort"])

    status_options = [
        ("active", "Active"),
        ("scheduled", "Scheduled"),
        ("completed", "Completed"),
        ("all", "All"),
    ]

    visibility_options = [
        ("all", "All"),
        ("public", "Public"),
        ("private", "Private"),
    ]

    priority_options = [
        ("critical", "Critical"),
        ("high", "High"),
        ("normal", "Normal"),
        ("low", "Low"),
        ("all", "All"),
    ]

    sort_options = [
        ("started_desc", "Started Date Descending"),
        ("started_asc", "Started Date Ascending"),
        ("estimated", "Estimated Completion"),
        ("priority", "Priority"),
        ("progress", "Progress"),
        ("name", "Name"),
    ]

    filter_labels = {
        "status": _pf67_patch009l_label_for_values(filters["status_values"], status_labels, "All Statuses"),
        "priority": _pf67_patch009l_label_for_values(filters["priority_values"], priority_labels, "All Priorities"),
    }

    return render_template(
        "flows_console_patch009l.html",
        flows=filtered,
        filters=filters,
        filter_labels=filter_labels,
        counts=counts,
        total_count=len(filtered),
        status_options=status_options,
        visibility_options=visibility_options,
        priority_options=priority_options,
        sort_options=sort_options,
    )

# === PF67 PATCH 009M INSTANT BACKEND FLOW FILTERS ===
from datetime import datetime as _pf67_009m_datetime, timedelta as _pf67_009m_timedelta
from flask import url_for, render_template
from .models import ProtocolTemplate, Job


try:
    _pf67_patch009m_edit_required = edit_required
except NameError:
    def _pf67_patch009m_edit_required(func):
        return func


def _pf67_patch009m_job_template(job):
    template = getattr(job, "template", None)

    if template is not None:
        return template

    template_id = getattr(job, "template_id", None)

    if template_id:
        try:
            return ProtocolTemplate.query.get(template_id)
        except Exception:
            return None

    return None


def _pf67_patch009m_current_open_step(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    for step in steps:
        if not getattr(step, "completed_at", None):
            return step

    return None


def _pf67_patch009m_step_status(step):
    if step is None:
        return "active"

    try:
        return calculate_step_status(step)
    except Exception:
        return "waiting"


def _pf67_patch009m_time_label(step):
    if step is None:
        return ""

    try:
        return step_compact_time_label(step, _pf67_patch009m_step_status(step)) or ""
    except Exception:
        anchor = getattr(step, "anchor_time", None)
        if anchor:
            try:
                return anchor.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                return ""

    return ""


def _pf67_patch009m_short_datetime(value):
    if not value:
        return ""

    try:
        return display_short_datetime(value)
    except Exception:
        try:
            return value.strftime("%Y-%m-%d %I:%M %p")
        except Exception:
            return str(value)


def _pf67_patch009m_timestamp_ms(value, fallback=0):
    if not value:
        return fallback

    try:
        return int(value.timestamp() * 1000)
    except Exception:
        return fallback


def _pf67_patch009m_estimated_completion(job):
    steps = list(getattr(job, "steps", []) or [])
    times = []

    for step in steps:
        completed = getattr(step, "completed_at", None)
        anchor = getattr(step, "anchor_time", None)
        ideal = getattr(step, "ideal_minutes", None)

        if completed:
            times.append(completed)
        elif anchor and ideal is not None:
            times.append(anchor + _pf67_009m_timedelta(minutes=ideal))
        elif anchor:
            times.append(anchor)

    if not times:
        return None

    return max(times)


def _pf67_patch009m_progress(job):
    steps = list(getattr(job, "steps", []) or [])
    steps.sort(key=lambda step: (step.sort_order or 0, step.id or 0))

    now = _pf67_009m_datetime.utcnow()
    started = getattr(job, "started_at", None)
    end = _pf67_patch009m_estimated_completion(job)

    if started and end and end > started:
        total = (end - started).total_seconds()
        elapsed = (now - started).total_seconds()
        percent = int(round(max(0, min(100, (elapsed / total) * 100))))
        return percent, "time estimate"

    if steps:
        completed_count = len([step for step in steps if getattr(step, "completed_at", None)])
        percent = int(round((completed_count / len(steps)) * 100))
        return percent, "step estimate"

    return 0, "estimate"


def _pf67_patch009m_flow_group(job):
    status = (getattr(job, "status", "") or "").lower()
    started = getattr(job, "started_at", None)
    now = _pf67_009m_datetime.utcnow()

    if status in ["completed", "complete", "archived", "done"]:
        return "completed", "Completed"

    if started and started > now:
        return "scheduled", "Scheduled"

    if status in ["pending", "scheduled"]:
        return "scheduled", "Scheduled"

    return "active", "Active"


def _pf67_patch009m_priority_rank(priority):
    priority = (priority or "Normal").strip().lower()

    if priority == "critical":
        return 4

    if priority == "high":
        return 3

    if priority == "normal":
        return 2

    if priority == "low":
        return 1

    return 2


def _pf67_patch009m_item(job):
    template = _pf67_patch009m_job_template(job)
    current_step = _pf67_patch009m_current_open_step(job)
    progress_percent, progress_basis = _pf67_patch009m_progress(job)
    estimated_completion = _pf67_patch009m_estimated_completion(job)
    group_key, group_label = _pf67_patch009m_flow_group(job)
    priority = getattr(job, "priority", None) or "Normal"

    try:
        detail_url = url_for("views.job_detail", job_id=job.id)
    except Exception:
        try:
            detail_url = url_for("views.jobs_page")
        except Exception:
            detail_url = "#"

    started_value = getattr(job, "started_at", None)

    return {
        "job": job,
        "template": template,
        "current_step": current_step,
        "group_key": group_key,
        "group_label": group_label,
        "priority": priority,
        "priority_value": priority.strip().lower(),
        "priority_rank": _pf67_patch009m_priority_rank(priority),
        "is_private": bool(getattr(job, "is_private", False)),
        "progress_percent": progress_percent,
        "progress_basis": progress_basis,
        "started_value": started_value,
        "started_sort": _pf67_patch009m_timestamp_ms(started_value, 0),
        "started_display": _pf67_patch009m_short_datetime(started_value),
        "estimated_value": estimated_completion,
        "estimated_sort": _pf67_patch009m_timestamp_ms(estimated_completion, 9999999999999),
        "estimated_completion": _pf67_patch009m_short_datetime(estimated_completion),
        "time_label": _pf67_patch009m_time_label(current_step),
        "url": detail_url,
        "search_text": (
            (getattr(job, "name", "") or "") + " " +
            ((getattr(template, "name", "") or "") if template is not None else "")
        ).lower(),
    }


@views_bp.route("/flows-console-patch009m")
@_pf67_patch009m_edit_required
def pf67_patch009m_flows_console():
    rows = Job.query.order_by(Job.id.desc()).all()
    flows = [_pf67_patch009m_item(job) for job in rows]

    counts = {
        "active": len([item for item in flows if item["group_key"] == "active"]),
        "scheduled": len([item for item in flows if item["group_key"] == "scheduled"]),
        "completed": len([item for item in flows if item["group_key"] == "completed"]),
        "public": len([item for item in flows if not item["is_private"]]),
        "private": len([item for item in flows if item["is_private"]]),
    }

    return render_template(
        "flows_console_patch009m.html",
        flows=flows,
        counts=counts,
        total_count=len(flows),
    )

# === PF67 PATCH 010A PROCEDURES FOUNDATION ===
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate


try:
    _pf67_patch010a_edit_required = edit_required
except NameError:
    def _pf67_patch010a_edit_required(func):
        return func


def _pf67_patch010a_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010a_public_protocol_query():
    rows = ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()

    return [
        template for template in rows
        if not getattr(template, "is_private", False)
        and not _pf67_patch010a_is_procedure(template)
    ]


@views_bp.app_template_global("pf67_patch009g_public_protocol_list")
def pf67_patch009g_public_protocol_list():
    return _pf67_patch010a_public_protocol_query()


@views_bp.app_template_global("pf67_patch010a_role_label")
def pf67_patch010a_role_label(template):
    if _pf67_patch010a_is_procedure(template):
        return "Procedure"

    return "Protocol"


@views_bp.app_template_global("pf67_patch009g_public_dashboard_data")
def pf67_patch009g_public_dashboard_data():
    active_flows = []
    scheduled_flows = []
    completed_flows = []

    try:
        active_flows, scheduled_flows, completed_flows = _pf67_patch009g_public_flow_groups()
    except Exception:
        try:
            active_flows, scheduled_flows, completed_flows = _pf67_patch009f_public_flow_items()
        except Exception:
            pass

    return {
        "active_flows": active_flows,
        "scheduled_flows": scheduled_flows,
        "completed_flows": completed_flows,
        "public_protocols": _pf67_patch010a_public_protocol_query(),
    }


@views_bp.route("/procedures/new-patch010a")
@_pf67_patch010a_edit_required
def pf67_patch010a_new_procedure():
    procedure = ProtocolTemplate(
        name="New Procedure",
        description="",
        category="Procedure",
    )

    if hasattr(procedure, "protocol_role"):
        procedure.protocol_role = "procedure"

    if hasattr(procedure, "can_start_flow"):
        procedure.can_start_flow = True

    if hasattr(procedure, "is_private"):
        procedure.is_private = True

    db.session.add(procedure)
    db.session.commit()

    return redirect(url_for("views.template_detail", template_id=procedure.id))


@views_bp.route("/protocols/<int:template_id>/role-patch010a", methods=["POST"])
@_pf67_patch010a_edit_required
def pf67_patch010a_set_protocol_role(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    requested_role = (request.form.get("protocol_role") or "protocol").strip().lower()

    if requested_role not in ["protocol", "procedure"]:
        requested_role = "protocol"

    if hasattr(template, "protocol_role"):
        template.protocol_role = requested_role
        db.session.commit()

    return redirect(url_for("views.templates_page", role=requested_role))

# === PF67 PATCH 010B INSERT PROCEDURES ===
import html as _pf67_010b_html
import re as _pf67_010b_re
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate


try:
    _pf67_patch010b_edit_required = edit_required
except NameError:
    def _pf67_patch010b_edit_required(func):
        return func


def _pf67_patch010b_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010b_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010b_column_names(model):
    try:
        return set(model.__table__.columns.keys())
    except Exception:
        return set()


def _pf67_patch010b_plain_text(value):
    html_value = _pf67_010b_html.unescape(str(value or ""))
    html_value = _pf67_010b_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_010b_re.IGNORECASE | _pf67_010b_re.DOTALL,
    )
    html_value = _pf67_010b_re.sub(r"<[^>]+>", " ", html_value)
    html_value = _pf67_010b_html.unescape(html_value)
    return _pf67_010b_re.sub(r"\s+", " ", html_value).strip()


@views_bp.app_template_filter("pf67_patch010b_plain_summary")
def pf67_patch010b_plain_summary(value, max_length=220):
    text_value = _pf67_patch010b_plain_text(value)

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 220

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "…"

    return text_value


def _pf67_patch010b_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    mins = rem % 60
    parts = []

    if days:
        parts.append(str(days) + " day" + ("" if days == 1 else "s"))

    if hours:
        parts.append(str(hours) + " hour" + ("" if hours == 1 else "s"))

    if mins:
        parts.append(str(mins) + " minute" + ("" if mins == 1 else "s"))

    return " ".join(parts)


def _pf67_patch010b_procedure_minutes(procedure):
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))

    total = 0

    for index, step in enumerate(steps):
        # PF67 timing model treats the first step's timing as the Flow start offset, not internal wait.
        if index == 0:
            continue

        try:
            value = int(getattr(step, "ideal_minutes", 0) or 0)
        except Exception:
            value = 0

        if value > 0:
            total += value

    return total


def _pf67_patch010b_procedure_block_html(procedure, minutes):
    title = _pf67_010b_html.escape(getattr(procedure, "name", "") or "Procedure")
    description = getattr(procedure, "description", "") or ""
    clean_description = _pf67_patch010b_plain_text(description)
    safe_description = _pf67_010b_html.escape(clean_description) if clean_description else "No description."
    duration = _pf67_patch010b_duration_label(minutes)
    procedure_url = url_for("views.template_detail", template_id=procedure.id)

    duration_html = ""

    if duration:
        duration_html = '<span class="pf67-procedure-reference-pill">Estimated procedure time: ' + _pf67_010b_html.escape(duration) + '</span>'

    return (
        '<div class="pf67-procedure-reference" data-pf67-procedure-id="' + str(procedure.id) + '">'
        '<div class="pf67-procedure-reference-title">Procedure: ' + title + '</div>'
        '<div class="pf67-procedure-reference-description">' + safe_description + '</div>'
        '<div class="pf67-procedure-reference-meta">'
        + duration_html +
        '<a class="pf67-procedure-reference-pill pf67-procedure-admin-link" href="' + procedure_url + '">Open Procedure</a>'
        '</div>'
        '</div>'
    )


def _pf67_patch010b_set_if_present(obj, columns, name, value):
    if name in columns and hasattr(obj, name):
        setattr(obj, name, value)


def _pf67_patch010b_template_fk_column(columns):
    if "template_id" in columns:
        return "template_id"

    if "protocol_template_id" in columns:
        return "protocol_template_id"

    return None


def _pf67_patch010b_next_sort_order(template):
    steps = list(getattr(template, "steps", []) or [])
    values = []

    for step in steps:
        try:
            values.append(int(getattr(step, "sort_order", 0) or 0))
        except Exception:
            pass

    if not values:
        return 10

    return max(values) + 10


@views_bp.route("/protocols/<int:template_id>/insert-procedure-patch010b", methods=["GET", "POST"])
@_pf67_patch010b_edit_required
def pf67_patch010b_insert_procedure(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        try:
            procedure_id = int(request.form.get("procedure_id") or 0)
        except Exception:
            procedure_id = 0

        procedure = ProtocolTemplate.query.get_or_404(procedure_id)

        if not _pf67_patch010b_is_procedure(procedure):
            return redirect(url_for("views.template_detail", template_id=template.id))

        StepModel = _pf67_patch010b_step_model()

        if StepModel is None:
            return redirect(url_for("views.template_detail", template_id=template.id))

        columns = _pf67_patch010b_column_names(StepModel)
        step = StepModel()
        fk_column = _pf67_patch010b_template_fk_column(columns)
        minutes = _pf67_patch010b_procedure_minutes(procedure)
        description = _pf67_patch010b_plain_text(getattr(procedure, "description", "") or "")

        if fk_column:
            setattr(step, fk_column, template.id)

        _pf67_patch010b_set_if_present(step, columns, "name", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b_set_if_present(step, columns, "title", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b_set_if_present(step, columns, "instructions_html", _pf67_patch010b_procedure_block_html(procedure, minutes))
        _pf67_patch010b_set_if_present(step, columns, "instructions", description)
        _pf67_patch010b_set_if_present(step, columns, "sort_order", _pf67_patch010b_next_sort_order(template))
        _pf67_patch010b_set_if_present(step, columns, "step_kind", "procedure")
        _pf67_patch010b_set_if_present(step, columns, "procedure_template_id", procedure.id)
        _pf67_patch010b_set_if_present(step, columns, "procedure_snapshot_title", getattr(procedure, "name", "") or "Procedure")
        _pf67_patch010b_set_if_present(step, columns, "procedure_snapshot_description", description)
        _pf67_patch010b_set_if_present(step, columns, "procedure_snapshot_minutes", minutes)

        # Do not set ideal_minutes here. 010C will expand timing properly into Flow steps.
        db.session.add(step)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    procedures = [
        candidate for candidate in ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        if _pf67_patch010b_is_procedure(candidate) and candidate.id != template.id
    ]

    return render_template(
        "insert_procedure_patch010b.html",
        template=template,
        procedures=procedures,
    )

# === PF67 PATCH 010B2 PROCEDURE REFERENCE CLEANUP ===
import html as _pf67_010b2_html
import re as _pf67_010b2_re
from flask import request, redirect, url_for, render_template, jsonify
from .models import db, ProtocolTemplate


try:
    _pf67_patch010b2_edit_required = edit_required
except NameError:
    def _pf67_patch010b2_edit_required(func):
        return func


def _pf67_patch010b2_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010b2_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010b2_column_names(model):
    try:
        return set(model.__table__.columns.keys())
    except Exception:
        return set()


def _pf67_patch010b2_plain_text(value):
    html_value = _pf67_010b2_html.unescape(str(value or ""))
    html_value = _pf67_010b2_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_010b2_re.IGNORECASE | _pf67_010b2_re.DOTALL,
    )
    html_value = _pf67_010b2_re.sub(r"<img\b[^>]*>", " ", html_value, flags=_pf67_010b2_re.IGNORECASE)
    html_value = _pf67_010b2_re.sub(r"<[^>]+>", " ", html_value)
    html_value = _pf67_010b2_html.unescape(html_value)
    return _pf67_010b2_re.sub(r"\s+", " ", html_value).strip()


def _pf67_patch010b2_first_img_src_raw(value):
    html_value = value or ""

    if not html_value:
        return ""

    candidates = [
        str(html_value),
        _pf67_010b2_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_010b2_re.search(pattern, candidate, flags=_pf67_010b2_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_010b2_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch010b2_plain_summary")
def pf67_patch010b2_plain_summary(value, max_length=220):
    text_value = _pf67_patch010b2_plain_text(value)

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 220

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "…"

    return text_value


@views_bp.app_template_filter("pf67_patch010b2_first_img_src")
def pf67_patch010b2_first_img_src(value):
    return _pf67_patch010b2_first_img_src_raw(value)


def _pf67_patch010b2_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    mins = rem % 60
    parts = []

    if days:
        parts.append(str(days) + " day" + ("" if days == 1 else "s"))

    if hours:
        parts.append(str(hours) + " hour" + ("" if hours == 1 else "s"))

    if mins:
        parts.append(str(mins) + " minute" + ("" if mins == 1 else "s"))

    return " ".join(parts)


@views_bp.app_template_global("pf67_patch010b2_duration_label")
def pf67_patch010b2_duration_label(minutes):
    return _pf67_patch010b2_duration_label(minutes)


def _pf67_patch010b2_procedure_minutes(procedure):
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
    total = 0

    for index, step in enumerate(steps):
        if index == 0:
            continue

        try:
            value = int(getattr(step, "ideal_minutes", 0) or 0)
        except Exception:
            value = 0

        if value > 0:
            total += value

    return total


@views_bp.app_template_global("pf67_patch010b2_procedure_minutes")
def pf67_patch010b2_procedure_minutes(procedure):
    return _pf67_patch010b2_procedure_minutes(procedure)


def _pf67_patch010b2_set_if_present(obj, columns, name, value):
    if name in columns and hasattr(obj, name):
        setattr(obj, name, value)


def _pf67_patch010b2_template_fk_column(columns):
    if "template_id" in columns:
        return "template_id"

    if "protocol_template_id" in columns:
        return "protocol_template_id"

    return None


def _pf67_patch010b2_next_sort_order(template):
    steps = list(getattr(template, "steps", []) or [])
    values = []

    for step in steps:
        try:
            values.append(int(getattr(step, "sort_order", 0) or 0))
        except Exception:
            pass

    if not values:
        return 10

    return max(values) + 10


def _pf67_patch010b2_marker(procedure_id):
    return '<div data-pf67-procedure-reference="true" data-procedure-id="' + str(procedure_id) + '"></div>'


@views_bp.route("/templates/<int:template_id>/role-patch010b2.json")
def pf67_patch010b2_template_role_json(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    return jsonify({
        "id": template.id,
        "role": "procedure" if _pf67_patch010b2_is_procedure(template) else "protocol",
        "label": "Procedure" if _pf67_patch010b2_is_procedure(template) else "Protocol",
    })


@views_bp.route("/procedures/<int:procedure_id>/reference-patch010b2.json")
def pf67_patch010b2_procedure_reference_json(procedure_id):
    procedure = ProtocolTemplate.query.get_or_404(procedure_id)
    minutes = _pf67_patch010b2_procedure_minutes(procedure)
    description = getattr(procedure, "description", "") or ""

    return jsonify({
        "id": procedure.id,
        "title": getattr(procedure, "name", "") or "Procedure",
        "description": _pf67_patch010b2_plain_text(description) or "No description.",
        "thumbnail": _pf67_patch010b2_first_img_src_raw(description),
        "duration_minutes": minutes,
        "duration_label": _pf67_patch010b2_duration_label(minutes),
        "edit_url": url_for("views.template_detail", template_id=procedure.id),
    })


@views_bp.route("/protocols/<int:template_id>/insert-procedure-patch010b2", methods=["GET", "POST"])
@_pf67_patch010b2_edit_required
def pf67_patch010b2_insert_procedure(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        try:
            procedure_id = int(request.form.get("procedure_id") or 0)
        except Exception:
            procedure_id = 0

        procedure = ProtocolTemplate.query.get_or_404(procedure_id)

        if not _pf67_patch010b2_is_procedure(procedure):
            return redirect(url_for("views.template_detail", template_id=template.id))

        StepModel = _pf67_patch010b2_step_model()

        if StepModel is None:
            return redirect(url_for("views.template_detail", template_id=template.id))

        columns = _pf67_patch010b2_column_names(StepModel)
        step = StepModel()
        fk_column = _pf67_patch010b2_template_fk_column(columns)
        minutes = _pf67_patch010b2_procedure_minutes(procedure)
        description = _pf67_patch010b2_plain_text(getattr(procedure, "description", "") or "")

        if fk_column:
            setattr(step, fk_column, template.id)

        _pf67_patch010b2_set_if_present(step, columns, "name", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b2_set_if_present(step, columns, "title", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b2_set_if_present(step, columns, "instructions_html", _pf67_patch010b2_marker(procedure.id))
        _pf67_patch010b2_set_if_present(step, columns, "instructions", "")
        _pf67_patch010b2_set_if_present(step, columns, "sort_order", _pf67_patch010b2_next_sort_order(template))
        _pf67_patch010b2_set_if_present(step, columns, "step_kind", "procedure")
        _pf67_patch010b2_set_if_present(step, columns, "procedure_template_id", procedure.id)
        _pf67_patch010b2_set_if_present(step, columns, "procedure_snapshot_title", getattr(procedure, "name", "") or "Procedure")
        _pf67_patch010b2_set_if_present(step, columns, "procedure_snapshot_description", description)
        _pf67_patch010b2_set_if_present(step, columns, "procedure_snapshot_minutes", minutes)

        db.session.add(step)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    procedures = [
        candidate for candidate in ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        if _pf67_patch010b2_is_procedure(candidate) and candidate.id != template.id
    ]

    return render_template(
        "insert_procedure_patch010b2.html",
        template=template,
        procedures=procedures,
    )

# === PF67 PATCH 010B3 INLINE PROCEDURE STEPS ===
import html as _pf67_010b3_html
import re as _pf67_010b3_re
from flask import request, redirect, url_for, render_template, jsonify
from .models import db, ProtocolTemplate


try:
    _pf67_patch010b3_edit_required = edit_required
except NameError:
    def _pf67_patch010b3_edit_required(func):
        return func


def _pf67_patch010b3_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010b3_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010b3_column_names(model):
    try:
        return set(model.__table__.columns.keys())
    except Exception:
        return set()


def _pf67_patch010b3_plain_text(value):
    html_value = _pf67_010b3_html.unescape(str(value or ""))
    html_value = _pf67_010b3_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_010b3_re.IGNORECASE | _pf67_010b3_re.DOTALL,
    )
    html_value = _pf67_010b3_re.sub(r"<img\b[^>]*>", " ", html_value, flags=_pf67_010b3_re.IGNORECASE)
    html_value = _pf67_010b3_re.sub(r"<[^>]+>", " ", html_value)
    html_value = _pf67_010b3_html.unescape(html_value)
    return _pf67_010b3_re.sub(r"\s+", " ", html_value).strip()


def _pf67_patch010b3_first_img_src_raw(value):
    html_value = value or ""

    if not html_value:
        return ""

    candidates = [
        str(html_value),
        _pf67_010b3_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_010b3_re.search(pattern, candidate, flags=_pf67_010b3_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_010b3_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch010b3_plain_summary")
def pf67_patch010b3_plain_summary(value, max_length=220):
    text_value = _pf67_patch010b3_plain_text(value)

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 220

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "..."

    return text_value


@views_bp.app_template_filter("pf67_patch010b3_first_img_src")
def pf67_patch010b3_first_img_src(value):
    return _pf67_patch010b3_first_img_src_raw(value)


def _pf67_patch010b3_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    mins = rem % 60
    parts = []

    if days:
        parts.append(str(days) + " day" + ("" if days == 1 else "s"))

    if hours:
        parts.append(str(hours) + " hour" + ("" if hours == 1 else "s"))

    if mins:
        parts.append(str(mins) + " minute" + ("" if mins == 1 else "s"))

    return " ".join(parts)


@views_bp.app_template_global("pf67_patch010b3_duration_label")
def pf67_patch010b3_duration_label(minutes):
    return _pf67_patch010b3_duration_label(minutes)


def _pf67_patch010b3_procedure_minutes(procedure):
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
    total = 0

    for index, step in enumerate(steps):
        if index == 0:
            continue

        try:
            value = int(getattr(step, "ideal_minutes", 0) or 0)
        except Exception:
            value = 0

        if value > 0:
            total += value

    return total


@views_bp.app_template_global("pf67_patch010b3_procedure_minutes")
def pf67_patch010b3_procedure_minutes(procedure):
    return _pf67_patch010b3_procedure_minutes(procedure)


def _pf67_patch010b3_step_wait_label(step, index):
    if index == 0:
        return "Starts when Procedure begins"

    try:
        ideal = int(getattr(step, "ideal_minutes", 0) or 0)
    except Exception:
        ideal = 0

    if ideal > 0:
        return "Wait " + _pf67_patch010b3_duration_label(ideal)

    return ""


def _pf67_patch010b3_step_title(step, index):
    for name in ["name", "title"]:
        value = getattr(step, name, None)
        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010b3_step_summary(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)
        if value:
            return _pf67_patch010b3_plain_text(value)

    return ""


def _pf67_patch010b3_set_if_present(obj, columns, name, value):
    if name in columns and hasattr(obj, name):
        setattr(obj, name, value)


def _pf67_patch010b3_template_fk_column(columns):
    if "template_id" in columns:
        return "template_id"

    if "protocol_template_id" in columns:
        return "protocol_template_id"

    return None


def _pf67_patch010b3_next_sort_order(template):
    steps = list(getattr(template, "steps", []) or [])
    values = []

    for step in steps:
        try:
            values.append(int(getattr(step, "sort_order", 0) or 0))
        except Exception:
            pass

    if not values:
        return 10

    return max(values) + 10


def _pf67_patch010b3_marker(procedure_id):
    return '<div data-pf67-procedure-reference="true" data-procedure-id="' + str(procedure_id) + '"></div>'


@views_bp.route("/templates/<int:template_id>/role-patch010b3.json")
def pf67_patch010b3_template_role_json(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    return jsonify({
        "id": template.id,
        "role": "procedure" if _pf67_patch010b3_is_procedure(template) else "protocol",
        "label": "Procedure" if _pf67_patch010b3_is_procedure(template) else "Protocol",
    })


@views_bp.route("/procedures/<int:procedure_id>/reference-patch010b3.json")
def pf67_patch010b3_procedure_reference_json(procedure_id):
    procedure = ProtocolTemplate.query.get_or_404(procedure_id)
    minutes = _pf67_patch010b3_procedure_minutes(procedure)
    description = getattr(procedure, "description", "") or ""
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))

    step_payload = []

    for index, step in enumerate(steps):
        step_payload.append({
            "id": getattr(step, "id", index + 1),
            "index": index,
            "title": _pf67_patch010b3_step_title(step, index),
            "summary": _pf67_patch010b3_step_summary(step),
            "wait_label": _pf67_patch010b3_step_wait_label(step, index),
        })

    return jsonify({
        "id": procedure.id,
        "title": getattr(procedure, "name", "") or "Procedure",
        "description": _pf67_patch010b3_plain_text(description) or "No description.",
        "thumbnail": _pf67_patch010b3_first_img_src_raw(description),
        "duration_minutes": minutes,
        "duration_label": _pf67_patch010b3_duration_label(minutes),
        "edit_url": url_for("views.template_detail", template_id=procedure.id),
        "steps": step_payload,
    })


@views_bp.route("/protocols/<int:template_id>/insert-procedure-patch010b3", methods=["GET", "POST"])
@_pf67_patch010b3_edit_required
def pf67_patch010b3_insert_procedure(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        try:
            procedure_id = int(request.form.get("procedure_id") or 0)
        except Exception:
            procedure_id = 0

        procedure = ProtocolTemplate.query.get_or_404(procedure_id)

        if not _pf67_patch010b3_is_procedure(procedure):
            return redirect(url_for("views.template_detail", template_id=template.id))

        StepModel = _pf67_patch010b3_step_model()

        if StepModel is None:
            return redirect(url_for("views.template_detail", template_id=template.id))

        columns = _pf67_patch010b3_column_names(StepModel)
        step = StepModel()
        fk_column = _pf67_patch010b3_template_fk_column(columns)
        minutes = _pf67_patch010b3_procedure_minutes(procedure)
        description = _pf67_patch010b3_plain_text(getattr(procedure, "description", "") or "")

        if fk_column:
            setattr(step, fk_column, template.id)

        _pf67_patch010b3_set_if_present(step, columns, "name", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b3_set_if_present(step, columns, "title", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b3_set_if_present(step, columns, "instructions_html", _pf67_patch010b3_marker(procedure.id))
        _pf67_patch010b3_set_if_present(step, columns, "instructions", "")
        _pf67_patch010b3_set_if_present(step, columns, "sort_order", _pf67_patch010b3_next_sort_order(template))
        _pf67_patch010b3_set_if_present(step, columns, "step_kind", "procedure")
        _pf67_patch010b3_set_if_present(step, columns, "procedure_template_id", procedure.id)
        _pf67_patch010b3_set_if_present(step, columns, "procedure_snapshot_title", getattr(procedure, "name", "") or "Procedure")
        _pf67_patch010b3_set_if_present(step, columns, "procedure_snapshot_description", description)
        _pf67_patch010b3_set_if_present(step, columns, "procedure_snapshot_minutes", minutes)

        db.session.add(step)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    procedures = [
        candidate for candidate in ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        if _pf67_patch010b3_is_procedure(candidate) and candidate.id != template.id
    ]

    return render_template(
        "insert_procedure_patch010b3.html",
        template=template,
        procedures=procedures,
    )

# === PF67 PATCH 010B7 PROCEDURE STEPS AS NORMAL STEPS ===
import html as _pf67_010b7_html
import re as _pf67_010b7_re
from flask import request, redirect, url_for, render_template, jsonify
from .models import db, ProtocolTemplate


try:
    _pf67_patch010b7_edit_required = edit_required
except NameError:
    def _pf67_patch010b7_edit_required(func):
        return func


def _pf67_patch010b7_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010b7_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010b7_column_names(model):
    try:
        return set(model.__table__.columns.keys())
    except Exception:
        return set()


def _pf67_patch010b7_plain_text(value):
    html_value = _pf67_010b7_html.unescape(str(value or ""))
    html_value = _pf67_010b7_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_010b7_re.IGNORECASE | _pf67_010b7_re.DOTALL,
    )
    html_value = _pf67_010b7_re.sub(r"<img\b[^>]*>", " ", html_value, flags=_pf67_010b7_re.IGNORECASE)
    html_value = _pf67_010b7_re.sub(r"<[^>]+>", " ", html_value)
    html_value = _pf67_010b7_html.unescape(html_value)
    return _pf67_010b7_re.sub(r"\s+", " ", html_value).strip()


def _pf67_patch010b7_first_img_src_raw(value):
    html_value = value or ""

    if not html_value:
        return ""

    candidates = [
        str(html_value),
        _pf67_010b7_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_010b7_re.search(pattern, candidate, flags=_pf67_010b7_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_010b7_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch010b7_plain_summary")
def pf67_patch010b7_plain_summary(value, max_length=220):
    text_value = _pf67_patch010b7_plain_text(value)

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 220

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "..."

    return text_value


@views_bp.app_template_filter("pf67_patch010b7_first_img_src")
def pf67_patch010b7_first_img_src(value):
    return _pf67_patch010b7_first_img_src_raw(value)


def _pf67_patch010b7_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    mins = rem % 60
    parts = []

    if days:
        parts.append(str(days) + " day" + ("" if days == 1 else "s"))

    if hours:
        parts.append(str(hours) + " hour" + ("" if hours == 1 else "s"))

    if mins:
        parts.append(str(mins) + " minute" + ("" if mins == 1 else "s"))

    return " ".join(parts)


@views_bp.app_template_global("pf67_patch010b7_duration_label")
def pf67_patch010b7_duration_label(minutes):
    return _pf67_patch010b7_duration_label(minutes)


def _pf67_patch010b7_procedure_minutes(procedure):
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
    total = 0

    for index, step in enumerate(steps):
        if index == 0:
            continue

        try:
            value = int(getattr(step, "ideal_minutes", 0) or 0)
        except Exception:
            value = 0

        if value > 0:
            total += value

    return total


@views_bp.app_template_global("pf67_patch010b7_procedure_minutes")
def pf67_patch010b7_procedure_minutes(procedure):
    return _pf67_patch010b7_procedure_minutes(procedure)


def _pf67_patch010b7_step_wait_label(step, index):
    if index == 0:
        return ""

    try:
        ideal = int(getattr(step, "ideal_minutes", 0) or 0)
    except Exception:
        ideal = 0

    if ideal > 0:
        return "Wait " + _pf67_patch010b7_duration_label(ideal)

    return ""


def _pf67_patch010b7_step_title(step, index):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010b7_step_summary(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return _pf67_patch010b7_plain_text(value)

    return ""


def _pf67_patch010b7_set_if_present(obj, columns, name, value):
    if name in columns and hasattr(obj, name):
        setattr(obj, name, value)


def _pf67_patch010b7_template_fk_column(columns):
    if "template_id" in columns:
        return "template_id"

    if "protocol_template_id" in columns:
        return "protocol_template_id"

    return None


def _pf67_patch010b7_next_sort_order(template):
    steps = list(getattr(template, "steps", []) or [])
    values = []

    for step in steps:
        try:
            values.append(int(getattr(step, "sort_order", 0) or 0))
        except Exception:
            pass

    if not values:
        return 10

    return max(values) + 10


def _pf67_patch010b7_marker(procedure_id):
    return '<div data-pf67-procedure-reference="true" data-procedure-id="' + str(procedure_id) + '"></div>'


@views_bp.route("/templates/<int:template_id>/role-patch010b7.json")
def pf67_patch010b7_template_role_json(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    return jsonify({
        "id": template.id,
        "role": "procedure" if _pf67_patch010b7_is_procedure(template) else "protocol",
        "label": "Procedure" if _pf67_patch010b7_is_procedure(template) else "Protocol",
    })


@views_bp.route("/procedures/<int:procedure_id>/reference-patch010b7.json")
def pf67_patch010b7_procedure_reference_json(procedure_id):
    procedure = ProtocolTemplate.query.get_or_404(procedure_id)
    minutes = _pf67_patch010b7_procedure_minutes(procedure)
    description = getattr(procedure, "description", "") or ""
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))

    step_payload = []

    for index, step in enumerate(steps):
        step_payload.append({
            "id": getattr(step, "id", index + 1),
            "index": index,
            "title": _pf67_patch010b7_step_title(step, index),
            "summary": _pf67_patch010b7_step_summary(step),
            "wait_label": _pf67_patch010b7_step_wait_label(step, index),
        })

    return jsonify({
        "id": procedure.id,
        "title": getattr(procedure, "name", "") or "Procedure",
        "description": _pf67_patch010b7_plain_text(description) or "",
        "thumbnail": _pf67_patch010b7_first_img_src_raw(description),
        "duration_minutes": minutes,
        "duration_label": _pf67_patch010b7_duration_label(minutes),
        "edit_url": url_for("views.template_detail", template_id=procedure.id),
        "steps": step_payload,
    })


@views_bp.route("/protocols/<int:template_id>/insert-procedure-patch010b7", methods=["GET", "POST"])
@_pf67_patch010b7_edit_required
def pf67_patch010b7_insert_procedure(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        try:
            procedure_id = int(request.form.get("procedure_id") or 0)
        except Exception:
            procedure_id = 0

        procedure = ProtocolTemplate.query.get_or_404(procedure_id)

        if not _pf67_patch010b7_is_procedure(procedure):
            return redirect(url_for("views.template_detail", template_id=template.id))

        StepModel = _pf67_patch010b7_step_model()

        if StepModel is None:
            return redirect(url_for("views.template_detail", template_id=template.id))

        columns = _pf67_patch010b7_column_names(StepModel)
        step = StepModel()
        fk_column = _pf67_patch010b7_template_fk_column(columns)
        minutes = _pf67_patch010b7_procedure_minutes(procedure)
        description = _pf67_patch010b7_plain_text(getattr(procedure, "description", "") or "")

        if fk_column:
            setattr(step, fk_column, template.id)

        _pf67_patch010b7_set_if_present(step, columns, "name", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b7_set_if_present(step, columns, "title", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b7_set_if_present(step, columns, "instructions_html", _pf67_patch010b7_marker(procedure.id))
        _pf67_patch010b7_set_if_present(step, columns, "instructions", "")
        _pf67_patch010b7_set_if_present(step, columns, "sort_order", _pf67_patch010b7_next_sort_order(template))
        _pf67_patch010b7_set_if_present(step, columns, "step_kind", "procedure")
        _pf67_patch010b7_set_if_present(step, columns, "procedure_template_id", procedure.id)
        _pf67_patch010b7_set_if_present(step, columns, "procedure_snapshot_title", getattr(procedure, "name", "") or "Procedure")
        _pf67_patch010b7_set_if_present(step, columns, "procedure_snapshot_description", description)
        _pf67_patch010b7_set_if_present(step, columns, "procedure_snapshot_minutes", minutes)

        db.session.add(step)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    procedures = [
        candidate for candidate in ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        if _pf67_patch010b7_is_procedure(candidate) and candidate.id != template.id
    ]

    return render_template(
        "insert_procedure_patch010b7.html",
        template=template,
        procedures=procedures,
    )

# === PF67 PATCH 010B9 SERVER RENDERED PROCEDURE STEP BUNDLE ===
import html as _pf67_010b9_html
import re as _pf67_010b9_re
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate


try:
    _pf67_patch010b9_edit_required = edit_required
except NameError:
    def _pf67_patch010b9_edit_required(func):
        return func


class _PF67Patch010B9DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010b9_is_procedure(template):
    return (getattr(template, "protocol_role", None) or "protocol") == "procedure"


def _pf67_patch010b9_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010b9_column_names(model):
    try:
        return set(model.__table__.columns.keys())
    except Exception:
        return set()


def _pf67_patch010b9_plain_text(value):
    html_value = _pf67_010b9_html.unescape(str(value or ""))
    html_value = _pf67_010b9_re.sub(
        r"<(script|style)\b.*?</\1>",
        " ",
        html_value,
        flags=_pf67_010b9_re.IGNORECASE | _pf67_010b9_re.DOTALL,
    )
    html_value = _pf67_010b9_re.sub(r"<img\b[^>]*>", " ", html_value, flags=_pf67_010b9_re.IGNORECASE)
    html_value = _pf67_010b9_re.sub(r"<[^>]+>", " ", html_value)
    html_value = _pf67_010b9_html.unescape(html_value)
    return _pf67_010b9_re.sub(r"\s+", " ", html_value).strip()


def _pf67_patch010b9_first_img_src_raw(value):
    html_value = value or ""

    if not html_value:
        return ""

    candidates = [
        str(html_value),
        _pf67_010b9_html.unescape(str(html_value)),
    ]

    patterns = [
        r"<img\b[^>]*\bsrc\s*=\s*\"([^\"]+)\"",
        r"<img\b[^>]*\bsrc\s*=\s*'([^']+)'",
        r"<img\b[^>]*\bsrc\s*=\s*([^>\s]+)",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = _pf67_010b9_re.search(pattern, candidate, flags=_pf67_010b9_re.IGNORECASE)

            if match:
                src = (match.group(1) or "").strip()
                src = src.strip('"').strip("'")
                return _pf67_010b9_html.unescape(src)

    return ""


@views_bp.app_template_filter("pf67_patch010b9_plain_summary")
def pf67_patch010b9_plain_summary(value, max_length=220):
    text_value = _pf67_patch010b9_plain_text(value)

    try:
        max_length = int(max_length)
    except Exception:
        max_length = 220

    if max_length > 0 and len(text_value) > max_length:
        text_value = text_value[: max_length - 1].rstrip() + "..."

    return text_value


@views_bp.app_template_filter("pf67_patch010b9_first_img_src")
def pf67_patch010b9_first_img_src(value):
    return _pf67_patch010b9_first_img_src_raw(value)


def _pf67_patch010b9_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    mins = rem % 60
    parts = []

    if days:
        parts.append(str(days) + " day" + ("" if days == 1 else "s"))

    if hours:
        parts.append(str(hours) + " hour" + ("" if hours == 1 else "s"))

    if mins:
        parts.append(str(mins) + " minute" + ("" if mins == 1 else "s"))

    return " ".join(parts)


@views_bp.app_template_global("pf67_patch010b9_duration_label")
def pf67_patch010b9_duration_label(minutes):
    return _pf67_patch010b9_duration_label(minutes)


def _pf67_patch010b9_procedure_minutes(procedure):
    steps = list(getattr(procedure, "steps", []) or [])
    steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
    total = 0

    for index, step in enumerate(steps):
        if index == 0:
            continue

        try:
            value = int(getattr(step, "ideal_minutes", 0) or 0)
        except Exception:
            value = 0

        if value > 0:
            total += value

    return total


@views_bp.app_template_global("pf67_patch010b9_procedure_minutes")
def pf67_patch010b9_procedure_minutes(procedure):
    return _pf67_patch010b9_procedure_minutes(procedure)


def _pf67_patch010b9_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010b9_step_title(step, index):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010b9_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010b9_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]
    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010b9_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010b9_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                # Keep the reference visible if the referenced Procedure cannot be found.
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010b9_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010b9_timing_overrides_from(step)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010b9_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010b9_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "sort_order": _pf67_patch010b9_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if proc_index == 0:
                    # The parent Procedure-reference step's timing controls the wait before the Procedure begins.
                    overrides.update(parent_timing)

                result.append(_PF67Patch010B9DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            if not hasattr(step, "is_procedure_derived"):
                try:
                    setattr(step, "is_procedure_derived", False)
                except Exception:
                    pass

            result.append(step)

    return result


def _pf67_patch010b9_set_if_present(obj, columns, name, value):
    if name in columns and hasattr(obj, name):
        setattr(obj, name, value)


def _pf67_patch010b9_template_fk_column(columns):
    if "template_id" in columns:
        return "template_id"

    if "protocol_template_id" in columns:
        return "protocol_template_id"

    return None


def _pf67_patch010b9_next_sort_order(template):
    steps = list(getattr(template, "steps", []) or [])
    values = []

    for step in steps:
        try:
            values.append(int(getattr(step, "sort_order", 0) or 0))
        except Exception:
            pass

    if not values:
        return 10

    return max(values) + 10


def _pf67_patch010b9_marker(procedure_id):
    return '<div data-pf67-procedure-reference="true" data-procedure-id="' + str(procedure_id) + '"></div>'


@views_bp.before_app_request
def pf67_patch010b9_redirect_procedure_reference_step_edits():
    # If the existing editor routes attempt to edit the parent Procedure-reference step,
    # redirect to the Procedure editor instead. This keeps derived Procedure steps read-only
    # in the parent Protocol while preserving the current editor route structure.
    path_value = request.path or ""
    match = _pf67_010b9_re.search(r"/(?:templates|protocols)/(\d+).*?/(?:steps?|protocol_steps?|template_steps?)/(\d+)", path_value)

    if not match:
        return None

    try:
        parent_template_id = int(match.group(1))
        step_id = int(match.group(2))
    except Exception:
        return None

    StepModel = _pf67_patch010b9_step_model()

    if StepModel is None:
        return None

    step = StepModel.query.get(step_id)

    if not step:
        return None

    procedure_id = getattr(step, "procedure_template_id", None)

    if not procedure_id:
        return None

    try:
        procedure_id = int(procedure_id)
    except Exception:
        return None

    return_to = request.referrer or url_for("views.template_detail", template_id=parent_template_id)

    return redirect(url_for("views.template_detail", template_id=procedure_id, return_to=return_to))


@views_bp.route("/protocols/<int:template_id>/insert-procedure-patch010b9", methods=["GET", "POST"])
@_pf67_patch010b9_edit_required
def pf67_patch010b9_insert_procedure(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        try:
            procedure_id = int(request.form.get("procedure_id") or 0)
        except Exception:
            procedure_id = 0

        procedure = ProtocolTemplate.query.get_or_404(procedure_id)

        if not _pf67_patch010b9_is_procedure(procedure):
            return redirect(url_for("views.template_detail", template_id=template.id))

        StepModel = _pf67_patch010b9_step_model()

        if StepModel is None:
            return redirect(url_for("views.template_detail", template_id=template.id))

        columns = _pf67_patch010b9_column_names(StepModel)
        step = StepModel()
        fk_column = _pf67_patch010b9_template_fk_column(columns)
        minutes = _pf67_patch010b9_procedure_minutes(procedure)
        description = _pf67_patch010b9_plain_text(getattr(procedure, "description", "") or "")

        if fk_column:
            setattr(step, fk_column, template.id)

        _pf67_patch010b9_set_if_present(step, columns, "name", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b9_set_if_present(step, columns, "title", "Procedure: " + (getattr(procedure, "name", "") or "Procedure"))
        _pf67_patch010b9_set_if_present(step, columns, "instructions_html", _pf67_patch010b9_marker(procedure.id))
        _pf67_patch010b9_set_if_present(step, columns, "instructions", "")
        _pf67_patch010b9_set_if_present(step, columns, "sort_order", _pf67_patch010b9_next_sort_order(template))
        _pf67_patch010b9_set_if_present(step, columns, "step_kind", "procedure")
        _pf67_patch010b9_set_if_present(step, columns, "procedure_template_id", procedure.id)
        _pf67_patch010b9_set_if_present(step, columns, "procedure_snapshot_title", getattr(procedure, "name", "") or "Procedure")
        _pf67_patch010b9_set_if_present(step, columns, "procedure_snapshot_description", description)
        _pf67_patch010b9_set_if_present(step, columns, "procedure_snapshot_minutes", minutes)

        db.session.add(step)
        db.session.commit()

        return redirect(url_for("views.template_detail", template_id=template.id))

    procedures = [
        candidate for candidate in ProtocolTemplate.query.order_by(ProtocolTemplate.name.asc()).all()
        if _pf67_patch010b9_is_procedure(candidate) and candidate.id != template.id
    ]

    return render_template(
        "insert_procedure_patch010b9.html",
        template=template,
        procedures=procedures,
    )

# === PF67 PATCH 010B12 PROCEDURE BUNDLE BOUNDARIES ===
from flask import request, redirect, url_for
from .models import ProtocolTemplate


class _PF67Patch010B12DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010b12_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010b12_step_title(step, index):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010b12_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010b12_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]
    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010b12_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010b12_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010b12_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010b12_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010b12_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010b12_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": proc_index == 0,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "sort_order": _pf67_patch010b12_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if proc_index == 0:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010B12DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
            except Exception:
                pass

            result.append(step)

    return result

# === PF67 PATCH 010C PROCEDURE FLOW SNAPSHOT EXPANSION ===
from .models import ProtocolTemplate


class _PF67Patch010CFlowStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
        "wait_minutes",
        "duration_minutes",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


def _pf67_patch010c_apply_step_data(target_step, source_step):
    # Copy common step data from an expanded source step to a new static Flow/Job step.
    # Existing start-flow code may use direct assignments; this helper is available
    # for a future direct route patch if needed.
    title = _pf67_patch010c_step_title(source_step, 0)
    instructions = _pf67_patch010c_step_instructions_html(source_step)

    for name in ["name", "title"]:
        if hasattr(target_step, name):
            try:
                setattr(target_step, name, title)
            except Exception:
                pass

    for name in ["instructions_html", "instructions", "description"]:
        if hasattr(target_step, name):
            try:
                setattr(target_step, name, instructions)
            except Exception:
                pass

    for name, value in _pf67_patch010c_timing_overrides_from(source_step).items():
        if hasattr(target_step, name):
            try:
                setattr(target_step, name, value)
            except Exception:
                pass

    provenance = {
        "source_protocol_step_id": getattr(source_step, "source_protocol_step_id", getattr(source_step, "id", None)),
        "source_procedure_template_id": getattr(source_step, "source_procedure_template_id", None),
        "source_procedure_step_id": getattr(source_step, "source_procedure_step_id", None),
        "source_procedure_reference_step_id": getattr(source_step, "source_procedure_reference_step_id", None),
        "is_procedure_derived": bool(getattr(source_step, "is_procedure_derived", False)),
    }

    for name, value in provenance.items():
        if hasattr(target_step, name):
            try:
                setattr(target_step, name, value)
            except Exception:
                pass

    return target_step


def pf67_patch010c_flow_steps(template):
    # Return static step source objects for Flow creation.
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))

            parent_timing = _pf67_patch010c_timing_overrides_from(step)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c_step_instructions_html(proc_step)
                is_first = proc_index == 0
                is_last = proc_index == (len(procedure_steps) - 1)

                overrides = {
                    "id": getattr(proc_step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": instructions_html,
                    "instructions": instructions_html,
                    "is_procedure_derived": True,
                    "source_protocol_step_id": getattr(step, "id", None),
                    "source_procedure_template_id": procedure.id,
                    "source_procedure_step_id": getattr(proc_step, "id", None),
                    "source_procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": is_first,
                    "is_last_procedure_derived": is_last,
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if is_first:
                    # Parent Protocol reference timing controls the wait before the Procedure begins.
                    overrides.update(parent_timing)

                result.append(_PF67Patch010CFlowStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "source_protocol_step_id", getattr(step, "id", None))
            except Exception:
                pass

            result.append(step)

    return result

# === PF67 PATCH 010C2 PROCEDURE REFERENCE EDITOR AND CONTROLS ===
from flask import request, redirect, url_for, render_template
from .models import db, ProtocolTemplate


try:
    _pf67_patch010c2_edit_required = edit_required
except NameError:
    def _pf67_patch010c2_edit_required(func):
        return func


class _PF67Patch010C2DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c2_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c2_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c2_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c2_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c2_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "wait_minutes",
        "duration_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010c2_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c2_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c2_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010c2_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c2_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c2_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "instructions": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": proc_index == 0,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "pf67_show_reorder_controls": False,
                    "allow_reorder": False,
                    "can_reorder": False,
                    "is_reorderable": False,
                    "sort_order": _pf67_patch010c2_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if proc_index == 0:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010C2DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
                setattr(step, "pf67_show_reorder_controls", True)
                setattr(step, "allow_reorder", True)
                setattr(step, "can_reorder", True)
                setattr(step, "is_reorderable", True)
            except Exception:
                pass

            result.append(step)

    return result


def _pf67_patch010c2_timing_fields(step):
    field_defs = [
        ("check_minutes", "Check"),
        ("ideal_minutes", "Ideal"),
        ("limit_minutes", "Limit"),
        ("risk_minutes", "Risk"),
        ("failure_minutes", "Failure"),
        ("minimum_minutes", "Minimum"),
        ("maximum_minutes", "Maximum"),
        ("wait_minutes", "Wait"),
        ("duration_minutes", "Duration"),
    ]

    fields = []

    for name, label in field_defs:
        if hasattr(step, name):
            try:
                value = getattr(step, name)
            except Exception:
                value = ""

            if value is None:
                value = ""

            fields.append({
                "name": name,
                "label": label + " minutes",
                "value": value,
            })

    return fields


@views_bp.route("/protocols/<int:template_id>/procedure-reference/<int:step_id>/timing", methods=["GET", "POST"])
@_pf67_patch010c2_edit_required
def pf67_patch010c2_procedure_reference_timing(template_id, step_id):
    template = ProtocolTemplate.query.get_or_404(template_id)
    StepModel = _pf67_patch010c2_step_model()

    if StepModel is None:
        return redirect(url_for("views.template_detail", template_id=template.id))

    step = StepModel.query.get_or_404(step_id)
    procedure_id = getattr(step, "procedure_template_id", None)

    if not procedure_id:
        return redirect(url_for("views.template_detail", template_id=template.id))

    procedure = ProtocolTemplate.query.get(procedure_id)
    return_to = request.args.get("return_to") or request.form.get("return_to") or request.referrer or url_for("views.template_detail", template_id=template.id)
    timing_fields = _pf67_patch010c2_timing_fields(step)

    if request.method == "POST":
        for field in timing_fields:
            raw_value = request.form.get(field["name"], "").strip()

            if raw_value == "":
                value = 0
            else:
                try:
                    value = int(float(raw_value))
                except Exception:
                    value = 0

            try:
                setattr(step, field["name"], value)
            except Exception:
                pass

        db.session.commit()
        return redirect(return_to)

    selected_step_title = request.args.get("step_title") or ""

    return render_template(
        "procedure_reference_timing_patch010c2.html",
        template=template,
        step=step,
        procedure=procedure,
        timing_fields=timing_fields,
        selected_step_title=selected_step_title,
        return_to=return_to,
    )


@views_bp.before_app_request
def pf67_patch010c2_redirect_procedure_reference_step_edits():
    path_value = request.path or ""
    lower_path = path_value.lower()

    if "procedure-reference" in lower_path:
        return None

    if "step" not in lower_path:
        return None

    if request.method not in ("GET", "POST"):
        return None

    StepModel = _pf67_patch010c2_step_model()

    if StepModel is None:
        return None

    numbers = [int(value) for value in re.findall(r"\d+", path_value)]

    if not numbers:
        return None

    # Last matching Procedure-reference step id wins.
    for candidate_step_id in reversed(numbers):
        try:
            step = StepModel.query.get(candidate_step_id)
        except Exception:
            step = None

        if not step:
            continue

        procedure_id = getattr(step, "procedure_template_id", None)

        if not procedure_id:
            continue

        template_id = None

        for attr in ["template_id", "protocol_template_id"]:
            if hasattr(step, attr):
                try:
                    template_id = int(getattr(step, attr))
                    break
                except Exception:
                    pass

        if not template_id:
            template_id = numbers[0]

        return_to = request.referrer or url_for("views.template_detail", template_id=template_id)
        step_title = request.args.get("step_title") or ""

        return redirect(
            url_for(
                "views.pf67_patch010c2_procedure_reference_timing",
                template_id=template_id,
                step_id=candidate_step_id,
                return_to=return_to,
                step_title=step_title,
            )
        )

    return None

# === PF67 PATCH 010C3 PROCEDURE BUNDLE CONTROLS AND DELETE FIX ===
import re as _pf67_010c3_re
from flask import request, redirect, url_for
from .models import db, ProtocolTemplate


class _PF67Patch010C3DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c3_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c3_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c3_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c3_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c3_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "wait_minutes",
        "duration_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010c3_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c3_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c3_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))

            parent_timing = _pf67_patch010c3_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c3_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c3_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_first = proc_index == 0
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "instructions": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": is_first,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "pf67_show_reorder_controls": is_first,
                    "allow_reorder": is_first,
                    "can_reorder": is_first,
                    "is_reorderable": is_first,
                    "sort_order": _pf67_patch010c3_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if is_first:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010C3DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "is_first_procedure_derived", False)
                setattr(step, "is_last_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
                setattr(step, "pf67_show_reorder_controls", True)
                setattr(step, "allow_reorder", True)
                setattr(step, "can_reorder", True)
                setattr(step, "is_reorderable", True)
            except Exception:
                pass

            result.append(step)

    return result


def _pf67_patch010c3_template_redirect_url():
    for endpoint in ["views.template_list", "views.templates", "views.protocols", "views.dashboard"]:
        try:
            return url_for(endpoint)
        except Exception:
            pass

    return "/templates"


@views_bp.before_app_request
def pf67_patch010c3_delete_protocol_with_procedure_references():
    # Target only full Protocol/Template delete POST routes, not step deletes.
    if request.method != "POST":
        return None

    path_value = request.path or ""
    lower_path = path_value.lower()

    if "step" in lower_path:
        return None

    if "delete" not in lower_path and "remove" not in lower_path:
        return None

    match = _pf67_010c3_re.search(r"/(?:templates|protocols)/(\d+)/(?:delete|remove)", lower_path)

    if not match:
        match = _pf67_010c3_re.search(r"/(?:delete|remove)/(?:templates|protocols)/(\d+)", lower_path)

    if not match:
        return None

    try:
        template_id = int(match.group(1))
    except Exception:
        return None

    template = ProtocolTemplate.query.get(template_id)

    if not template:
        return None

    StepModel = _pf67_patch010c3_step_model()

    if StepModel is not None:
        for step in list(getattr(template, "steps", []) or []):
            try:
                db.session.delete(step)
            except Exception:
                pass

    try:
        db.session.delete(template)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    return redirect(_pf67_patch010c3_template_redirect_url())


@views_bp.before_app_request
def pf67_patch010c3_redirect_procedure_reference_step_edits():
    # Robust redirect for full-editor links that point at the parent Procedure-reference step.
    path_value = request.path or ""
    lower_path = path_value.lower()

    if "procedure-reference" in lower_path:
        return None

    if "step" not in lower_path:
        return None

    if request.method not in ("GET", "POST"):
        return None

    StepModel = _pf67_patch010c3_step_model()

    if StepModel is None:
        return None

    candidate_numbers = [int(value) for value in _pf67_010c3_re.findall(r"\d+", path_value)]

    for key, value in request.values.items():
        try:
            if str(value).isdigit():
                candidate_numbers.append(int(value))
        except Exception:
            pass

    seen = set()

    for candidate_step_id in reversed(candidate_numbers):
        if candidate_step_id in seen:
            continue

        seen.add(candidate_step_id)

        try:
            step = StepModel.query.get(candidate_step_id)
        except Exception:
            step = None

        if not step:
            continue

        procedure_id = getattr(step, "procedure_template_id", None)

        if not procedure_id:
            continue

        template_id = None

        for attr in ["template_id", "protocol_template_id"]:
            if hasattr(step, attr):
                try:
                    template_id = int(getattr(step, attr))
                    break
                except Exception:
                    pass

        if not template_id:
            template_id = candidate_numbers[0] if candidate_numbers else 0

        return_to = request.referrer or url_for("views.template_detail", template_id=template_id)
        step_title = request.args.get("step_title") or ""

        return redirect(
            url_for(
                "views.pf67_patch010c2_procedure_reference_timing",
                template_id=template_id,
                step_id=candidate_step_id,
                return_to=return_to,
                step_title=step_title,
            )
        )

    return None

# === PF67 PATCH 010C4 PROTOCOL EDITOR PROCEDURE CLEANUP ===
import re as _pf67_010c4_re
from flask import request, jsonify, redirect, url_for
from .models import ProtocolTemplate


class _PF67Patch010C4DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c4_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c4_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c4_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c4_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c4_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "wait_minutes",
        "duration_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010c4_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c4_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c4_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010c4_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c4_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c4_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_first = proc_index == 0
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "instructions": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": is_first,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "pf67_show_reorder_controls": True,
                    "allow_reorder": True,
                    "can_reorder": True,
                    "is_reorderable": True,
                    "pf67_bundle_move_up": is_first,
                    "pf67_bundle_move_down": is_last,
                    "sort_order": _pf67_patch010c4_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if is_first:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010C4DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "is_first_procedure_derived", False)
                setattr(step, "is_last_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
                setattr(step, "pf67_show_reorder_controls", True)
                setattr(step, "allow_reorder", True)
                setattr(step, "can_reorder", True)
                setattr(step, "is_reorderable", True)
                setattr(step, "pf67_bundle_move_up", False)
                setattr(step, "pf67_bundle_move_down", False)
            except Exception:
                pass

            result.append(step)

    return result


def _pf67_patch010c4_template_id_for_step(step):
    for attr in ["template_id", "protocol_template_id"]:
        if hasattr(step, attr):
            try:
                value = getattr(step, attr)
                if value:
                    return int(value)
            except Exception:
                pass

    return None


def _pf67_patch010c4_is_first_step(step):
    template_id = _pf67_patch010c4_template_id_for_step(step)

    if not template_id:
        return False

    StepModel = _pf67_patch010c4_step_model()

    if StepModel is None:
        return False

    steps = []

    try:
        columns = StepModel.__table__.columns.keys()
    except Exception:
        columns = []

    try:
        if "template_id" in columns:
            steps = StepModel.query.filter_by(template_id=template_id).all()
        elif "protocol_template_id" in columns:
            steps = StepModel.query.filter_by(protocol_template_id=template_id).all()
    except Exception:
        steps = []

    if not steps:
        return True

    steps.sort(key=lambda item: (_pf67_patch010c4_step_sort_value(item), getattr(item, "id", 0) or 0))
    return bool(steps and getattr(steps[0], "id", None) == getattr(step, "id", None))


@views_bp.route("/pf67/patch010c4/step-context")
def pf67_patch010c4_step_context():
    StepModel = _pf67_patch010c4_step_model()
    step_id = request.args.get("step_id", type=int)
    template_id = request.args.get("template_id", type=int)

    payload = {
        "ok": True,
        "is_first_step": False,
        "hide_timing": False,
        "is_procedure_reference": False,
    }

    if StepModel is not None and step_id:
        step = StepModel.query.get(step_id)

        if step:
            is_first = _pf67_patch010c4_is_first_step(step)
            payload["is_first_step"] = is_first
            payload["hide_timing"] = is_first
            payload["is_procedure_reference"] = bool(getattr(step, "procedure_template_id", None))
            payload["procedure_template_id"] = getattr(step, "procedure_template_id", None)
            return jsonify(payload)

    if StepModel is not None and template_id:
        steps = []

        try:
            columns = StepModel.__table__.columns.keys()
        except Exception:
            columns = []

        try:
            if "template_id" in columns:
                steps = StepModel.query.filter_by(template_id=template_id).all()
            elif "protocol_template_id" in columns:
                steps = StepModel.query.filter_by(protocol_template_id=template_id).all()
        except Exception:
            steps = []

        payload["is_first_step"] = len(steps) == 0
        payload["hide_timing"] = len(steps) == 0

    return jsonify(payload)


@views_bp.before_app_request
def pf67_patch010c4_redirect_procedure_reference_step_edits():
    path_value = request.path or ""
    lower_path = path_value.lower()

    if "procedure-reference" in lower_path:
        return None

    if "step" not in lower_path:
        return None

    if request.method not in ("GET", "POST"):
        return None

    StepModel = _pf67_patch010c4_step_model()

    if StepModel is None:
        return None

    candidate_numbers = [int(value) for value in _pf67_010c4_re.findall(r"\d+", path_value)]

    for key, value in request.values.items():
        try:
            if str(value).isdigit():
                candidate_numbers.append(int(value))
        except Exception:
            pass

    seen = set()

    for candidate_step_id in reversed(candidate_numbers):
        if candidate_step_id in seen:
            continue

        seen.add(candidate_step_id)

        try:
            step = StepModel.query.get(candidate_step_id)
        except Exception:
            step = None

        if not step:
            continue

        procedure_id = getattr(step, "procedure_template_id", None)

        if not procedure_id:
            continue

        template_id = _pf67_patch010c4_template_id_for_step(step) or (candidate_numbers[0] if candidate_numbers else 0)
        return_to = request.referrer or url_for("views.template_detail", template_id=template_id)
        step_title = request.args.get("step_title") or ""

        try:
            return redirect(
                url_for(
                    "views.pf67_patch010c2_procedure_reference_timing",
                    template_id=template_id,
                    step_id=candidate_step_id,
                    return_to=return_to,
                    step_title=step_title,
                )
            )
        except Exception:
            return None

    return None

# === PF67 PATCH 010C5 PROCEDURE REFERENCE CONTROL ROW ===
import re as _pf67_010c5_re
from flask import request, jsonify, redirect, url_for
from .models import ProtocolTemplate


class _PF67Patch010C5DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c5_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c5_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c5_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c5_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c5_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "wait_minutes",
        "duration_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010c5_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c5_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c5_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010c5_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c5_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c5_step_instructions_html(proc_step)
                wrapped_instructions = '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_first = proc_index == 0
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "instructions": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": is_first,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "pf67_show_reorder_controls": False,
                    "allow_reorder": False,
                    "can_reorder": False,
                    "is_reorderable": False,
                    "pf67_bundle_move_up": False,
                    "pf67_bundle_move_down": False,
                    "sort_order": _pf67_patch010c5_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if is_first:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010C5DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "is_first_procedure_derived", False)
                setattr(step, "is_last_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
                setattr(step, "pf67_show_reorder_controls", True)
                setattr(step, "allow_reorder", True)
                setattr(step, "can_reorder", True)
                setattr(step, "is_reorderable", True)
                setattr(step, "pf67_bundle_move_up", False)
                setattr(step, "pf67_bundle_move_down", False)
            except Exception:
                pass

            result.append(step)

    return result


def _pf67_patch010c5_template_id_for_step(step):
    for attr in ["template_id", "protocol_template_id"]:
        if hasattr(step, attr):
            try:
                value = getattr(step, attr)
                if value:
                    return int(value)
            except Exception:
                pass

    return None


def _pf67_patch010c5_is_first_step(step):
    template_id = _pf67_patch010c5_template_id_for_step(step)

    if not template_id:
        return False

    StepModel = _pf67_patch010c5_step_model()

    if StepModel is None:
        return False

    steps = []

    try:
        columns = StepModel.__table__.columns.keys()
    except Exception:
        columns = []

    try:
        if "template_id" in columns:
            steps = StepModel.query.filter_by(template_id=template_id).all()
        elif "protocol_template_id" in columns:
            steps = StepModel.query.filter_by(protocol_template_id=template_id).all()
    except Exception:
        steps = []

    if not steps:
        return True

    steps.sort(key=lambda item: (_pf67_patch010c5_step_sort_value(item), getattr(item, "id", 0) or 0))
    return bool(steps and getattr(steps[0], "id", None) == getattr(step, "id", None))


@views_bp.route("/pf67/patch010c5/step-context")
def pf67_patch010c5_step_context():
    StepModel = _pf67_patch010c5_step_model()
    step_id = request.args.get("step_id", type=int)
    template_id = request.args.get("template_id", type=int)

    payload = {
        "ok": True,
        "is_first_step": False,
        "hide_timing": False,
        "is_procedure_reference": False,
    }

    if StepModel is not None and step_id:
        step = StepModel.query.get(step_id)

        if step:
            is_first = _pf67_patch010c5_is_first_step(step)
            payload["is_first_step"] = is_first
            payload["hide_timing"] = is_first
            payload["is_procedure_reference"] = bool(getattr(step, "procedure_template_id", None))
            payload["procedure_template_id"] = getattr(step, "procedure_template_id", None)
            return jsonify(payload)

    if StepModel is not None and template_id:
        steps = []

        try:
            columns = StepModel.__table__.columns.keys()
        except Exception:
            columns = []

        try:
            if "template_id" in columns:
                steps = StepModel.query.filter_by(template_id=template_id).all()
            elif "protocol_template_id" in columns:
                steps = StepModel.query.filter_by(protocol_template_id=template_id).all()
        except Exception:
            steps = []

        payload["is_first_step"] = len(steps) == 0
        payload["hide_timing"] = len(steps) == 0

    return jsonify(payload)


@views_bp.before_app_request
def pf67_patch010c5_redirect_procedure_reference_step_edits():
    path_value = request.path or ""
    lower_path = path_value.lower()

    if "procedure-reference" in lower_path:
        return None

    if "step" not in lower_path:
        return None

    if request.method not in ("GET", "POST"):
        return None

    StepModel = _pf67_patch010c5_step_model()

    if StepModel is None:
        return None

    candidate_numbers = [int(value) for value in _pf67_010c5_re.findall(r"\d+", path_value)]

    for key, value in request.values.items():
        try:
            if str(value).isdigit():
                candidate_numbers.append(int(value))
        except Exception:
            pass

    seen = set()

    for candidate_step_id in reversed(candidate_numbers):
        if candidate_step_id in seen:
            continue

        seen.add(candidate_step_id)

        try:
            step = StepModel.query.get(candidate_step_id)
        except Exception:
            step = None

        if not step:
            continue

        procedure_id = getattr(step, "procedure_template_id", None)

        if not procedure_id:
            continue

        template_id = _pf67_patch010c5_template_id_for_step(step) or (candidate_numbers[0] if candidate_numbers else 0)
        return_to = request.referrer or url_for("views.template_detail", template_id=template_id)
        step_title = request.args.get("step_title") or ""

        try:
            return redirect(
                url_for(
                    "views.pf67_patch010c2_procedure_reference_timing",
                    template_id=template_id,
                    step_id=candidate_step_id,
                    return_to=return_to,
                    step_title=step_title,
                )
            )
        except Exception:
            return None

    return None

# === PF67 PATCH 010C7 PROCEDURE CONTROL LINKS FIXED ===
import re as _pf67_010c7_re
from flask import request, jsonify, redirect, url_for, render_template
from .models import db, ProtocolTemplate


try:
    _pf67_patch010c7_edit_required = edit_required
except NameError:
    def _pf67_patch010c7_edit_required(func):
        return func


class _PF67Patch010C7DisplayStep:
    def __init__(self, source_step, overrides=None):
        self._source_step = source_step
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]

        return getattr(self._source_step, name)

    def __getitem__(self, name):
        return getattr(self, name)


def _pf67_patch010c7_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c7_step_sort_field(step):
    if hasattr(step, "sort_order"):
        return "sort_order"

    if hasattr(step, "position"):
        return "position"

    return ""


def _pf67_patch010c7_step_sort_value(step):
    try:
        return float(getattr(step, "sort_order", 0) or 0)
    except Exception:
        try:
            return float(getattr(step, "position", 0) or 0)
        except Exception:
            return float(getattr(step, "id", 0) or 0)


def _pf67_patch010c7_step_title(step, index=0):
    for name in ["name", "title"]:
        value = getattr(step, name, None)

        if value:
            return str(value)

    return "Step " + str(index + 1)


def _pf67_patch010c7_step_instructions_html(step):
    for name in ["instructions_html", "instructions", "description"]:
        value = getattr(step, name, None)

        if value:
            return value

    return ""


def _pf67_patch010c7_timing_overrides_from(step):
    names = [
        "check_minutes",
        "ideal_minutes",
        "limit_minutes",
        "risk_minutes",
        "failure_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "wait_minutes",
        "duration_minutes",
        "check_value",
        "ideal_value",
        "limit_value",
        "risk_value",
        "failure_value",
        "check_unit",
        "ideal_unit",
        "limit_unit",
        "risk_unit",
        "failure_unit",
    ]

    values = {}

    for name in names:
        if hasattr(step, name):
            try:
                values[name] = getattr(step, name)
            except Exception:
                pass

    return values


@views_bp.app_template_global("pf67_patch010b9_display_steps")
def pf67_patch010c7_display_steps(template):
    source_steps = list(getattr(template, "steps", []) or [])
    source_steps.sort(key=lambda step: (_pf67_patch010c7_step_sort_value(step), getattr(step, "id", 0) or 0))

    result = []
    procedure_instance_index = 0

    for parent_index, step in enumerate(source_steps):
        procedure_id = getattr(step, "procedure_template_id", None)
        step_kind = getattr(step, "step_kind", "") or ""

        if procedure_id or step_kind == "procedure":
            try:
                procedure_id_int = int(procedure_id or 0)
            except Exception:
                procedure_id_int = 0

            procedure = ProtocolTemplate.query.get(procedure_id_int) if procedure_id_int else None

            if not procedure:
                result.append(step)
                continue

            procedure_steps = list(getattr(procedure, "steps", []) or [])
            procedure_steps.sort(key=lambda proc_step: (_pf67_patch010c7_step_sort_value(proc_step), getattr(proc_step, "id", 0) or 0))
            parent_timing = _pf67_patch010c7_timing_overrides_from(step)
            total_proc_steps = len(procedure_steps)

            for proc_index, proc_step in enumerate(procedure_steps):
                title = _pf67_patch010c7_step_title(proc_step, proc_index)
                instructions_html = _pf67_patch010c7_step_instructions_html(proc_step)
                marker = (
                    '<span class="pf67-procedure-reference-marker" '
                    'data-pf67-reference-step-id="' + str(getattr(step, "id", "")) + '" '
                    'data-pf67-procedure-template-id="' + str(procedure.id) + '" '
                    'data-pf67-procedure-step-id="' + str(getattr(proc_step, "id", "")) + '" '
                    'hidden></span>'
                )
                wrapped_instructions = marker + '<div class="pf67-procedure-derived-instructions">' + str(instructions_html or "") + '</div>'
                is_first = proc_index == 0
                is_last = proc_index == (total_proc_steps - 1)

                overrides = {
                    "id": getattr(step, "id", None),
                    "name": title,
                    "title": title,
                    "instructions_html": wrapped_instructions,
                    "instructions": wrapped_instructions,
                    "is_procedure_derived": True,
                    "is_procedure_reference": False,
                    "procedure_template_id": procedure.id,
                    "procedure_step_id": getattr(proc_step, "id", None),
                    "procedure_reference_step_id": getattr(step, "id", None),
                    "procedure_instance_index": procedure_instance_index,
                    "is_first_procedure_derived": is_first,
                    "is_last_procedure_derived": is_last,
                    "pf67_allow_parent_insert_after": is_last,
                    "allow_insert_after": is_last,
                    "pf67_show_reorder_controls": False,
                    "allow_reorder": False,
                    "can_reorder": False,
                    "is_reorderable": False,
                    "sort_order": _pf67_patch010c7_step_sort_value(step) + ((proc_index + 1) / 1000.0),
                    "pf67_source_step": proc_step,
                    "pf67_parent_reference_step": step,
                }

                if is_first:
                    overrides.update(parent_timing)

                result.append(_PF67Patch010C7DisplayStep(proc_step, overrides))

            procedure_instance_index += 1
        else:
            try:
                setattr(step, "is_procedure_derived", False)
                setattr(step, "pf67_allow_parent_insert_after", True)
                setattr(step, "allow_insert_after", True)
                setattr(step, "pf67_show_reorder_controls", True)
                setattr(step, "allow_reorder", True)
                setattr(step, "can_reorder", True)
                setattr(step, "is_reorderable", True)
            except Exception:
                pass

            result.append(step)

    return result


def _pf67_patch010c7_template_id_for_step(step):
    for attr in ["template_id", "protocol_template_id"]:
        if hasattr(step, attr):
            try:
                value = getattr(step, attr)
                if value:
                    return int(value)
            except Exception:
                pass

    return None


def _pf67_patch010c7_steps_for_template(template_id):
    StepModel = _pf67_patch010c7_step_model()

    if StepModel is None:
        return []

    try:
        columns = StepModel.__table__.columns.keys()
    except Exception:
        columns = []

    try:
        if "template_id" in columns:
            steps = StepModel.query.filter_by(template_id=template_id).all()
        elif "protocol_template_id" in columns:
            steps = StepModel.query.filter_by(protocol_template_id=template_id).all()
        else:
            steps = []
    except Exception:
        steps = []

    steps.sort(key=lambda item: (_pf67_patch010c7_step_sort_value(item), getattr(item, "id", 0) or 0))
    return steps


def _pf67_patch010c7_timing_fields(step):
    field_defs = [
        ("check_minutes", "Check minutes"),
        ("ideal_minutes", "Ideal minutes"),
        ("limit_minutes", "Limit minutes"),
        ("risk_minutes", "Risk minutes"),
        ("failure_minutes", "Failure minutes"),
        ("minimum_minutes", "Minimum minutes"),
        ("maximum_minutes", "Maximum minutes"),
        ("wait_minutes", "Wait minutes"),
        ("duration_minutes", "Duration minutes"),
    ]

    fields = []

    for name, label in field_defs:
        if hasattr(step, name):
            try:
                value = getattr(step, name)
            except Exception:
                value = ""

            if value is None:
                value = ""

            fields.append({
                "name": name,
                "label": label,
                "value": value,
            })

    return fields


def _pf67_patch010c7_template_redirect_url(template_id):
    try:
        return url_for("views.template_detail", template_id=template_id)
    except Exception:
        return "/templates/" + str(template_id)


@views_bp.route("/protocols/<int:template_id>/procedure-reference/<int:step_id>/timing-c7", methods=["GET", "POST"])
@_pf67_patch010c7_edit_required
def pf67_patch010c7_procedure_reference_timing(template_id, step_id):
    StepModel = _pf67_patch010c7_step_model()

    if StepModel is None:
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    step = StepModel.query.get_or_404(step_id)
    procedure_id = getattr(step, "procedure_template_id", None)

    if not procedure_id:
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    procedure = ProtocolTemplate.query.get(procedure_id)
    return_to = request.args.get("return_to") or request.form.get("return_to") or request.referrer or _pf67_patch010c7_template_redirect_url(template_id)
    timing_fields = _pf67_patch010c7_timing_fields(step)

    if request.method == "POST":
        for field in timing_fields:
            raw_value = request.form.get(field["name"], "").strip()

            if raw_value == "":
                value = 0
            else:
                try:
                    value = int(float(raw_value))
                except Exception:
                    value = 0

            try:
                setattr(step, field["name"], value)
            except Exception:
                pass

        db.session.commit()
        return redirect(return_to)

    return render_template(
        "procedure_reference_timing_patch010c7.html",
        template=ProtocolTemplate.query.get(template_id),
        step=step,
        procedure=procedure,
        timing_fields=timing_fields,
        return_to=return_to,
    )


@views_bp.route("/protocols/<int:template_id>/procedure-reference/<int:step_id>/move-up-c7")
@_pf67_patch010c7_edit_required
def pf67_patch010c7_procedure_reference_move_up(template_id, step_id):
    return _pf67_patch010c7_move_reference(template_id, step_id, -1)


@views_bp.route("/protocols/<int:template_id>/procedure-reference/<int:step_id>/move-down-c7")
@_pf67_patch010c7_edit_required
def pf67_patch010c7_procedure_reference_move_down(template_id, step_id):
    return _pf67_patch010c7_move_reference(template_id, step_id, 1)


def _pf67_patch010c7_move_reference(template_id, step_id, direction):
    StepModel = _pf67_patch010c7_step_model()

    if StepModel is None:
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    step = StepModel.query.get_or_404(step_id)
    steps = _pf67_patch010c7_steps_for_template(template_id)
    index = None

    for idx, candidate in enumerate(steps):
        if getattr(candidate, "id", None) == getattr(step, "id", None):
            index = idx
            break

    if index is None:
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    target_index = index + direction

    if target_index < 0 or target_index >= len(steps):
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    target = steps[target_index]
    sort_field = _pf67_patch010c7_step_sort_field(step)

    if not sort_field or not hasattr(target, sort_field):
        return redirect(_pf67_patch010c7_template_redirect_url(template_id))

    current_value = getattr(step, sort_field, None)
    target_value = getattr(target, sort_field, None)

    try:
        setattr(step, sort_field, target_value)
        setattr(target, sort_field, current_value)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(request.referrer or _pf67_patch010c7_template_redirect_url(template_id))


@views_bp.route("/pf67/patch010c7/step-context")
def pf67_patch010c7_step_context():
    StepModel = _pf67_patch010c7_step_model()
    step_id = request.args.get("step_id", type=int)
    template_id = request.args.get("template_id", type=int)

    payload = {
        "ok": True,
        "is_first_step": False,
        "hide_timing": False,
        "is_procedure_reference": False,
    }

    if StepModel is not None and step_id:
        step = StepModel.query.get(step_id)

        if step:
            template_id = _pf67_patch010c7_template_id_for_step(step) or template_id
            steps = _pf67_patch010c7_steps_for_template(template_id) if template_id else []
            is_first = bool(steps and getattr(steps[0], "id", None) == getattr(step, "id", None))
            payload["is_first_step"] = is_first
            payload["hide_timing"] = is_first
            payload["is_procedure_reference"] = bool(getattr(step, "procedure_template_id", None))
            payload["procedure_template_id"] = getattr(step, "procedure_template_id", None)
            return jsonify(payload)

    if StepModel is not None and template_id:
        steps = _pf67_patch010c7_steps_for_template(template_id)
        payload["is_first_step"] = len(steps) == 0
        payload["hide_timing"] = len(steps) == 0

    return jsonify(payload)


@views_bp.before_app_request
def pf67_patch010c7_redirect_procedure_reference_step_edits():
    path_value = request.path or ""
    lower_path = path_value.lower()

    if "procedure-reference" in lower_path:
        return None

    if "step" not in lower_path:
        return None

    if request.method not in ("GET", "POST"):
        return None

    StepModel = _pf67_patch010c7_step_model()

    if StepModel is None:
        return None

    candidate_numbers = [int(value) for value in _pf67_010c7_re.findall(r"\d+", path_value)]

    for key, value in request.values.items():
        try:
            if str(value).isdigit():
                candidate_numbers.append(int(value))
        except Exception:
            pass

    seen = set()

    for candidate_step_id in reversed(candidate_numbers):
        if candidate_step_id in seen:
            continue

        seen.add(candidate_step_id)

        try:
            step = StepModel.query.get(candidate_step_id)
        except Exception:
            step = None

        if not step:
            continue

        procedure_id = getattr(step, "procedure_template_id", None)

        if not procedure_id:
            continue

        template_id = _pf67_patch010c7_template_id_for_step(step) or (candidate_numbers[0] if candidate_numbers else 0)
        return_to = request.referrer or _pf67_patch010c7_template_redirect_url(template_id)

        try:
            return redirect(
                url_for(
                    "views.pf67_patch010c7_procedure_reference_timing",
                    template_id=template_id,
                    step_id=candidate_step_id,
                    return_to=return_to,
                )
            )
        except Exception:
            return None

    return None

# === PF67 PATCH 010C8 INLINE PROCEDURE TIMING EDITOR ===
from flask import request, jsonify
from .models import db, ProtocolTemplate


try:
    _pf67_patch010c8_edit_required = edit_required
except NameError:
    def _pf67_patch010c8_edit_required(func):
        return func


def _pf67_patch010c8_step_model():
    try:
        return ProtocolTemplate.steps.property.mapper.class_
    except Exception:
        return None


def _pf67_patch010c8_allowed_timing_columns(step):
    # Normal PF67 timing-window labels. Storage columns may vary by revision.
    candidates = [
        {
            "key": "check",
            "label": "Check",
            "columns": ["check_minutes", "minimum_minutes"],
        },
        {
            "key": "ideal",
            "label": "Ideal",
            "columns": ["ideal_minutes", "wait_minutes", "duration_minutes"],
        },
        {
            "key": "limit",
            "label": "Limit",
            "columns": ["limit_minutes", "maximum_minutes"],
        },
        {
            "key": "risk",
            "label": "Risk",
            "columns": ["risk_minutes"],
        },
        {
            "key": "failure",
            "label": "Failure",
            "columns": ["failure_minutes"],
        },
    ]

    fields = []

    for item in candidates:
        selected_column = None

        for column in item["columns"]:
            if hasattr(step, column):
                selected_column = column
                break

        if not selected_column:
            continue

        try:
            minutes = int(getattr(step, selected_column) or 0)
        except Exception:
            minutes = 0

        value, unit = _pf67_patch010c8_minutes_to_value_unit(minutes)

        fields.append({
            "key": item["key"],
            "label": item["label"],
            "column": selected_column,
            "minutes": minutes,
            "value": value,
            "unit": unit,
        })

    return fields


def _pf67_patch010c8_minutes_to_value_unit(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return "", "hours"

    if minutes % 1440 == 0:
        return minutes // 1440, "days"

    if minutes % 60 == 0:
        return minutes // 60, "hours"

    return minutes, "minutes"


def _pf67_patch010c8_value_unit_to_minutes(value, unit):
    try:
        numeric = float(value)
    except Exception:
        numeric = 0

    if numeric < 0:
        numeric = 0

    unit = (unit or "minutes").lower().strip()

    if unit == "days":
        return int(round(numeric * 1440))

    if unit == "hours":
        return int(round(numeric * 60))

    return int(round(numeric))


@views_bp.route("/protocols/<int:template_id>/procedure-reference/<int:step_id>/timing-inline-c8", methods=["GET", "POST"])
@_pf67_patch010c8_edit_required
def pf67_patch010c8_procedure_reference_timing_inline(template_id, step_id):
    StepModel = _pf67_patch010c8_step_model()

    if StepModel is None:
        return jsonify({"ok": False, "error": "Step model was not detected."}), 500

    step = StepModel.query.get(step_id)

    if not step:
        return jsonify({"ok": False, "error": "Procedure reference step was not found."}), 404

    procedure_id = getattr(step, "procedure_template_id", None)

    if not procedure_id:
        return jsonify({"ok": False, "error": "This step is not a Procedure reference."}), 400

    procedure = ProtocolTemplate.query.get(procedure_id)
    fields = _pf67_patch010c8_allowed_timing_columns(step)

    allowed_columns = set([field["column"] for field in fields])

    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict(flat=False)
        incoming_fields = data.get("fields", [])

        if isinstance(incoming_fields, dict):
            incoming_fields = list(incoming_fields.values())

        if not isinstance(incoming_fields, list):
            incoming_fields = []

        for item in incoming_fields:
            if not isinstance(item, dict):
                continue

            column = item.get("column")
            value = item.get("value", "")
            unit = item.get("unit", "minutes")

            if column not in allowed_columns:
                continue

            minutes = _pf67_patch010c8_value_unit_to_minutes(value, unit)

            try:
                setattr(step, column, minutes)
            except Exception:
                pass

        db.session.commit()
        fields = _pf67_patch010c8_allowed_timing_columns(step)

    return jsonify({
        "ok": True,
        "procedure_id": procedure_id,
        "procedure_name": getattr(procedure, "name", "Procedure") if procedure else "Procedure",
        "fields": fields,
    })

# === PF67 PATCH 011A FLOW STEP SEQUENCE ORDERING ===
def _pf67_patch011a_step_order_value(step):
    # Explicit Flow sequence first. Due date/status/title are intentionally not first.
    for name in ["sequence_order", "display_order", "step_sequence", "sort_order", "position"]:
        if hasattr(step, name):
            try:
                value = getattr(step, name)

                if value is not None:
                    numeric = float(value)

                    if numeric != 0:
                        return numeric
            except Exception:
                pass

    try:
        return float(getattr(step, "id", 0) or 0)
    except Exception:
        return 0.0


def pf67_patch011a_ordered_flow_steps(source):
    # Accept either a Flow/Job object with .steps or a list/query of steps.
    try:
        if hasattr(source, "steps"):
            steps = list(getattr(source, "steps") or [])
        else:
            steps = list(source or [])
    except Exception:
        steps = []

    return sorted(steps, key=lambda step: (_pf67_patch011a_step_order_value(step), getattr(step, "id", 0) or 0))


try:
    views_bp.add_app_template_global(pf67_patch011a_ordered_flow_steps, "pf67_patch011a_ordered_flow_steps")
except Exception:
    try:
        views_bp.app_template_global("pf67_patch011a_ordered_flow_steps")(pf67_patch011a_ordered_flow_steps)
    except Exception:
        pass

# === PF67 PATCH 011A2 FLOW CREATION SEQUENCE FIX ===
def pf67_patch011a2_sequence_flow_steps(template):
    # Return the expanded Protocol/Procedure step list in natural sequence.
    #
    # Important:
    # - Do NOT sort the expanded list by step.sort_order here.
    # - Procedure-derived items can carry Procedure-internal sort_order values.
    #   Sorting the flat expanded list by those values groups repeated Procedure
    #   steps together and breaks the user's intended Protocol sequence.
    # - Preserve the order produced by pf67_patch010c_flow_steps(template), then
    #   attach transient ordering values that Flow creation snapshots into JobStep.
    try:
        expanded_steps = list(pf67_patch010c_flow_steps(template))
    except Exception:
        expanded_steps = list(getattr(template, "steps", []) or [])

        try:
            expanded_steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
        except Exception:
            pass

    for index, step in enumerate(expanded_steps):
        try:
            setattr(step, "_pf67_flow_sort_order", (index + 1) * 10)
        except Exception:
            pass

        try:
            setattr(step, "_pf67_flow_sequence_order", (index + 1) * 1000)
        except Exception:
            pass

    return expanded_steps

# === PF67 PATCH 011CD KEYWORD FILTERS AND DURATION PILLS ===
from flask import jsonify as _pf67_patch011cd_jsonify


def _pf67_patch011cd_minutes_from_value_unit(value, unit):
    try:
        numeric = float(value or 0)
    except Exception:
        numeric = 0

    if numeric <= 0:
        return 0

    unit = str(unit or "minutes").strip().lower()

    if unit.startswith("day"):
        return int(round(numeric * 1440))

    if unit.startswith("hour"):
        return int(round(numeric * 60))

    return int(round(numeric))


def _pf67_patch011cd_minutes_for_step(step):
    # Estimate anchor: Ideal first, then wait/duration fallback. Check/Limit/Risk/Failure
    # are not estimate anchors.
    for name in ["ideal_minutes", "wait_minutes", "duration_minutes"]:
        if hasattr(step, name):
            try:
                value = int(getattr(step, name) or 0)
            except Exception:
                value = 0

            if value > 0:
                return value

    # Some older UI variants store value + unit.
    value_unit_pairs = [
        ("ideal_value", "ideal_unit"),
        ("wait_value", "wait_unit"),
        ("duration_value", "duration_unit"),
    ]

    for value_name, unit_name in value_unit_pairs:
        if hasattr(step, value_name):
            try:
                value = getattr(step, value_name)
                unit = getattr(step, unit_name, "minutes") if hasattr(step, unit_name) else "minutes"
            except Exception:
                value = 0
                unit = "minutes"

            minutes = _pf67_patch011cd_minutes_from_value_unit(value, unit)

            if minutes > 0:
                return minutes

    return 0


def pf67_patch011cd_template_duration_minutes(template):
    try:
        steps = list(pf67_patch010b9_display_steps(template))
    except Exception:
        try:
            steps = list(pf67_patch010c_flow_steps(template))
        except Exception:
            steps = list(getattr(template, "steps", []) or [])

            try:
                steps.sort(key=lambda step: (getattr(step, "sort_order", 0) or 0, getattr(step, "id", 0) or 0))
            except Exception:
                pass

    total = 0

    for index, step in enumerate(steps):
        # PF67 model: first displayed step timing is irrelevant.
        if index == 0:
            continue

        total += _pf67_patch011cd_minutes_for_step(step)

    return int(total or 0)


def pf67_patch011cd_duration_label(minutes):
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0

    if minutes <= 0:
        return ""

    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return str(minutes) + " " + unit

    if minutes < 2880:
        hours = minutes // 60
        rem_minutes = minutes % 60

        label = str(hours) + " " + ("hour" if hours == 1 else "hours")

        if rem_minutes:
            label += " " + str(rem_minutes) + " " + ("minute" if rem_minutes == 1 else "minutes")

        return label

    days = minutes // 1440
    rem = minutes % 1440
    hours = rem // 60
    rem_minutes = rem % 60

    label = str(days) + " " + ("day" if days == 1 else "days")

    if hours:
        label += " " + str(hours) + " " + ("hour" if hours == 1 else "hours")

    if rem_minutes:
        label += " " + str(rem_minutes) + " " + ("minute" if rem_minutes == 1 else "minutes")

    return label


def pf67_patch011cd_template_duration_label(template):
    return pf67_patch011cd_duration_label(pf67_patch011cd_template_duration_minutes(template))


try:
    views_bp.add_app_template_global(pf67_patch011cd_template_duration_minutes, "pf67_patch011cd_template_duration_minutes")
    views_bp.add_app_template_global(pf67_patch011cd_template_duration_label, "pf67_patch011cd_template_duration_label")
except Exception:
    try:
        views_bp.app_template_global("pf67_patch011cd_template_duration_minutes")(pf67_patch011cd_template_duration_minutes)
        views_bp.app_template_global("pf67_patch011cd_template_duration_label")(pf67_patch011cd_template_duration_label)
    except Exception:
        pass


@views_bp.route("/pf67/patch011cd/template-durations")
def pf67_patch011cd_template_durations():
    payload = {}

    try:
        templates = ProtocolTemplate.query.all()
    except Exception:
        templates = []

    for template in templates:
        try:
            minutes = pf67_patch011cd_template_duration_minutes(template)
            label = pf67_patch011cd_duration_label(minutes)

            payload[str(template.id)] = {
                "minutes": minutes,
                "label": label,
            }
        except Exception:
            payload[str(getattr(template, "id", ""))] = {
                "minutes": 0,
                "label": "",
            }

    return _pf67_patch011cd_jsonify({
        "ok": True,
        "durations": payload,
    })
