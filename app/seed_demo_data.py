from datetime import datetime, timedelta

from pf67 import create_app
from pf67.models import db, ProtocolTemplate, StepTemplate, Job, JobStep, JobNote


def add_template(name, description, steps):
    template = ProtocolTemplate(
        name=name,
        description=description,
        current_version=1,
        created_at=datetime.utcnow()
    )

    db.session.add(template)
    db.session.flush()

    for index, step_data in enumerate(steps, start=1):
        step = StepTemplate(
            template_id=template.id,
            sort_order=index,
            name=step_data["name"],
            step_type=step_data["step_type"],
            instructions_html=step_data["instructions_html"],
            minimum_minutes=step_data.get("minimum_minutes"),
            ideal_minutes=step_data.get("ideal_minutes"),
            limit_minutes=step_data.get("limit_minutes"),
            detrimental_minutes=step_data.get("detrimental_minutes"),
            failure_minutes=step_data.get("failure_minutes"),
            estimated_duration_minutes=step_data.get("estimated_duration_minutes"),
            context_tag=step_data.get("context_tag", "")
        )

        db.session.add(step)

    db.session.commit()

    return template


def add_job(template, name, started_at):
    job = Job(
        template_id=template.id,
        name=name,
        template_version=template.current_version,
        status="active",
        started_at=started_at,
        allow_ai_read=True,
        allow_ai_suggest=True,
        allow_ai_write=False
    )

    db.session.add(job)
    db.session.flush()

    anchor_time = started_at

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


def add_note(job, step, title, html, created_at):
    note = JobNote(
        job_id=job.id,
        job_step_id=step.id if step else None,
        note_type="note",
        title=title,
        note_html=html,
        created_at=created_at,
        updated_at=created_at
    )

    db.session.add(note)
    db.session.commit()

    return note


