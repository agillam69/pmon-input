"""Web-based configuration and monitoring dashboard.

Runs a Flask app on a configurable port, providing:
- Setup wizard for first-run configuration
- Live dashboard with queue stats and recent messages
- Configuration editor
- Message queue browser
- Action buttons (check, test-delivery, dry-run toggle)
"""

from __future__ import annotations

import functools
import io
import logging
import os
import threading
import time
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import __version__
from .config import Config, config_to_env_dict, load_config, save_config, validate_config
from .store import MessageStore

logger = logging.getLogger(__name__)

# Thread-safe shared state between the bridge loop and the web UI
_state_lock = threading.Lock()
_shared_state: dict[str, Any] = {
    "service_state": "stopped",  # running, dry_run, setup_required, stopped
    "started_at": None,
    "last_fetch_time": None,
    "last_fetch_status": None,
    "last_fetch_duration_ms": None,
    "last_fetch_messages_found": None,
    "counts": {"pending": 0, "delivered": 0, "dead_letter": 0},
}


def update_shared_state(**kwargs: Any) -> None:
    """Update the shared state dict (thread-safe)."""
    with _state_lock:
        _shared_state.update(kwargs)


def get_shared_state() -> dict[str, Any]:
    """Get a snapshot of the shared state (thread-safe)."""
    with _state_lock:
        return dict(_shared_state)


def _env_file_path() -> str:
    """Return the path to the .env file (in the working directory)."""
    return os.path.join(os.getcwd(), ".env")


def _needs_setup() -> bool:
    """Check whether the .env file exists."""
    return not Path(_env_file_path()).is_file()


