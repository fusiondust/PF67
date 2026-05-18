from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, session, abort, render_template_string

import os


APP_TITLE = "PF67 Dev Editor"
HOST = "0.0.0.0"
PORT = 5080

PROJECT_ROOT = Path(__file__).resolve().parent
BACKUP_ROOT = PROJECT_ROOT / "backups" / "dev_editor"

ALLOWED_EXTENSIONS = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml"
}

BLOCKED_PARTS = {
    ".git",
    "__pycache__",
    "db",
    "backups",
    "venv",
    ".venv",
    "env",
    ".env"
}

app = Flask(__name__)
app.secret_key = os.environ.get("PF67_EDITOR_SECRET", "pf67-dev-editor-change-me")


def get_password():
    password = os.environ.get("PF67_EDITOR_PASSWORD", "")

    if not password:
        password = "pf67"

    return password


def is_logged_in():
    return session.get("pf67_editor_logged_in") is True


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


def html_escape(value):
    value = value.replace("&", "&amp;")
    value = value.replace("<", "&lt;")
    value = value.replace(">", "&gt;")
    value = value.replace('"', "&quot;")

    return value


def safe_relative_path(path_text):
    if path_text is None:
        abort(400)

    path_text = path_text.strip().replace("\\", "/")

    if not path_text:
        abort(400)

    if path_text.startswith("/"):
        abort(400)

    parts = path_text.split("/")

    for part in parts:
        if part == "..":
            abort(400)

        if part in BLOCKED_PARTS:
            abort(403)

    full_path = (PROJECT_ROOT / path_text).resolve()

    try:
        full_path.relative_to(PROJECT_ROOT)
    except ValueError:
        abort(403)

    return full_path


def is_allowed_file(path):
    if not path.is_file():
        return False

    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False

    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return False

    for part in relative.parts:
        if part in BLOCKED_PARTS:
            return False

    return True


def list_editable_files():
    files = []

    for path in PROJECT_ROOT.rglob("*"):
        if is_allowed_file(path):
            files.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    files.sort()

    return files


def create_backup(path, old_content):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    relative = path.relative_to(PROJECT_ROOT)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_path = BACKUP_ROOT / timestamp / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(old_content, encoding="utf-8")

    return backup_path


BASE_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{{ title }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <link
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
        rel="stylesheet">

    <style>
        body {
            padding-top: 20px;
            padding-bottom: 40px;
        }

        textarea.codebox {
            width: 100%;
            min-height: 70vh;
            font-family: Consolas, Monaco, monospace;
            font-size: 14px;
            white-space: pre;
            tab-size: 4;
        }

        .file-list {
            max-height: 70vh;
            overflow: auto;
        }

        code {
            user-select: all;
        }

        .pf67-muted {
            color: #666;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
            <h1 class="h3 mb-0">{{ title }}</h1>
            <div class="text-muted">Root: <code>{{ project_root }}</code></div>
        </div>

        {% if logged_in %}
            <div>
                <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('index') }}">Files</a>
                <a class="btn btn-outline-danger btn-sm" href="{{ url_for('logout') }}">Logout</a>
            </div>
        {% endif %}
    </div>

    {% if message %}
        <div class="alert alert-info">{{ message }}</div>
    {% endif %}

    {{ body | safe }}
</div>
</body>
</html>
"""


def page(body, message=""):
    return render_template_string(
        BASE_HTML,
        title=APP_TITLE,
        project_root=str(PROJECT_ROOT),
        logged_in=is_logged_in(),
        message=message,
        body=body
    )


@app.route("/", methods=["GET"])
@login_required
def index():
    files = list_editable_files()

    rows = []

    for file_path in files:
        rows.append(
            '<a class="list-group-item list-group-item-action" href="'
            + url_for("edit_file", path=file_path)
            + '">'
            + file_path
            + "</a>"
        )

    body = """
    <div class="row">
        <div class="col-md-4">
            <h2 class="h5">Editable files</h2>
            <div class="list-group file-list">
                {rows}
            </div>
        </div>

        <div class="col-md-8">
            <h2 class="h5">Notes</h2>

            <p>
                This editor is intentionally narrow. It only edits text files inside the PF67 project folder.
            </p>

            <p>
                After saving Python files, restart the PF67 app manually.
            </p>

            <hr>

            <p class="mb-1">Blocked folders:</p>
            <code>{blocked}</code>

            <p class="mt-3 mb-1">Allowed extensions:</p>
            <code>{extensions}</code>

            <hr>

            <p class="pf67-muted">
                Each saved file is backed up before overwrite under:
                <br>
                <code>backups/dev_editor/</code>
            </p>
        </div>
    </div>
    """.format(
        rows="\n".join(rows),
        blocked=", ".join(sorted(BLOCKED_PARTS)),
        extensions=", ".join(sorted(ALLOWED_EXTENSIONS))
    )

    return page(body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == get_password():
            session["pf67_editor_logged_in"] = True
            return redirect(url_for("index"))

        body = """
        <div class="alert alert-danger">Invalid password.</div>

        <form method="post" class="card card-body" style="max-width: 420px;">
            <label class="form-label">Password</label>
            <input class="form-control" type="password" name="password" autofocus>
            <button class="btn btn-primary mt-3" type="submit">Login</button>
        </form>
        """

        return page(body)

    body = """
    <form method="post" class="card card-body" style="max-width: 420px;">
        <label class="form-label">Password</label>
        <input class="form-control" type="password" name="password" autofocus>
        <button class="btn btn-primary mt-3" type="submit">Login</button>
    </form>
    """

    return page(body)


@app.route("/logout")
def logout():
    session.clear()

    return redirect(url_for("login"))


@app.route("/edit")
@login_required
def edit_file():
    path_text = request.args.get("path", "")
    path = safe_relative_path(path_text)

    if not is_allowed_file(path):
        abort(403)

    content = path.read_text(encoding="utf-8")

    body = """
    <form method="post" action="{save_url}">
        <div class="mb-2">
            <label class="form-label">Editing</label>
            <input class="form-control" value="{path_text}" readonly>
        </div>

        <textarea class="form-control codebox" name="content" spellcheck="false">{content}</textarea>

        <div class="mt-3">
            <button class="btn btn-success" type="submit">Save File</button>
            <a class="btn btn-secondary" href="{index_url}">Cancel</a>
        </div>
    </form>
    """.format(
        save_url=url_for("save_file", path=path_text),
        index_url=url_for("index"),
        path_text=html_escape(path_text),
        content=html_escape(content)
    )

    return page(body)


@app.route("/save", methods=["POST"])
@login_required
def save_file():
    path_text = request.args.get("path", "")
    path = safe_relative_path(path_text)

    if not is_allowed_file(path):
        abort(403)

    old_content = path.read_text(encoding="utf-8")
    new_content = request.form.get("content", "")

    create_backup(path, old_content)

    path.write_text(new_content, encoding="utf-8")

    return redirect(url_for("edit_file", path=path_text))


@app.route("/health")
def health():
    return {
        "ok": True,
        "app": "PF67 Dev Editor",
        "project_root": str(PROJECT_ROOT)
    }


if __name__ == "__main__":
    print("")
    print(APP_TITLE)
    print("Project root: " + str(PROJECT_ROOT))
    print("Listening on: http://" + HOST + ":" + str(PORT))
    print("")
    print("Password source: PF67_EDITOR_PASSWORD")
    print("If unset, temporary default password is: pf67")
    print("")

    app.run(
        host=HOST,
        port=PORT,
        debug=False
    )