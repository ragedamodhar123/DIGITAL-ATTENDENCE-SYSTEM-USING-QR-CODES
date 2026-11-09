from flask import Blueprint

bp = Blueprint("student", __name__, url_prefix="/student")

from . import routes  # noqa: E402,F401