def _format_timestamp(ts: float | None) -> str:
    """Format a Unix timestamp to a readable string."""
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _uptime_string(started_at: float | None) -> str | None:
    """Return a human-readable uptime string."""
    if started_at is None:
        return None
    elapsed = time.time() - started_at
    if elapsed < 60:
        return f"{int(elapsed)}s"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    hours = int(elapsed // 3600)
    mins = int((elapsed % 3600) // 60)
    return f"{hours}h {mins}m"


def create_app(cfg: Config, store: MessageStore | None = None) -> Flask:
    """Create and configure the Flask application."""
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.secret_key = cfg.webui_password or os.urandom(24).hex()
    app.config["cfg"] = cfg
    app.config["store"] = store

    # Jinja2 custom filters
    @app.template_filter("format_ts")
    def _filter_format_ts(ts: float | None) -> str:
        return _format_timestamp(ts)

    @app.template_filter("short_hash")
    def _filter_short_hash(h: str | None) -> str:
        if not h:
            return "—"
        return h[:12]

    @app.template_filter("truncate_text")
    def _filter_truncate(text: str | None, length: int = 80) -> str:
        if not text:
            return "—"
        if len(text) <= length:
            return text
        return text[:length] + "…"

    # ---------- Auth middleware ----------

    def _auth_required() -> bool:
        """Determine if authentication is required for this request."""
        password = app.config["cfg"].webui_password
        if not password:
            return False
        # No auth for localhost when no password set
        remote = request.remote_addr
        if remote in ("127.0.0.1", "::1") and not password:
            return False
        return True

    def login_required(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if _auth_required() and not session.get("authenticated"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ---------- Context processors ----------

    @app.context_processor
    def inject_globals():
        state = get_shared_state()
        return {
            "version": __version__,
            "service_state": state.get("service_state", "stopped"),
            "auth_required": _auth_required(),
            "needs_setup": _needs_setup(),
        }

    # ---------- Routes ----------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == app.config["cfg"].webui_password:
                session["authenticated"] = True
                flash("Logged in successfully.", "success")
                return redirect(url_for("dashboard"))
            flash("Invalid password.", "danger")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("Logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        if _needs_setup():
            return redirect(url_for("setup"))

        state = get_shared_state()
        store_inst: MessageStore | None = app.config.get("store")
        recent = []
        counts = state.get("counts", {"pending": 0, "delivered": 0, "dead_letter": 0})

        if store_inst:
            try:
                recent = store_inst.get_recent(limit=10)
                counts = store_inst.get_counts()
            except Exception as e:
                logger.warning("Failed to query store for dashboard: %s", e)

        return render_template(
            "dashboard.html",
            counts=counts,
            recent_messages=recent,
            last_fetch={
                "time": _format_timestamp(state.get("last_fetch_time")),
                "status": state.get("last_fetch_status", "—"),
                "duration_ms": state.get("last_fetch_duration_ms"),
                "messages_found": state.get("last_fetch_messages_found"),
            },
            uptime=_uptime_string(state.get("started_at")),
        )

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if request.method == "POST":
            env_dict: dict[str, str] = {}
            for key in request.form:
                val = request.form[key].strip()
                if val:
                    env_dict[key] = val

            # Ensure required fields
            if not env_dict.get("PAGERMON_API_KEY"):
                flash("PagerMon API key is required.", "danger")
                return render_template("setup.html")

            try:
                save_config(env_dict, _env_file_path())
                flash("Configuration saved! The bridge will start polling.", "success")
                # Reload config for the running app
                new_cfg = load_config(_env_file_path())
                app.config["cfg"] = new_cfg
                update_shared_state(service_state="running" if not new_cfg.dry_run else "dry_run")
                return redirect(url_for("dashboard"))
            except Exception as e:
                flash(f"Failed to save configuration: {e}", "danger")
                return render_template("setup.html")

        return render_template("setup.html")

    @app.route("/config", methods=["GET", "POST"])
    @login_required
    def config_page():
        if _needs_setup():
            return redirect(url_for("setup"))

        if request.method == "POST":
            env_dict: dict[str, str] = {}
            for key in request.form:
                val = request.form[key].strip()
                if val:
                    env_dict[key] = val

            # Handle checkboxes (unchecked = not in form data)
            for checkbox_key in ("DRY_RUN", "WEBUI_ENABLED"):
                if checkbox_key not in request.form:
                    env_dict[checkbox_key] = "false"

            try:
                save_config(env_dict, _env_file_path())
                flash("Configuration saved. Most changes require a service restart.", "success")
                new_cfg = load_config(_env_file_path())
                app.config["cfg"] = new_cfg
            except Exception as e:
                flash(f"Failed to save configuration: {e}", "danger")

            return redirect(url_for("config_page"))

        current = config_to_env_dict(app.config["cfg"])
        return render_template("config.html", config=current)

    @app.route("/messages")
    @login_required
    def messages_page():
        if _needs_setup():
            return redirect(url_for("setup"))

        state_filter = request.args.get("state", "all")
        page = max(1, int(request.args.get("page", 1)))
        per_page = 25

        store_inst: MessageStore | None = app.config.get("store")
        msgs: list[dict] = []
        has_next = False

        if store_inst:
            try:
                filter_val = None if state_filter == "all" else state_filter
                # Fetch one extra to check if there's a next page
                msgs = store_inst.get_messages(
                    state=filter_val,
                    limit=per_page + 1,
                    offset=(page - 1) * per_page,
                )
                if len(msgs) > per_page:
                    has_next = True
                    msgs = msgs[:per_page]
            except Exception as e:
                logger.warning("Failed to query messages: %s", e)
                flash(f"Database query error: {e}", "danger")

        return render_template(
            "messages.html",
            messages=msgs,
            current_filter=state_filter,
            page=page,
            has_next=has_next,
        )

    @app.route("/messages/<message_hash>")
    @login_required
    def message_detail(message_hash: str):
        store_inst: MessageStore | None = app.config.get("store")
        if not store_inst:
            flash("Database not available.", "danger")
            return redirect(url_for("messages_page"))

        try:
            msg = store_inst.get_message_by_hash(message_hash)
        except Exception as e:
            flash(f"Database query error: {e}", "danger")
            return redirect(url_for("messages_page"))

        if msg is None:
            flash("Message not found.", "warning")
            return redirect(url_for("messages_page"))

        return render_template("message_detail.html", message=msg)

    # ---------- API routes ----------

    @app.route("/api/status")
    @login_required
    def api_status():
        state = get_shared_state()
        store_inst: MessageStore | None = app.config.get("store")
        counts = state.get("counts", {"pending": 0, "delivered": 0, "dead_letter": 0})
        if store_inst:
            try:
                counts = store_inst.get_counts()
            except Exception:
                pass

        return jsonify({
            "service_state": state.get("service_state", "stopped"),
            "uptime": _uptime_string(state.get("started_at")),
            "counts": counts,
            "last_fetch": {
                "time": _format_timestamp(state.get("last_fetch_time")),
                "status": state.get("last_fetch_status"),
                "duration_ms": state.get("last_fetch_duration_ms"),
                "messages_found": state.get("last_fetch_messages_found"),
            },
        })

    @app.route("/api/check", methods=["POST"])
    @login_required
    def api_check():
        from .fetcher import Fetcher
        from .pagermon import PagerMonClient
        from .parser import parse_messages

        current_cfg = app.config["cfg"]
        results: list[dict[str, str]] = []

        # 1. Validate config
        errors = validate_config(current_cfg)
        if errors:
            for e in errors:
                results.append({"status": "fail", "check": "config", "message": e})
            return jsonify({"ok": False, "results": results})
        results.append({"status": "ok", "check": "config", "message": "Configuration valid"})

        # 2. Check DB
        store_inst: MessageStore | None = app.config.get("store")
        if store_inst:
            try:
                counts = store_inst.get_counts()
                results.append({
                    "status": "ok",
                    "check": "database",
                    "message": f"Database OK. Counts: {counts}",
                })
            except Exception as e:
                results.append({"status": "fail", "check": "database", "message": str(e)})
                return jsonify({"ok": False, "results": results})
        else:
            results.append({"status": "warn", "check": "database", "message": "Store not initialized"})

        # 3. Fetch source
        fetcher = Fetcher(current_cfg)
        fetch_res = fetcher.fetch()
        fetcher.close()
        if fetch_res.success:
            results.append({
                "status": "ok",
                "check": "source_fetch",
                "message": f"Fetched OK in {fetch_res.duration_ms:.0f}ms",
            })
            msgs = parse_messages(fetch_res.html or "", max_message_length=current_cfg.max_message_length)
            results.append({
                "status": "ok",
                "check": "parser",
                "message": f"Parsed {len(msgs)} valid message(s)",
            })
        else:
            results.append({
                "status": "fail",
                "check": "source_fetch",
                "message": f"Fetch failed: {fetch_res.error}",
            })

        # 4. Check PagerMon
        client = PagerMonClient(current_cfg)
        reachable, msg = client.check_reachable()
        client.close()
        results.append({
            "status": "ok" if reachable else "warn",
            "check": "pagermon",
            "message": msg,
        })

        all_ok = all(r["status"] != "fail" for r in results)
        return jsonify({"ok": all_ok, "results": results})

    @app.route("/api/dry-run/toggle", methods=["POST"])
    @login_required
    def api_toggle_dry_run():
        current_cfg = app.config["cfg"]
        new_dry_run = not current_cfg.dry_run
        new_cfg = replace(current_cfg, dry_run=new_dry_run)
        app.config["cfg"] = new_cfg

        # Also update the .env file
        env_dict = config_to_env_dict(new_cfg)
        try:
            save_config(env_dict, _env_file_path())
        except Exception as e:
            logger.warning("Failed to persist dry-run toggle: %s", e)

        update_shared_state(service_state="dry_run" if new_dry_run else "running")
        state_label = "enabled" if new_dry_run else "disabled"
        flash(f"Dry-run mode {state_label}.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/api/test-delivery", methods=["POST"])
    @login_required
    def api_test_delivery():
        from .pagermon import PagerMonClient

        current_cfg = app.config["cfg"]
        test_text = f"TEST - CFA WEB BRIDGE - Web UI test at {time.strftime('%Y-%m-%d %H:%M:%S')}"

        client = PagerMonClient(current_cfg)
        res = client.deliver(test_text)
        client.close()

        if res.success:
            flash(f"Test delivery successful! PagerMon response: {res.pagermon_id}", "success")
        else:
            flash(f"Test delivery failed: [{res.error_category}] {res.error_desc}", "danger")

        return redirect(url_for("dashboard"))

    return app


def start_webui(cfg: Config, store: MessageStore | None = None) -> threading.Thread | None:
    """Start the web UI in a daemon thread. Returns the thread, or None if disabled."""
    if not cfg.webui_enabled:
        logger.info("Web UI is disabled (WEBUI_ENABLED=false)")
        return None

    app = create_app(cfg, store)

    # Suppress Flask's default startup banner in production
    werkzeug_log = logging.getLogger("werkzeug")
    werkzeug_log.setLevel(logging.WARNING)

    def _run():
        logger.info(
            "Web UI starting on http://%s:%d",
            cfg.webui_host,
            cfg.webui_port,
        )
        app.run(
            host=cfg.webui_host,
            port=cfg.webui_port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    thread = threading.Thread(target=_run, name="webui", daemon=True)
    thread.start()
    return thread
