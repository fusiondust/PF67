from datetime import timedelta
from flask import Blueprint, Response, url_for
from .models import Job, JobStep
from .services import calculate_step_status, status_label, time_after

calendar_bp = Blueprint("calendar", __name__)


def ics_escape(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace(",", "\\,")
    value = value.replace(";", "\\;")
    value = value.replace("\n", "\\n")

    return value


def format_ics_datetime(value):
    return value.strftime("%Y%m%dT%H%M%SZ")


@calendar_bp.route("/jobs.ics")
def jobs_ics():
    lines = []
    lines.append("BEGIN:VCALENDAR")
    lines.append("VERSION:2.0")
    lines.append("PRODID:-//PF67//ProtocolFlow67//EN")
    lines.append("CALSCALE:GREGORIAN")

    jobs = Job.query.filter_by(status="active").all()

    for job in jobs:
        for step in job.steps:
            if step.completed_at:
                continue

            event_time = time_after(step.anchor_time, step.ideal_minutes)

            if event_time is None:
                event_time = step.anchor_time

            end_time = event_time + timedelta(minutes=step.estimated_duration_minutes or 30)

            status = calculate_step_status(step)

            description = ""
            description += "Job: " + job.name + "\\n"
            description += "Step: " + step.name + "\\n"
            description += "Status: " + status_label(status) + "\\n"
            description += "Context: " + (step.context_tag or "") + "\\n"
            description += "\\n"
            description += "Open PF67 to complete this step and add notes."

            lines.append("BEGIN:VEVENT")
            lines.append("UID:pf67-step-" + str(step.id) + "@pf67")
            lines.append("DTSTAMP:" + format_ics_datetime(event_time))
            lines.append("DTSTART:" + format_ics_datetime(event_time))
            lines.append("DTEND:" + format_ics_datetime(end_time))
            lines.append("SUMMARY:" + ics_escape("PF67: " + step.name))
            lines.append("DESCRIPTION:" + ics_escape(description))
            lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    body = "\r\n".join(lines) + "\r\n"

    return Response(body, mimetype="text/calendar")
