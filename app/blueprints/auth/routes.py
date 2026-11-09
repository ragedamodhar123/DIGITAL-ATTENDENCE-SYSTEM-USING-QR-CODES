"""Login, logout and change password.

Routes stay thin: read the form, call the service, choose the response.
"""
from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from ...extensions import limiter
from ...models import Role
from ...services import auth_service
from ...services.errors import AuthenticationFailed, ValidationFailed
from ...utils.decorators import role_required
from ...utils.http import is_safe_next
from ...utils.ratelimit import current_user_key, login_ip_key, login_user_key
from . import bp

TOO_MANY_LOGINS = "Too many login attempts. Wait a few minutes and try again."
TOO_MANY_CHANGES = "Too many attempts. Wait a few minutes and try again."


def _home_url():
    return url_for("main.home")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["RATELIMIT_LOGIN_PER_USER"],
    key_func=login_user_key,
    methods=["POST"],
    error_message=TOO_MANY_LOGINS,
)
@limiter.limit(
    lambda: current_app.config["RATELIMIT_LOGIN_PER_IP"],
    key_func=login_ip_key,
    methods=["POST"],
    error_message=TOO_MANY_LOGINS,
)
def login():
    if current_user.is_authenticated:
        return redirect(_home_url())

    next_url = request.values.get("next", "")
    if not is_safe_next(next_url):
        next_url = ""

    if request.method == "GET":
        return render_template("auth/login.html", next_url=next_url, username="")

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    remember = request.form.get("remember") == "on"

    try:
        user = auth_service.authenticate(username, password)
    except AuthenticationFailed as exc:
        # 401 + the same message for every failure; the username is kept so the
        # student does not retype it, the password is never sent back.
        return (
            render_template("auth/login.html", next_url=next_url, username=username[:64], error=exc.message),
            401,
        )

    # Start from a clean session so nothing from before login carries over.
    session.clear()
    login_user(user, remember=remember)
    session["remember_me"] = remember

    if user.must_change_password:
        flash("Welcome. Set your own password to continue.", "info")
        return redirect(url_for("auth.change_password"))
    return redirect(next_url or _home_url())


@bp.post("/logout")
@role_required(Role.STUDENT, Role.FACULTY, allow_pending_password=True)
def logout():
    # POST only (with CSRF token): a hidden image or link on another site
    # cannot log someone out.
    # Order matters. session.clear() FIRST, then logout_user(): logout_user()
    # leaves a note in the session telling Flask-Login to delete the
    # "keep me logged in" cookie. Clearing the session afterwards would erase
    # that note, the cookie would survive, and the next request would log the
    # user straight back in.
    session.clear()
    logout_user()
    flash("You have been logged out.", "info")
    response = redirect(url_for("auth.login"))
    # Belt and braces: delete the remember-me cookie explicitly as well.
    response.delete_cookie(
        current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token"),
        path=current_app.config.get("REMEMBER_COOKIE_PATH", "/"),
        domain=current_app.config.get("REMEMBER_COOKIE_DOMAIN"),
    )
    # Tell the browser to throw away its stored copies of this site's pages, so
    # Back cannot show the dashboard on a shared computer. Supported by current
    # Chrome, Edge and Firefox on HTTPS and on localhost.
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/change-password", methods=["GET", "POST"])
@role_required(Role.STUDENT, Role.FACULTY, allow_pending_password=True)
@limiter.limit("10 per 5 minutes", key_func=current_user_key, methods=["POST"], error_message=TOO_MANY_CHANGES)
def change_password():
    first_time = current_user.must_change_password

    if request.method == "GET":
        return render_template("auth/change_password.html", first_time=first_time, errors={})

    user = current_user._get_current_object()
    try:
        auth_service.change_password(
            user,
            request.form.get("current_password", ""),
            request.form.get("new_password", ""),
            request.form.get("confirm_password", ""),
        )
    except ValidationFailed as exc:
        return (
            render_template("auth/change_password.html", first_time=first_time, errors=exc.details),
            422,
        )

    # session_version went up, so this browser's login is now stale too.
    # Log in again with the new version, keeping the earlier "remember me" choice.
    login_user(user, remember=bool(session.get("remember_me")))
    flash("Password changed. Other devices have been signed out.", "success")
    return redirect(_home_url())