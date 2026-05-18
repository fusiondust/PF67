from .models import db


def ensure_schema_updates():
    """Small startup migration hook for lightweight SQLite-era PF67 changes."""
    with db.engine.begin() as conn:
        job_columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)").fetchall()]
        if "priority" not in job_columns:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN priority VARCHAR(20) DEFAULT 'Normal'")
        conn.exec_driver_sql("UPDATE jobs SET priority = 'Normal' WHERE priority IS NULL OR priority = ''")
