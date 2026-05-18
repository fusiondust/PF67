# PF67 Rev 1 Skeleton

PF67 - ProtocolFlow67

Protocol-based calendar automation for craft production.

## Run

python build_pf67_rev1.py

cd pf67_rev1

pip install -r requirements.txt

python run.py

Then open:

http://127.0.0.1:5000

## Notes

Rev 1 is intentionally skeletal.

Included:
- Flask app
- SQLite database in db/pf67.sqlite3
- SQLAlchemy models
- Template creation
- Step creation
- Job creation from template
- Basic dashboard
- Read-only JSON API
- Basic ICS calendar feed
- AI suggestions enabled by default at job level
- Folders created automatically

Not yet included:
- Full WYSIWYG editor
- Image upload handling
- Nested subtemplate expansion
- Authentication
- AI write access
