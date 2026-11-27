from flask import Blueprint

bp = Blueprint("attend", __name__)                                         # /a/<code>: the link inside the QR code
api_bp = Blueprint("attend_api", __name__, url_prefix="/api/attendance")  # face check JSON

from . import api, routes  # noqa: E402,F401