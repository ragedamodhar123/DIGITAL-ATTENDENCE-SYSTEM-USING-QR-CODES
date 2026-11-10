from flask import Blueprint

bp = Blueprint("attend", __name__)  # /a/<code>: the link inside the QR code

from . import routes  # noqa: E402,F401