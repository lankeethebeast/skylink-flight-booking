"""View decorators: API-error handling and traceback logging."""
from __future__ import annotations

import logging
import traceback
from functools import wraps

from flask import flash, redirect, render_template, request, url_for

from skylink_client import SkyLinkAPIError

logger = logging.getLogger("skylink")


def handle_api_errors(view):
    """Wrap a view to convert ``SkyLinkAPIError`` into user-friendly redirects,
    and any other unhandled exception into a logged error page that includes
    the actual traceback when in debug/development mode.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except SkyLinkAPIError as exc:
            code = exc.status_code
            payload = exc.error_payload or {}
            logger.warning(
                "SkyLinkAPIError in %s: %s (payload=%s)",
                view.__name__,
                exc.message,
                payload,
            )
            if code == 403 and payload.get("blocked") is True:
                flash(payload.get("message", "This booking is currently blocked."), "warning")
                return redirect(url_for("index"))
            if code == 502:
                flash("Supplier temporarily unavailable. Please search again.", "error")
                return redirect(url_for("index"))
            if code == 503:
                flash(
                    exc.message or "Unable to reach SkyLink API. Please try again shortly.",
                    "error",
                )
                return redirect(url_for("index"))
            if code == 504:
                flash(
                    exc.message or "Flight supplier search timed out. Please try again.",
                    "warning",
                )
                return redirect(url_for("index"))
            if code == 400:
                flash(payload.get("message", "Validation error, please review your input."), "error")
                return redirect(request.referrer or url_for("index"))
            flash(f"Request failed ({code}): {exc.message}", "error")
            return redirect(url_for("index"))
        except Exception as exc:  # noqa: BLE001 — intentional catch-all
            tb = traceback.format_exc()
            logger.error("Unhandled exception in %s: %s\n%s", view.__name__, exc, tb)
            return render_template(
                "error.html",
                show_debug=_is_debug(),
                error_type=type(exc).__name__,
                error_message=str(exc),
                error_traceback=tb,
                request_path=request.path,
                request_method=request.method,
            ), 500

    return wrapper


def _is_debug() -> bool:
    """Best-effort lookup of the FLASK_DEBUG flag without importing app.py."""
    import os
    return os.getenv("FLASK_DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}
