from flask import Flask
from .models import db
from .views import views_bp
from .api import api_bp
from .calendar_feed import calendar_bp
from .config import Config


def create_app():
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static"
    )

    app.config.from_object(Config)

    db.init_app(app)

    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(calendar_bp, url_prefix="/calendar")

    with app.app_context():
        db.create_all()

    return app
