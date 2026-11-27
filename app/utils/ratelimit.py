"""Keys that decide WHO a rate limit counts against.

A whole classroom usually reaches the server through ONE college IP address,
so limiting logins by IP alone would block 60 students at once. Instead:

- login_user_key: IP + username. Slows someone guessing one account's password,
  without affecting anyone else.
- login_ip_key:   IP only, with a high limit. Stops one machine hammering many
  usernames, while a full classroom on one IP stays well under it.
- current_user_key: per logged-in user, for scan and face attempts (Module 4).
"""
from flask import request
from flask_login import current_user

from .validators import normalize_username

MAX_KEY_USERNAME = 64


def client_ip():
    return request.remote_addr or "unknown"


def _submitted_username():
    if request.is_json:
        data = request.get_json(silent=True)
        raw = data.get("username") if isinstance(data, dict) else None
    else:
        raw = request.form.get("username")
    username = normalize_username(raw) if isinstance(raw, str) else ""
    return username[:MAX_KEY_USERNAME]


def login_user_key():
    return f"login:{client_ip()}:{_submitted_username()}"


def login_ip_key():
    return f"login-ip:{client_ip()}"


def current_user_key():
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return f"anon:{client_ip()}"