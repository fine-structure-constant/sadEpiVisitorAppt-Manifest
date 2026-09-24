from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import notification_mailer
import pku_web
from queue_store import AppointmentQueue


VALID_APPOINTMENT = {
    "lxdh": "13800138000",
    "yyrq": "2030-01-02",
    "yysj": "10:00",
    "yyxm": "东门",
    "yysy": "测试预约",
    "byyrxm": "测试访客",
    "byyrzjh": "ABC123456",
    "byyrlxdh": "13900139000",
}


class FakeSmtpClient:
    def __init__(self):
        self.started_tls = False
        self.credentials = None
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        self.message = message


class MailerTests(unittest.TestCase):
    def test_starttls_success_notification(self):
        client = FakeSmtpClient()
        smtp = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "me@example.com",
            "password": "secret",
            "from": "me@example.com",
            "to": "me@example.com",
            "starttls": True,
        }
        job = {
            "id": 8,
            "remote_id": "REMOTE-8",
            "payload": VALID_APPOINTMENT,
        }
        with patch.object(notification_mailer.smtplib, "SMTP", return_value=client):
            notification_mailer.send_appointment_success(smtp, job)
        self.assertTrue(client.started_tls)
        self.assertEqual(client.credentials, ("me@example.com", "secret"))
        self.assertEqual(client.message["To"], "me@example.com")
        self.assertIn("REMOTE-8", client.message.get_content())


class QueueStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = AppointmentQueue(Path(self.temp.name) / "queue.db")
        self.queue.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_lifecycle_and_recovery_fields(self):
        job = self.queue.add(VALID_APPOINTMENT, time.time() - 1, max_attempts=3)
        claimed = self.queue.claim_due()
        self.assertEqual(claimed["id"], job["id"])
        self.assertEqual(claimed["status"], "running")
        failed = self.queue.fail(job["id"], "temporary")
        self.assertEqual(failed["status"], "retrying")
        self.assertIsNotNone(failed["next_attempt_at"])

        self.assertTrue(self.queue.cancel(job["id"]))
        self.assertTrue(self.queue.retry(job["id"], time.time() + 60))
        self.assertEqual(self.queue.get(job["id"])["status"], "pending")

    def test_completed_job_tracks_email_separately(self):
        job = self.queue.add(VALID_APPOINTMENT, time.time() - 1)
        self.queue.claim_due()
        completed = self.queue.complete(
            job["id"], remote_id="REMOTE-1", result={"success": True}
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["email_status"], "pending")
        updated = self.queue.set_email_result(job["id"], "failed", "smtp down")
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["email_status"], "failed")


class WebAndAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = AppointmentQueue(Path(self.temp.name) / "queue.db")
        self.queue.initialize()
        self.original_queue = pku_web.QUEUE
        pku_web.QUEUE = self.queue
        pku_web.app.config.update(TESTING=True)
        self.client = pku_web.app.test_client()

    def tearDown(self):
        pku_web.QUEUE = self.original_queue
        self.temp.cleanup()

    def test_all_machine_pages_render(self):
        for path in ("/topt", "/history", "/appointment", "/stack"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
        self.assertEqual(self.client.get("/totp").status_code, 302)

    def test_stack_api_lifecycle(self):
        stored = self.queue.add(
            VALID_APPOINTMENT,
            pku_web.parse_beijing_time("2030-01-01T12:00:00"),
            max_attempts=3,
            remote_id="REMOTE-LIFECYCLE",
        )
        job = pku_web.public_job(stored)
        self.assertTrue(job["scheduled_at"].endswith("+08:00"))

        changed = self.client.patch(
            f"/api/stack/{job['id']}",
            json={"scheduled_at": "2030-01-01T13:00:00"},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post(f"/api/stack/{job['id']}/cancel").status_code, 200)
        self.assertEqual(self.client.post(f"/api/stack/{job['id']}/retry").status_code, 200)

    def test_stack_adds_existing_history_draft_without_saving_again(self):
        detail = {"code": 1, "row": {**VALID_APPOINTMENT, "sqxxid": "REMOTE-9", "tjbz": "n"}}
        with (
            patch.object(pku_web, "current_upstream", return_value={"sid": "s"}),
            patch.object(pku_web, "appointment_request", return_value=detail) as request_mock,
        ):
            response = self.client.post(
                "/api/stack/from-history",
                json={
                    "sqxxid": "REMOTE-9",
                    "scheduled_at": "2030-01-01T12:00:00",
                    "max_attempts": 3,
                },
            )
        self.assertEqual(response.status_code, 201)
        job = response.get_json()["job"]
        self.assertEqual(job["remote_id"], "REMOTE-9")
        self.assertEqual(request_mock.call_args.args[2], "getSqxx")
        duplicate = self.client.post(
            "/api/stack/from-history",
            json={"sqxxid": "REMOTE-9", "scheduled_at": "2030-01-01T13:00:00"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_automatic_submission_uses_totp(self):
        calls = []

        def fake_request(upstream, method, action, *, params=None, data=None):
            calls.append((method, action, params, data))
            if action == "getSqxx":
                return {"code": 1, "row": {**VALID_APPOINTMENT, "sqxxid": "REMOTE-2", "tjbz": "n"}}
            if action == "sendEcyzCode":
                return {"code": 1, "row": {"type": "opt", "flag": True}}
            return {"code": 1, "success": True}

        class FakeTotp:
            @staticmethod
            def now():
                return "123456"

        with (
            patch.object(pku_web, "login_from_local_config", return_value={"sid": "s"}),
            patch.object(pku_web, "appointment_request", side_effect=fake_request),
            patch.object(pku_web, "configured_totp", return_value=FakeTotp()),
        ):
            remote_id, result = pku_web.perform_queued_appointment(
                {"id": 1, "remote_id": "REMOTE-2", "payload": VALID_APPOINTMENT}
            )

        self.assertEqual(remote_id, "REMOTE-2")
        self.assertTrue(result["submitted"]["success"])
        self.assertEqual(calls[-1][2]["code"], "123456")

    def test_retry_reconciles_existing_remote_submission(self):
        calls = []

        def fake_request(upstream, method, action, *, params=None, data=None):
            calls.append(action)
            return {"code": 1, "row": {"sqxxid": "REMOTE-4", "tjbz": "y"}}

        with (
            patch.object(pku_web, "login_from_local_config", return_value={"sid": "s"}),
            patch.object(pku_web, "appointment_request", side_effect=fake_request),
        ):
            remote_id, result = pku_web.perform_queued_appointment(
                {"id": 9, "remote_id": "REMOTE-4", "payload": VALID_APPOINTMENT}
            )

        self.assertEqual(remote_id, "REMOTE-4")
        self.assertTrue(result["reconciled"])
        self.assertEqual(calls, ["getSqxx"])

    def test_email_failure_never_reopens_completed_job(self):
        job = self.queue.add(VALID_APPOINTMENT, time.time() - 1)
        self.queue.claim_due()
        self.queue.complete(job["id"], remote_id="REMOTE-3", result={"success": True})
        with (
            patch.object(pku_web, "configured_smtp", return_value={"host": "smtp"}),
            patch.object(pku_web, "send_success_email", side_effect=OSError("offline")),
        ):
            updated = pku_web.notify_completed_job(job["id"])
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["email_status"], "failed")


if __name__ == "__main__":
    unittest.main()
