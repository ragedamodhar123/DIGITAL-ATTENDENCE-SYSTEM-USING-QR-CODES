"""Admin JSON API, used from Postman. Every route needs 'Authorization: Bearer <token>'.

Excluded from CSRF checks on purpose: CSRF attacks ride on cookies a browser
sends automatically, and these routes ignore cookies entirely; they only accept
the token header, which another website cannot make a browser send.
"""
from flask import Blueprint

bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")

from . import academic, users  # noqa: E402,F401  (registers the routes on bp)