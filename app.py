import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import psutil
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash


DEFAULT_POLICY = {"services": {}, "limits": {}, "tasks": []}
SERVICE_ALLOWLIST = {"cron", "unattended-upgrades"}
TASK_NAME = re.compile(r"[a-z0-9-]{1,48}\Z")
TASK_SCRIPT = re.compile(r"[a-z0-9_.-]{1,64}\.sh\Z")
CRON_FIELD = re.compile(r"[0-9*/,-]+\Z")
MEMORY_LIMIT = re.compile(r"(?:[1-9][0-9]{0,5}[KMG]|infinity)\Z")
CPU_QUOTA = re.compile(r"(?:[1-9][0-9]{0,3})%\Z")


def validate_policy(policy, task_dir):
    if not isinstance(policy, dict) or set(policy) != {"services", "limits", "tasks"}:
        raise ValueError("Policy must contain only services, limits, and tasks.")

    services = policy["services"]
    limits = policy["limits"]
    tasks = policy["tasks"]
    if not isinstance(services, dict) or not isinstance(limits, dict) or not isinstance(tasks, list):
        raise ValueError("Invalid policy section types.")

    for service, state in services.items():
        if service not in SERVICE_ALLOWLIST or not isinstance(state, str) or state not in {"running", "stopped"}:
            raise ValueError(f"Unsupported service setting: {service}.")

    for service, values in limits.items():
        if service not in SERVICE_ALLOWLIST or not isinstance(values, dict):
            raise ValueError(f"Unsupported service limit: {service}.")
        if set(values) != {"MemoryMax", "CPUQuota"}:
            raise ValueError(f"Invalid limit fields for {service}.")
        if not isinstance(values["MemoryMax"], str) or not MEMORY_LIMIT.fullmatch(values["MemoryMax"]):
            raise ValueError(f"Invalid MemoryMax for {service}.")
        if not isinstance(values["CPUQuota"], str) or not CPU_QUOTA.fullmatch(values["CPUQuota"]):
            raise ValueError(f"Invalid CPUQuota for {service}.")

    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"name", "cron", "script"}:
            raise ValueError("Each task must contain only name, cron, and script.")
        name, schedule, script = task["name"], task["cron"], task["script"]
        if not isinstance(name, str) or not TASK_NAME.fullmatch(name):
            raise ValueError("Task names may contain lowercase letters, digits, and hyphens.")
        if not isinstance(schedule, str) or len(schedule) > 100:
            raise ValueError(f"Invalid schedule for task {name}.")
        fields = schedule.split()
        if len(fields) != 5 or any(not CRON_FIELD.fullmatch(field) for field in fields):
            raise ValueError(f"Invalid five-field cron schedule for task {name}.")
        if not isinstance(script, str) or not TASK_SCRIPT.fullmatch(script):
            raise ValueError(f"Invalid script name for task {name}.")
        script_path = Path(task_dir) / script
        try:
            script_mode = script_path.lstat().st_mode
        except FileNotFoundError:
            raise ValueError(f"Task script does not exist: {script}.")
        if not stat.S_ISREG(script_mode):
            raise ValueError(f"Task script must be a regular file: {script}.")


