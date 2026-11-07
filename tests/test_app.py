import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_dir = self.root / "tasks"
        self.task_dir.mkdir()
        (self.task_dir / "backup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self.policy_path = self.root / "policy.json"
        self.app = create_app(
            {
                "TESTING": True,
                "SESSION_COOKIE_SECURE": False,
                "DASHBOARD_CONFIG": {
                    "secret_key": "test-key-not-for-production",
                    "user": "admin",
                    "password_hash": generate_password_hash("correct-horse-battery"),
                    "allowed_networks": ["127.0.0.1/32"],
                },
                "POLICY_PATH": str(self.policy_path),
                "TASK_DIR": str(self.task_dir),
                "APPLY_COMMAND": ["missing-homedash-helper"],
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def login(self):
        response = self.client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        return self.client.post(
            "/login",
            data={"user": "admin", "password": "correct-horse-battery", "csrf_token": token},
            follow_redirects=True,
        )

    def session_csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def test_login_and_dashboard_render(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Live status", response.data)
        self.assertIn(b"Policy", response.data)
        with self.client.session_transaction() as session:
            self.assertTrue(session.permanent)

    def test_private_api_requires_authentication(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 401)

    def test_private_api_post_requires_authentication_before_csrf(self):
        response = self.client.post("/api/policy", json={"services": {}, "limits": {}, "tasks": []})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Login required."})

    def test_dashboard_sets_security_headers(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_client_network_allowlist_is_enforced(self):
        response = self.client.get("/login", environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        self.assertEqual(response.status_code, 403)

    def test_policy_save_validates_and_persists(self):
        self.login()
        policy = {
            "services": {"cron": "running"},
            "limits": {"cron": {"MemoryMax": "100M", "CPUQuota": "20%"}},
            "tasks": [{"name": "nightly-backup", "cron": "0 3 * * *", "script": "backup.sh"}],
        }
        response = self.client.post(
            "/api/policy",
            json=policy,
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(self.policy_path.read_text(encoding="utf-8")), policy)
        if os.name == "posix":
            self.assertEqual(self.policy_path.stat().st_mode & 0o777, 0o600)

    def test_policy_api_returns_empty_default(self):
        self.login()
        response = self.client.get("/api/policy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"services": {}, "limits": {}, "tasks": []})

    @patch("app.psutil.disk_usage")
    @patch("app.psutil.virtual_memory")
    @patch("app.psutil.cpu_percent", return_value=3.0)
    @patch("app.psutil.boot_time", return_value=1)
    @patch("app.subprocess.run")
    def test_status_reports_supported_services(
        self, systemctl, boot_time, cpu_percent, virtual_memory, disk_usage
    ):
        self.login()
        systemctl.return_value.stdout = "active\n"
        systemctl.return_value.stderr = ""
        virtual_memory.return_value.percent = 40
        disk_usage.return_value.percent = 50
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        if os.name == "nt":
            self.assertEqual(
                response.json["services"],
                {
                    "cron": "Linux service status unavailable on Windows",
                    "unattended-upgrades": "Linux service status unavailable on Windows",
                },
            )
            systemctl.assert_not_called()
        else:
            self.assertEqual(
                response.json["services"],
                {"cron": "active", "unattended-upgrades": "active"},
            )
            self.assertEqual(systemctl.call_count, 2)

    def test_policy_post_rejects_non_json_body(self):
        self.login()
        response = self.client.post(
            "/api/policy",
            data={"services": "{}", "csrf_token": self.session_csrf()},
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 415)

    def test_policy_post_rejects_malformed_json(self):
        self.login()
        response = self.client.post(
            "/api/policy",
            data="{",
            content_type="application/json",
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"invalid JSON", response.data)

    def test_policy_rejects_unknown_service_and_keeps_existing_file(self):
        original = {"services": {}, "limits": {}, "tasks": []}
        self.policy_path.write_text(json.dumps(original), encoding="utf-8")
        self.login()
        response = self.client.post(
            "/api/policy",
            json={"services": {"ssh": "stopped"}, "limits": {}, "tasks": []},
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(self.policy_path.read_text(encoding="utf-8")), original)

    def test_policy_post_requires_csrf_token(self):
        self.login()
        response = self.client.post("/api/policy", json={"services": {}, "limits": {}, "tasks": []})
        self.assertEqual(response.status_code, 400)

    def test_policy_rejects_script_path_traversal(self):
        self.login()
        response = self.client.post(
            "/api/policy",
            json={
                "services": {},
                "limits": {},
                "tasks": [{"name": "escape", "cron": "* * * * *", "script": "../backup.sh"}],
            },
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.policy_path.exists())

    def test_policy_rejects_non_string_service_state(self):
        self.login()
        response = self.client.post(
            "/api/policy",
            json={"services": {"cron": []}, "limits": {}, "tasks": []},
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_failure_is_reported_as_unavailable(self):
        self.login()
        response = self.client.post(
            "/api/apply",
            json={},
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Could not run policy helper", response.data)

    @patch("app.subprocess.run")
    def test_apply_runs_only_configured_helper(self, run_helper):
        self.login()
        run_helper.return_value.returncode = 0
        run_helper.return_value.stdout = "OK: policy applied\n"
        run_helper.return_value.stderr = ""
        response = self.client.post(
            "/api/apply",
            json={"command": "ignored"},
            headers={"X-CSRF-Token": self.session_csrf()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(run_helper.call_args.args[0], ["missing-homedash-helper"])


if __name__ == "__main__":
    unittest.main()
