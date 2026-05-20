from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ProtocolTemplate(db.Model):
    protocol_role = db.Column(db.String(24), nullable=False, default="protocol")
    can_start_flow = db.Column(db.Boolean, nullable=False, default=True)
    __tablename__ = "protocol_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(120), default="")
    is_private = db.Column(db.Boolean, nullable=False, default=False)
    current_version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    steps = db.relationship(
        "StepTemplate",
        backref="template",
        cascade="all, delete-orphan",
        order_by="StepTemplate.sort_order"
    )


class StepTemplate(db.Model):
    step_kind = db.Column(db.String(24), nullable=False, default="step")
    procedure_template_id = db.Column(db.Integer, nullable=True)
    procedure_snapshot_title = db.Column(db.String(255), nullable=True)
    procedure_snapshot_description = db.Column(db.Text, nullable=True)
    procedure_snapshot_minutes = db.Column(db.Integer, nullable=False, default=0)
    __tablename__ = "step_templates"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("protocol_templates.id"), nullable=False)

    sort_order = db.Column(db.Integer, default=1)
    name = db.Column(db.String(200), nullable=False)
    step_type = db.Column(db.String(50), default="action")

    instructions_html = db.Column(db.Text, default="")

    minimum_minutes = db.Column(db.Integer, nullable=True)
    ideal_minutes = db.Column(db.Integer, nullable=True)
    limit_minutes = db.Column(db.Integer, nullable=True)
    detrimental_minutes = db.Column(db.Integer, nullable=True)
    failure_minutes = db.Column(db.Integer, nullable=True)

    estimated_duration_minutes = db.Column(db.Integer, nullable=True)
    context_tag = db.Column(db.String(100), default="")


class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("protocol_templates.id"), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    template_version = db.Column(db.Integer, default=1)

    status = db.Column(db.String(50), default="active")

    priority = db.Column(db.String(20), default="Normal")
    is_private = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    allow_ai_read = db.Column(db.Boolean, default=True)
    allow_ai_suggest = db.Column(db.Boolean, default=True)
    allow_ai_write = db.Column(db.Boolean, default=False)

    template = db.relationship("ProtocolTemplate")
    steps = db.relationship(
        "JobStep",
        backref="job",
        cascade="all, delete-orphan",
        order_by="JobStep.sequence_order, JobStep.sort_order, JobStep.id"
    )
    notes = db.relationship(
        "JobNote",
        backref="job",
        cascade="all, delete-orphan",
        order_by="JobNote.event_at"
    )


class JobStep(db.Model):
    sequence_order = db.Column(db.Integer, nullable=False, default=0)
    source_protocol_step_id = db.Column(db.Integer, nullable=True)
    source_procedure_template_id = db.Column(db.Integer, nullable=True)
    source_procedure_step_id = db.Column(db.Integer, nullable=True)
    source_procedure_reference_step_id = db.Column(db.Integer, nullable=True)
    is_procedure_derived = db.Column(db.Boolean, nullable=False, default=False)
    __tablename__ = "job_steps"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    source_step_template_id = db.Column(db.Integer, nullable=True)

    sort_order = db.Column(db.Integer, default=1)
    name = db.Column(db.String(200), nullable=False)
    step_type = db.Column(db.String(50), default="action")

    instructions_html = db.Column(db.Text, default="")
    context_tag = db.Column(db.String(100), default="")

    minimum_minutes = db.Column(db.Integer, nullable=True)
    ideal_minutes = db.Column(db.Integer, nullable=True)
    limit_minutes = db.Column(db.Integer, nullable=True)
    detrimental_minutes = db.Column(db.Integer, nullable=True)
    failure_minutes = db.Column(db.Integer, nullable=True)
    estimated_duration_minutes = db.Column(db.Integer, nullable=True)

    anchor_time = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    status_override = db.Column(db.String(50), default="")
    notes_html = db.Column(db.Text, default="")


class JobNote(db.Model):
    __tablename__ = "job_notes"

    id = db.Column(db.Integer, primary_key=True)

    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    job_step_id = db.Column(db.Integer, db.ForeignKey("job_steps.id"), nullable=True)

    note_type = db.Column(db.String(50), default="note")
    title = db.Column(db.String(200), default="")
    note_html = db.Column(db.Text, default="")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    event_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    preview_image_url = db.Column(db.Text, default="")

    step = db.relationship("JobStep", backref="timeline_notes")


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    job_step_id = db.Column(db.Integer, db.ForeignKey("job_steps.id"), nullable=True)
    note_id = db.Column(db.Integer, db.ForeignKey("job_notes.id"), nullable=True)

    filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(100), default="")
    caption = db.Column(db.String(255), default="")
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    note = db.relationship("JobNote", backref="attachments")

class ProtocolAttachment(db.Model):
    __tablename__ = "protocol_attachments"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("protocol_templates.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(100), default="")
    title = db.Column(db.String(200), default="")
    caption = db.Column(db.Text, default="")
    attachment_type = db.Column(db.String(80), default="Reference")
    sort_order = db.Column(db.Integer, default=1)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    template = db.relationship("ProtocolTemplate", backref="protocol_attachments")

# === PF67 PATCH 011A FLOW STEP SEQUENCE ORDERING ===
try:
    from sqlalchemy import event as _pf67_patch011a_event
    from sqlalchemy.orm import Session as _PF67Patch011ASession
except Exception:
    _pf67_patch011a_event = None
    _PF67Patch011ASession = None


def _pf67_patch011a_is_flow_step_object(obj):
    name = obj.__class__.__name__.lower()

    if "step" not in name:
        return False

    if not hasattr(obj, "sequence_order"):
        return False

    return hasattr(obj, "job_id") or hasattr(obj, "flow_id") or hasattr(obj, "job") or hasattr(obj, "flow")


def _pf67_patch011a_parent_key(obj):
    for attr in ["flow_id", "job_id"]:
        if hasattr(obj, attr):
            try:
                value = getattr(obj, attr)
                if value:
                    return attr + ":" + str(value)
            except Exception:
                pass

    for attr in ["flow", "job"]:
        if hasattr(obj, attr):
            try:
                value = getattr(obj, attr)
                if value is not None:
                    return attr + ":" + str(id(value))
            except Exception:
                pass

    return "orphan:" + obj.__class__.__name__


if _pf67_patch011a_event is not None and _PF67Patch011ASession is not None:
    @_pf67_patch011a_event.listens_for(_PF67Patch011ASession, "before_flush")
    def _pf67_patch011a_assign_sequence_order(session, flush_context, instances):
        counters = {}

        for obj in list(session.new):
            if not _pf67_patch011a_is_flow_step_object(obj):
                continue

            try:
                existing = int(getattr(obj, "sequence_order") or 0)
            except Exception:
                existing = 0

            if existing:
                continue

            parent_key = _pf67_patch011a_parent_key(obj)
            counters[parent_key] = counters.get(parent_key, 0) + 1

            try:
                setattr(obj, "sequence_order", counters[parent_key] * 1000)
            except Exception:
                pass
