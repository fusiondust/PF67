from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ProtocolTemplate(db.Model):
    __tablename__ = "protocol_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    current_version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    steps = db.relationship(
        "StepTemplate",
        backref="template",
        cascade="all, delete-orphan",
        order_by="StepTemplate.sort_order"
    )


class StepTemplate(db.Model):
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
        order_by="JobStep.sort_order"
    )
    notes = db.relationship(
        "JobNote",
        backref="job",
        cascade="all, delete-orphan",
        order_by="JobNote.event_at"
    )


class JobStep(db.Model):
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
