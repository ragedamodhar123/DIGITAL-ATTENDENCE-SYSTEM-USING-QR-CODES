"""Turn errors into consistent responses.

/api/... routes always get JSON: {"error": "<code>", "message": "...", "details": {...}}
Pages get the errors/error.html template with a plain-language message.
"""
from flask import jsonify, render_template
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from ..extensions import db
from ..services.errors import ServiceError
from .http import json_error, wants_json

SERVER_ERROR_MESSAGE = "Something went wrong. Please try again."

# Plain-language titles and messages for pages. Technical descriptions stay out of sight.
PAGE_TEXT = {
    400: ("Request not understood", "The page could not handle that request. Go back and try again."),
    401: ("Log in required", "Please log in to continue."),
    403: ("Not allowed", "Your account does not have access to this page."),
    404: ("Page not found", "Check the address, or go back to your home page."),
    405: ("Not allowed", "That action is not available on this page."),
    413: ("File too large", "The upload is too large. Try a smaller photo."),
    429: ("Too many attempts", "Wait a few minutes and try again."),
    500: ("Something went wrong", SERVER_ERROR_MESSAGE),
}


def _http_code(exc):
    return (exc.name or "error").lower().replace(" ", "_")


def render_error_page(status, message=None):
    title, default_message = PAGE_TEXT.get(status, ("Something went wrong", SERVER_ERROR_MESSAGE))
    return render_template("errors/error.html", title=title, message=message or default_message), status


def register_error_handlers(app):
    @app.errorhandler(ServiceError)
    def handle_service_error(exc):
        if wants_json():
            # to_dict() lets an error add its own fields (e.g. a face problem's "reason").
            return jsonify(exc.to_dict()), exc.status_code
        return render_error_page(exc.status_code, exc.message)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc):
        message = "This form expired or came from another site. Reload the page and try again."
        if wants_json():
            return json_error(400, "csrf_failed", message)
        return render_error_page(400, message)

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        status = exc.code or 500
        if wants_json():
            return json_error(status, _http_code(exc), exc.description or exc.name)
        if status == 429:
            # Our rate limits carry a readable message; show it.
            return render_error_page(429, exc.description)
        return render_error_page(status)

    @app.errorhandler(500)
    def handle_server_error(exc):
        # The failed request may have left the session mid-transaction.
        db.session.rollback()
        original = getattr(exc, "original_exception", None) or exc
        app.logger.error("Unhandled error", exc_info=original)
        if wants_json():
            return json_error(500, "server_error", SERVER_ERROR_MESSAGE)
        return render_error_page(500)