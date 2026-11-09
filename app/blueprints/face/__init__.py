from flask import Blueprint

bp = Blueprint("face", __name__, url_prefix="/face")

from . import routes  # noqa: E402,F401