def save_policy(path, policy):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".policy-", dir=target.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(policy, file, indent=2)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_policy(path):
    target = Path(path)
    try:
        contents = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_POLICY
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Could not read policy file: {error}") from error
    try:
        policy = json.loads(contents)
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read policy file: {error}") from error
    if (
        not isinstance(policy, dict)
        or set(policy) != {"services", "limits", "tasks"}
        or not isinstance(policy["services"], dict)
        or not isinstance(policy["limits"], dict)
        or not isinstance(policy["tasks"], list)
    ):
        raise ValueError("Policy file has an invalid structure.")
    return policy


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HOMEDASH_COOKIE_SECURE", "1") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
        CONFIG_PATH=os.environ.get("HOMEDASH_CONFIG", "/etc/homedash/config.json"),
        POLICY_PATH=os.environ.get("HOMEDASH_POLICY", "/var/lib/homedash/policy.json"),
        TASK_DIR=os.environ.get("HOMEDASH_TASKS", "/opt/homedash/tasks"),
        APPLY_COMMAND=["sudo", "-n", "/usr/local/sbin/homedash-apply"],
        APPLY_TIMEOUT=75,
    )
    if test_config:
        app.config.update(test_config)

    if "DASHBOARD_CONFIG" in app.config:
        dashboard_config = app.config["DASHBOARD_CONFIG"]
    else:
        config_path = Path(app.config["CONFIG_PATH"])
        if not config_path.is_file():
            raise RuntimeError(f"Dashboard config file not found: {config_path}")
        dashboard_config = json.loads(config_path.read_text(encoding="utf-8"))
    required = {"secret_key", "user", "password_hash", "allowed_networks"}
    if not isinstance(dashboard_config, dict) or set(dashboard_config) != required:
        raise RuntimeError("Dashboard config must contain secret_key, user, password_hash, and allowed_networks.")
    if any(
        not isinstance(dashboard_config[key], str) or not dashboard_config[key]
        for key in ("secret_key", "user", "password_hash")
    ):
        raise RuntimeError("Dashboard secret key, username, and password hash must be non-empty strings.")
    if not isinstance(dashboard_config["allowed_networks"], list) or not dashboard_config["allowed_networks"]:
        raise RuntimeError("Configure at least one allowed client network.")
    try:
        allowed_networks = [
            ipaddress.ip_network(network, strict=False)
            for network in dashboard_config["allowed_networks"]
        ]
    except (TypeError, ValueError) as error:
        raise RuntimeError("Dashboard config contains an invalid allowed network.") from error
    app.secret_key = dashboard_config["secret_key"]
    app.extensions["dashboard_config"] = dashboard_config
    app.extensions["allowed_networks"] = allowed_networks
    app.extensions["login_failures"] = {}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    @app.before_request
    def restrict_network_and_auth():
        try:
            address = ipaddress.ip_address(request.remote_addr)
        except (TypeError, ValueError):
            abort(400, "Invalid client address.")
        if not any(address in network for network in allowed_networks):
            abort(403)

        if request.endpoint not in {"login", "static"} and not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify(error="Login required."), 401
            return redirect(url_for("login"))

        if request.method == "POST":
            supplied = request.headers.get("X-CSRF-Token")
            if request.is_json:
                token = supplied
            else:
                token = request.form.get("csrf_token")
            if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
                abort(400, "Invalid or missing CSRF token.")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        failures = app.extensions["login_failures"]
        address = request.remote_addr
        count, started = failures.get(address, (0, time.monotonic()))
        if time.monotonic() - started >= 300:
            count, started = 0, time.monotonic()

        message = ""
        if request.method == "POST":
            config = app.extensions["dashboard_config"]
            if count >= 5:
                message = "Too many failed attempts. Wait five minutes."
            elif (
                request.form.get("user") == config["user"]
                and check_password_hash(config["password_hash"], request.form.get("password", ""))
            ):
                failures.pop(address, None)
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("home"))
            else:
                failures[address] = (count + 1, started)
                message = "Incorrect username or password."

        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return render_template("login.html", message=message, csrf_token=session["csrf_token"])

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def home():
        try:
            policy = load_policy(app.config["POLICY_PATH"])
        except ValueError as error:
            return str(error), 500
        return render_template("dashboard.html", policy=policy, csrf_token=session["csrf_token"])

    @app.get("/api/status")
    def status():
        service_states = {}
        if os.name == "nt":
            service_states = {service: "Linux service status unavailable on Windows" for service in sorted(SERVICE_ALLOWLIST)}
        else:
            for service in sorted(SERVICE_ALLOWLIST):
                try:
                    result = subprocess.run(
                        ["systemctl", "is-active", service + ".service"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=3,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    return jsonify(error=f"Could not query service status for {service}: {error}"), 503
                service_states[service] = result.stdout.strip() or result.stderr.strip() or "unknown"
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(Path.cwd().anchor or "/")
        return jsonify(
            uptime_hours=round((time.time() - psutil.boot_time()) / 3600, 1),
            cpu_percent=psutil.cpu_percent(interval=0.2),
            memory_percent=memory.percent,
            disk_percent=disk.percent,
            services=service_states,
        )

    @app.get("/api/policy")
    def get_policy():
        try:
            return jsonify(load_policy(app.config["POLICY_PATH"]))
        except ValueError as error:
            return jsonify(error=str(error)), 503

    @app.post("/api/policy")
    def update_policy():
        if not request.is_json:
            return jsonify(error="Expected a JSON request body."), 415
        try:
            policy = request.get_json()
        except BadRequest:
            return jsonify(error="Request body contains invalid JSON."), 400
        try:
            validate_policy(policy, app.config["TASK_DIR"])
            save_policy(app.config["POLICY_PATH"], policy)
        except (ValueError, OSError) as error:
            return jsonify(error=str(error)), 400
        return jsonify(ok=True, policy=policy)

    @app.post("/api/apply")
    def apply_policy():
        try:
            result = subprocess.run(
                app.config["APPLY_COMMAND"],
                capture_output=True,
                text=True,
                check=False,
                timeout=app.config["APPLY_TIMEOUT"],
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return jsonify(ok=False, error=f"Could not run policy helper: {error}"), 503
        output = (result.stdout if result.returncode == 0 else result.stderr or result.stdout).strip()[-1000:]
        if result.returncode:
            return jsonify(ok=False, error=output or "Policy helper failed."), 503
        return jsonify(ok=True, output=output)

    return app
