from flask import Blueprint

bp = Blueprint("auth", __name__)

from . import routes  # noqa: E402,F401  (registers the routes on bp)