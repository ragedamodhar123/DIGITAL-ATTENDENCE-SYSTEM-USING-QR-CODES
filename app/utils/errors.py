"""Turn errors into consistent responses.

/api/... routes always get JSON: {"error": "<code>", "message": "...", "details": {...}}
Pages get a simple HTML message for now; Module 1, part 3 adds proper templates.
"""
from flask_wtf.csrf import CSRFError
from markupsafe import escape
from werkzeug.exceptions import HTTPException

from ..extensions import db
from ..services.errors import ServiceError
from .http import json_error, wants_json

SERVER_ERROR_MESSAGE = "Something went wrong. Please try again."


def _http_code(exc):
    return (exc.name or "error").lower().replace(" ", "_")


def _simple_page(status, message):
    html = f"<!doctype html><title>{status}</title><h1>{status}</h1><p>{escape(message)}</p>"
    return html, status


def register_error_handlers(app):
    @app.errorhandler(ServiceError)
    def handle_service_error(exc):
        if wants_json():
            extra = {"details": exc.details} if exc.details else {}
            return json_error(exc.status_code, exc.code, exc.message, **extra)
        return _simple_page(exc.status_code, exc.message)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc):
        message = "Your form expired or was sent from another site. Reload the page and try again."
        if wants_json():
            return json_error(400, "csrf_failed", message)
        return _simple_page(400, message)

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        if wants_json():
            return json_error(exc.code or 500, _http_code(exc), exc.description or exc.name)
        return exc  # Werkzeug's default page until templates exist

    @app.errorhandler(500)
    def handle_server_error(exc):
        # The failed request may have left the session mid-transaction.
        db.session.rollback()
        original = getattr(exc, "original_exception", None) or exc
        app.logger.error("Unhandled error", exc_info=original)
        if wants_json():
            return json_error(500, "server_error", SERVER_ERROR_MESSAGE)
        return _simple_page(500, SERVER_ERROR_MESSAGE)