from flask import Blueprint

bp = Blueprint("face", __name__, url_prefix="/face")              # pages
api_bp = Blueprint("face_api", __name__, url_prefix="/api/face")  # JSON for the camera page

from . import api, routes  # noqa: E402,F401