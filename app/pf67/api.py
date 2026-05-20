from flask import Blueprint, jsonify
from .models import ProtocolTemplate, Job, JobStep
from .services import calculate_step_status, status_rank, job_to_dict, step_to_dict

api_bp = Blueprint("api", __name__)


@api_bp.route("/ping")
def ping():
    return jsonify({
        "ok": True,
        "app": "PF67",
        "revision": "Rev 7",
        "description": "Protocol-based calendar automation for craft production",
        "api_mode": "read-only"
    })


@api_bp.route("/templates")
def templates():
    items = ProtocolTemplate.query.order_by(ProtocolTemplate.name).all()

    data = []

    for template in items:
        data.append({
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "current_version": template.current_version,
            "created_at": template.created_at.isoformat(),
            "step_count": len(template.steps)
        })

    return jsonify(data)


@api_bp.route("/templates/<int:template_id>")
def template_detail(template_id):
    template = ProtocolTemplate.query.get_or_404(template_id)

    data = {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "current_version": template.current_version,
        "created_at": template.created_at.isoformat(),
        "steps": []
    }

    for step in template.steps:
        data["steps"].append({
            "id": step.id,
            "sort_order": step.sort_order,
            "name": step.name,
            "step_type": step.step_type,
            "context_tag": step.context_tag,
            "instructions_html": step.instructions_html,
            "minimum_minutes": step.minimum_minutes,
            "ideal_minutes": step.ideal_minutes,
            "limit_minutes": step.limit_minutes,
            "detrimental_minutes": step.detrimental_minutes,
            "failure_minutes": step.failure_minutes,
            "estimated_duration_minutes": step.estimated_duration_minutes
        })

    return jsonify(data)


@api_bp.route("/jobs")
def jobs():
    items = Job.query.order_by(Job.started_at.desc()).all()

    return jsonify([job_to_dict(job, include_steps=False) for job in items])


@api_bp.route("/jobs/active")
def active_jobs():
    items = Job.query.filter_by(status="active").order_by(Job.started_at.desc()).all()

    return jsonify([job_to_dict(job, include_steps=True) for job in items])


@api_bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = Job.query.get_or_404(job_id)
    data = job_to_dict(job, include_steps=True)
    data["notes"] = [note_to_dict(note) for note in job.notes]

    return jsonify(data)


@api_bp.route("/jobs/<int:job_id>/steps")
def job_steps(job_id):
    job = Job.query.get_or_404(job_id)

    return jsonify([step_to_dict(step) for step in job.steps])


@api_bp.route("/jobs/<int:job_id>/notes")
def job_notes(job_id):
    job = Job.query.get_or_404(job_id)

    data = []

    for note in job.notes:
        data.append(note_to_dict(note))

    return jsonify(data)


@api_bp.route("/tasks/active")
def active_tasks():
    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    data = []

    for step in steps:
        status = calculate_step_status(step)

        if status != "completed":
            row = step_to_dict(step)
            row["rank"] = status_rank(status)
            data.append(row)

    data.sort(key=lambda item: item["rank"])

    return jsonify(data)


@api_bp.route("/tasks/due")
def due_tasks():
    return filtered_tasks(["due", "ideal", "late", "risky", "critical"])


@api_bp.route("/tasks/checkable")
def checkable_tasks():
    return filtered_tasks(["checkable", "ideal", "due", "late", "risky", "critical"])


@api_bp.route("/tasks/critical")
def critical_tasks():
    return filtered_tasks(["risky", "critical"])


def filtered_tasks(statuses):
    steps = JobStep.query.join(Job).filter(Job.status == "active").all()

    data = []

    for step in steps:
        status = calculate_step_status(step)

        if status in statuses:
            data.append(step_to_dict(step))

    data.sort(key=lambda item: status_rank(item["status"]))

    return jsonify(data)


def note_to_dict(note):
    return {
        "id": note.id,
        "job_id": note.job_id,
        "job_step_id": note.job_step_id,
        "step_name": note.step.name if note.step else None,
        "note_type": note.note_type,
        "title": note.title,
        "note_html": note.note_html,
        "created_at": note.created_at.isoformat(),
        "event_at": note.event_at.isoformat() if note.event_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "preview_image_url": note.preview_image_url,
        "edit_url": "/notes/" + str(note.id) + "/edit"
    }