def main():
    app = create_app()

    with app.app_context():
        existing = ProtocolTemplate.query.filter(ProtocolTemplate.name.like("PF67 Demo%")).first()

        if existing:
            print("Demo data already exists. No new records were created.")
            print("Delete PF67 Demo templates manually if you want to reseed.")
            return

        now = datetime.utcnow()

        wine_template = add_template(
            "PF67 Demo - Blackberry Wine Batch",
            "Demo protocol for a fruit wine batch with fermentation checks and racking.",
            [
                {
                    "name": "Sanitize fermenter and tools",
                    "step_type": "action",
                    "instructions_html": "<p>Sanitize fermenter, spoon, airlock, hydrometer, and funnel.</p>",
                    "estimated_duration_minutes": 45,
                    "context_tag": "Kitchen"
                },
                {
                    "name": "Pitch yeast and start primary fermentation",
                    "step_type": "action",
                    "instructions_html": "<p>Pitch yeast, seal fermenter, and record starting gravity.</p>",
                    "estimated_duration_minutes": 30,
                    "context_tag": "Kitchen"
                },
                {
                    "name": "Check primary fermentation",
                    "step_type": "wait",
                    "instructions_html": "<p>Check bubbling, smell, temperature, and gravity. Proceed if fermentation is healthy.</p>",
                    "minimum_minutes": 5 * 1440,
                    "ideal_minutes": 7 * 1440,
                    "limit_minutes": 8 * 1440,
                    "detrimental_minutes": 10 * 1440,
                    "failure_minutes": 12 * 1440,
                    "estimated_duration_minutes": 15,
                    "context_tag": "Cellar"
                },
                {
                    "name": "Rack to secondary",
                    "step_type": "action",
                    "instructions_html": "<p>Rack wine off sediment into secondary vessel.</p>",
                    "minimum_minutes": 1 * 1440,
                    "ideal_minutes": 2 * 1440,
                    "limit_minutes": 3 * 1440,
                    "detrimental_minutes": 4 * 1440,
                    "failure_minutes": 6 * 1440,
                    "estimated_duration_minutes": 60,
                    "context_tag": "Cellar"
                }
            ]
        )

        beer_template = add_template(
            "PF67 Demo - Pale Ale Fermentation",
            "Demo protocol for a beer fermentation and dry-hop schedule.",
            [
                {
                    "name": "Brew day cleanup and yeast pitch",
                    "step_type": "action",
                    "instructions_html": "<p>Pitch yeast after wort is cooled. Record temperature and original gravity.</p>",
                    "estimated_duration_minutes": 60,
                    "context_tag": "Kitchen"
                },
                {
                    "name": "Fermentation activity check",
                    "step_type": "wait",
                    "instructions_html": "<p>Look for krausen, airlock activity, and temperature stability.</p>",
                    "minimum_minutes": 24 * 60,
                    "ideal_minutes": 36 * 60,
                    "limit_minutes": 48 * 60,
                    "detrimental_minutes": 72 * 60,
                    "failure_minutes": 96 * 60,
                    "estimated_duration_minutes": 10,
                    "context_tag": "Brew Area"
                },
                {
                    "name": "Dry hop addition",
                    "step_type": "wait",
                    "instructions_html": "<p>Add dry hops if fermentation is slowing and aroma target is appropriate.</p>",
                    "minimum_minutes": 5 * 1440,
                    "ideal_minutes": 7 * 1440,
                    "limit_minutes": 9 * 1440,
                    "detrimental_minutes": 11 * 1440,
                    "failure_minutes": 14 * 1440,
                    "estimated_duration_minutes": 15,
                    "context_tag": "Brew Area"
                }
            ]
        )

        mycology_template = add_template(
            "PF67 Demo - Blue Oyster Grain Spawn",
            "Demo protocol for inoculation, colonization check, shake, and transfer planning.",
            [
                {
                    "name": "Inoculate grain jar",
                    "step_type": "action",
                    "instructions_html": "<p>Inoculate sterile grain jar using clean technique. Label jar with strain and date.</p>",
                    "estimated_duration_minutes": 20,
                    "context_tag": "Lab"
                },
                {
                    "name": "Early colonization check",
                    "step_type": "wait",
                    "instructions_html": "<p>Check for healthy white growth and look for contamination. Add photo if useful.</p>",
                    "minimum_minutes": 4 * 1440,
                    "ideal_minutes": 5 * 1440,
                    "limit_minutes": 7 * 1440,
                    "detrimental_minutes": 9 * 1440,
                    "failure_minutes": 12 * 1440,
                    "estimated_duration_minutes": 10,
                    "context_tag": "Lab"
                },
                {
                    "name": "Shake jar",
                    "step_type": "action",
                    "instructions_html": "<p>Shake jar if colonization is strong enough to distribute mycelium.</p>",
                    "minimum_minutes": 1 * 1440,
                    "ideal_minutes": 2 * 1440,
                    "limit_minutes": 3 * 1440,
                    "detrimental_minutes": 4 * 1440,
                    "failure_minutes": 6 * 1440,
                    "estimated_duration_minutes": 10,
                    "context_tag": "Lab"
                },
                {
                    "name": "Plan substrate transfer",
                    "step_type": "observation",
                    "instructions_html": "<p>Inspect jar and decide whether to transfer to substrate.</p>",
                    "minimum_minutes": 3 * 1440,
                    "ideal_minutes": 5 * 1440,
                    "limit_minutes": 7 * 1440,
                    "detrimental_minutes": 9 * 1440,
                    "failure_minutes": 12 * 1440,
                    "estimated_duration_minutes": 15,
                    "context_tag": "Lab"
                }
            ]
        )

        wine_job = add_job(wine_template, "Demo Blackberry Wine - Primary Batch", now - timedelta(days=9))
        beer_job = add_job(beer_template, "Demo Pale Ale - Basement Fermenter", now - timedelta(hours=30))
        mushroom_job = add_job(mycology_template, "Demo Blue Oyster - Grain Jar A", now - timedelta(days=4, hours=12))

        wine_job.steps[0].completed_at = wine_job.started_at + timedelta(hours=1)
        wine_job.steps[1].completed_at = wine_job.started_at + timedelta(hours=2)

        beer_job.steps[0].completed_at = beer_job.started_at + timedelta(hours=1)

        mushroom_job.steps[0].completed_at = mushroom_job.started_at + timedelta(minutes=30)

        db.session.commit()

        add_note(
            wine_job,
            wine_job.steps[0],
            "Sanitation went smoothly",
            "<p>Everything was cleaned and staged before fruit handling. Felt organized.</p>",
            wine_job.steps[0].completed_at + timedelta(minutes=5)
        )

        add_note(
            wine_job,
            wine_job.steps[2],
            "Primary fermentation smell check",
            "<p>Healthy fruit aroma. Slightly sharp but not unpleasant. This is a ready task for testing.</p>",
            now - timedelta(hours=4)
        )

        add_note(
            beer_job,
            beer_job.steps[1],
            "Krausen forming",
            "<p>Visible foam ring and steady airlock activity. Temperature looked stable.</p>",
            now - timedelta(hours=2)
        )

        add_note(
            mushroom_job,
            mushroom_job.steps[1],
            "Early growth visible",
            "<p>White growth at several inoculation points. No visible contamination yet.</p>",
            now - timedelta(hours=8)
        )

        print("Demo data created.")
        print("")
        print("Open PF67:")
        print("https://pf67.elx.dscloud.me/")
        print("")
        print("Demo includes:")
        print("- Recent movements")
        print("- Ready wine/beer/mycology tasks")
        print("- Upcoming tasks")
        print("- Timeline notes")
        print("- Completed steps")
        print("- Read-only/edit-mode behavior")

if __name__ == "__main__":
    main()
