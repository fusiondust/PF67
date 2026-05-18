import os
from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent

    DB_PATH = os.environ.get(
        "PF67_DB_PATH",
        str(BASE_DIR / "db" / "pf67.sqlite3")
    )

    UPLOAD_DIR = Path(os.environ.get(
        "PF67_UPLOAD_DIR",
        str(BASE_DIR / "static" / "uploads")
    ))

    APP_REVISION = "v1.0.0-dev"
    EDIT_PASSWORD = os.environ.get("PF67_EDIT_PASSWORD", "pf67")

    SECRET_KEY = os.environ.get("PF67_SECRET_KEY", "pf67-dev-key-change-later")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB_PATH
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)