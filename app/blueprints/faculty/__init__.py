from flask import Blueprint

bp = Blueprint("faculty", __name__, url_prefix="/faculty")

from . import routes  # noqa: E402,F401