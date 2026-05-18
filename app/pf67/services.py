from datetime import datetime, timedelta
from .models import db, Job, JobStep


def minutes_from_form(value, unit):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    number = int(value)

    if unit == "days":
        return number * 1440

    if unit == "hours":
        return number * 60

    return number


def human_minutes(minutes):
    if minutes is None:
        return ""

    minutes = int(minutes)

    if minutes < 60:
        if minutes == 1:
            return "1 minute"

        return str(minutes) + " minutes"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours < 48:
        parts = []

        if hours == 1:
            parts.append("1 hour")
        else:
            parts.append(str(hours) + " hours")

        if remaining_minutes:
            if remaining_minutes == 1:
                parts.append("1 minute")
            else:
                parts.append(str(remaining_minutes) + " minutes")

        return " ".join(parts)

    days = minutes // 1440
    remainder = minutes % 1440
    rem_hours = remainder // 60
    rem_minutes = remainder % 60

    parts = []

    if days == 1:
        parts.append("1 day")
    else:
        parts.append(str(days) + " days")

    if rem_hours:
        if rem_hours == 1:
            parts.append("1 hour")
        else:
            parts.append(str(rem_hours) + " hours")

    if rem_minutes:
        if rem_minutes == 1:
            parts.append("1 minute")
        else:
            parts.append(str(rem_minutes) + " minutes")

    return " ".join(parts)


def time_after(anchor_time, minutes):
    if minutes is None:
        return None

    return anchor_time + timedelta(minutes=minutes)


def calculate_step_status(step, now=None):
    if now is None:
        now = datetime.utcnow()

    if step.completed_at:
        return "completed"

    if step.status_override:
        return step.status_override

    elapsed_minutes = int((now - step.anchor_time).total_seconds() // 60)

    if step.failure_minutes is not None and elapsed_minutes >= step.failure_minutes:
        return "critical"

    if step.detrimental_minutes is not None and elapsed_minutes >= step.detrimental_minutes:
        return "risky"

    if step.limit_minutes is not None and elapsed_minutes > step.limit_minutes:
        return "late"

    if step.ideal_minutes is not None and elapsed_minutes >= step.ideal_minutes:
        return "ideal"

    if step.minimum_minutes is not None and elapsed_minutes >= step.minimum_minutes:
        return "checkable"

    return "waiting"


def status_label(status):
    labels = {
        "waiting": "Waiting",
        "checkable": "Checkable",
        "ideal": "Ideal",
        "due": "Due",
        "late": "Late",
        "risky": "Risky",
        "critical": "Critical",
        "completed": "Completed",
        "skipped": "Skipped",
        "failed": "Failed"
    }

    return labels.get(status, status)


def status_rank(status):
    ranks = {
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

    return ranks.get(status, 99)


def timing_sanity_messages(step):
    checks = [
        ("Minimum", step.minimum_minutes, "Ideal", step.ideal_minutes),
        ("Ideal", step.ideal_minutes, "Limit", step.limit_minutes),
        ("Limit", step.limit_minutes, "Detrimental", step.detrimental_minutes),
        ("Detrimental", step.detrimental_minutes, "Failure", step.failure_minutes)
    ]

    messages = []

    for left_label, left_value, right_label, right_value in checks:
        if left_value is not None and right_value is not None:
            if left_value > right_value:
                messages.append(left_label + " is greater than " + right_label + ".")

    return messages


def create_job_from_template(template, job_name):
    job = Job(
        template_id=template.id,
        name=job_name,
        template_version=template.current_version,
        allow_ai_read=True,
        allow_ai_suggest=True,
        allow_ai_write=False
    )

    db.session.add(job)
    db.session.flush()

    anchor_time = job.started_at

    for step_template in template.steps:
        job_step = JobStep(
            job_id=job.id,
            source_step_template_id=step_template.id,
            sort_order=step_template.sort_order,
            name=step_template.name,
            step_type=step_template.step_type,
            instructions_html=step_template.instructions_html,
            context_tag=step_template.context_tag,
            minimum_minutes=step_template.minimum_minutes,
            ideal_minutes=step_template.ideal_minutes,
            limit_minutes=step_template.limit_minutes,
            detrimental_minutes=step_template.detrimental_minutes,
            failure_minutes=step_template.failure_minutes,
            estimated_duration_minutes=step_template.estimated_duration_minutes,
            anchor_time=anchor_time
        )

        db.session.add(job_step)

        if step_template.ideal_minutes is not None:
            anchor_time = anchor_time + timedelta(minutes=step_template.ideal_minutes)

    db.session.commit()

    return job


def step_to_dict(step):
    status = calculate_step_status(step)

    return {
        "id": step.id,
        "job_id": step.job_id,
        "sort_order": step.sort_order,
        "name": step.name,
        "step_type": step.step_type,
        "status": status,
        "status_label": status_label(status),
        "context_tag": step.context_tag,
        "instructions_html": step.instructions_html,
        "notes_html": step.notes_html,
        "anchor_time": step.anchor_time.isoformat(),
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        "minimum_minutes": step.minimum_minutes,
        "ideal_minutes": step.ideal_minutes,
        "limit_minutes": step.limit_minutes,
        "detrimental_minutes": step.detrimental_minutes,
        "failure_minutes": step.failure_minutes,
        "estimated_duration_minutes": step.estimated_duration_minutes,
        "minimum_display": human_minutes(step.minimum_minutes),
        "ideal_display": human_minutes(step.ideal_minutes),
        "limit_display": human_minutes(step.limit_minutes),
        "detrimental_display": human_minutes(step.detrimental_minutes),
        "failure_display": human_minutes(step.failure_minutes),
        "estimated_duration_display": human_minutes(step.estimated_duration_minutes),
        "sanity_messages": timing_sanity_messages(step)
    }


def job_to_dict(job, include_steps=True):
    data = {
        "id": job.id,
        "name": job.name,
        "template_id": job.template_id,
        "template_version": job.template_version,
        "status": job.status,
        "started_at": job.started_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "allow_ai_read": job.allow_ai_read,
        "allow_ai_suggest": job.allow_ai_suggest,
        "allow_ai_write": job.allow_ai_write
    }

    if include_steps:
        data["steps"] = [step_to_dict(step) for step in job.steps]

    return data
