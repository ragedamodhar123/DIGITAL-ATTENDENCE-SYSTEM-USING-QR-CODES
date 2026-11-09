"""Small request/response helpers shared by decorators, error handlers and routes."""
from urllib.parse import urlsplit

from flask import jsonify, request

# Every JSON endpoint lives under /api/. Everything else is an HTML page.
# One simple rule decides whether an error is sent as JSON or as a page.
API_PREFIX = "/api/"
MAX_NEXT_LENGTH = 512


def wants_json():
    return request.path.startswith(API_PREFIX)


def json_error(status, code, message, **extra):
    body = {"error": code, "message": message}
    body.update(extra)
    return jsonify(body), status


def is_safe_next(target):
    """True only for a same-site relative path like '/student/scan?x=1'.

    Blocks open redirects: 'https://evil.com', '//evil.com', '/\\evil.com',
    'javascript:...', and anything with control characters.
    """
    if not isinstance(target, str) or not target or len(target) > MAX_NEXT_LENGTH:
        return False
    if "\\" in target or any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return False
    if not target.startswith("/") or target.startswith("//"):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def current_path_for_next():
    """The current GET URL as a 'next' value, or None if it should not be remembered."""
    if request.method != "GET":
        return None  # never replay a form submission after login
    path = request.full_path
    if path.endswith("?"):
        path = path[:-1]
    return path if is_safe_next(path) else None