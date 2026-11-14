from flask import Blueprint

bp = Blueprint("faculty", __name__, url_prefix="/faculty")              # pages
api_bp = Blueprint("faculty_api", __name__, url_prefix="/api/faculty")  # JSON for the live screen

from . import api, records, routes, sessions  # noqa: E402,F401