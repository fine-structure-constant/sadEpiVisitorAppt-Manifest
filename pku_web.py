"""Local-only web UI for PKU visitor appointment history."""

from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from local_config import LocalConfig
from notification_mailer import send_appointment_success
from pku_client import (
    appointment_request,
    history_request,
    perform_login,
    prepare_iaaa_session,
    verify_summary,
)
from queue_store import AppointmentQueue
from scheduler_service import QueueScheduler


TOTP_INTERVAL = 30
CONFIG_PATH = Path(
    os.environ.get("PKU_TOTP_CONFIG", Path(__file__).with_name("config.json"))
)
QUEUE_DB_PATH = Path(
    os.environ.get(
        "PKU_QUEUE_DB", Path(__file__).with_name("data") / "appointments.db"
    )
)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
LOCAL_CONFIG = LocalConfig(CONFIG_PATH, totp_interval=TOTP_INTERVAL)

app = Flask(__name__)
app.secret_key = os.environ.get("PKU_WEB_SECRET", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Upstream sessions stay server-side. The browser receives only an opaque key.
UPSTREAM_SESSIONS: dict[str, dict] = {}
PENDING_SESSIONS: dict[str, dict] = {}
AUTO_UPSTREAM: dict | None = None
AUTO_UPSTREAM_LOCK = threading.Lock()
QUEUE = AppointmentQueue(QUEUE_DB_PATH)
QUEUE.initialize()


def configured_account() -> tuple[str, str]:
    return LOCAL_CONFIG.account()


def configured_smtp() -> dict:
    return LOCAL_CONFIG.smtp()


def start_iaaa_session(username: str) -> tuple[str, requests.Session, dict]:
    client, info = prepare_iaaa_session(username)
    pending_id = secrets.token_urlsafe(32)
    PENDING_SESSIONS[pending_id] = {
        "username": username,
        "client": client,
        "created": time.time(),
    }
    return pending_id, client, info


def cleanup_pending() -> None:
    cutoff = time.time() - 10 * 60
    for key, value in list(PENDING_SESSIONS.items()):
        if value["created"] < cutoff:
            PENDING_SESSIONS.pop(key, None)


def login_from_local_config() -> dict:
    """Create a fresh upstream session using only local secrets."""
    username, password = configured_account()
    client, verification_info = prepare_iaaa_session(username)
    summary = verify_summary(verification_info)
    if summary["captcha"]:
        raise RuntimeError("账户登录要求图形验证码，无法自动登录")

    otp_code = ""
    if summary["second_factor"]:
        mode = str(summary["mode"]).upper()
        if mode != "OTP":
            raise RuntimeError(f"账户登录要求 {mode or '未知'} 二次验证，无法自动填写")
        otp_code = configured_totp().now()

    result = perform_login(
        client,
        username,
        {
            "password": password,
            "otp_code": otp_code,
            "sms_code": "",
            "rand_code": "",
            "trust_device": True,
        },
    )
    return {**result, "username": username, "created": time.time()}


def automatic_upstream(*, force: bool = False) -> dict:
    global AUTO_UPSTREAM
    with AUTO_UPSTREAM_LOCK:
        if (
            not force
            and AUTO_UPSTREAM is not None
            and time.time() - AUTO_UPSTREAM["created"] < 15 * 60
        ):
            return AUTO_UPSTREAM
        AUTO_UPSTREAM = login_from_local_config()
        return AUTO_UPSTREAM


def current_upstream() -> dict:
    key = session.get("upstream_key")
    if key and key in UPSTREAM_SESSIONS:
        return UPSTREAM_SESSIONS[key]
    try:
        return automatic_upstream()
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        raise PermissionError(f"自动登录失败：{exc}") from exc


@app.get("/")
def index():
    return redirect(url_for("stack_page"))


def configured_totp():
    """Load the local TOTP secret without exposing it to the browser."""
    return LOCAL_CONFIG.totp()


@app.get("/topt")
def topt_page():
    return render_template("totp.html")


@app.get("/totp")
def totp_legacy_page():
    return redirect(url_for("topt_page"), code=302)


@app.get("/history")
def history_page():
    return render_template("history.html")


@app.get("/appointment")
def appointment_page():
    return render_template("appointment.html")


@app.get("/stack")
def stack_page():
    return render_template("stack.html")


@app.get("/api/totp")
def totp_code():
    now = time.time()
    try:
        code = configured_totp().at(now)
    except RuntimeError as exc:
        response = jsonify(error=str(exc))
        response.headers["Cache-Control"] = "no-store"
        return response, 503

    valid_until = (int(now) // TOTP_INTERVAL + 1) * TOTP_INTERVAL
    response = jsonify(
        code=code,
        digits=6,
        period=TOTP_INTERVAL,
        server_time=now,
        valid_until=valid_until,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/verification")
def verification():
    cleanup_pending()
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    if not username:
        return jsonify(error="请输入用户名"), 400
    try:
        pending_id, _, info = start_iaaa_session(username)
        return jsonify(pending_id=pending_id, **verify_summary(info))
    except requests.RequestException as exc:
        return jsonify(error=f"无法连接 IAAA：{exc}"), 502


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not username or not password:
        return jsonify(error="用户名和密码不能为空"), 400

    pending_id = str(body.get("pending_id", ""))
    pending = PENDING_SESSIONS.pop(pending_id, None)
    if pending and pending["username"] == username:
        client = pending["client"]
    else:
        try:
            _, client, _ = start_iaaa_session(username)
        except requests.RequestException as exc:
            return jsonify(error=f"无法连接 IAAA：{exc}"), 502

    try:
        result = perform_login(
            client,
            username,
            {
                "password": password,
                "otp_code": str(body.get("otp_code", "")).strip(),
                "sms_code": str(body.get("sms_code", "")).strip(),
                "rand_code": str(body.get("rand_code", "")).strip(),
                "trust_device": bool(body.get("trust_device", True)),
            },
        )
    except ValueError as exc:
        return jsonify(error=str(exc), needs_verification=True), 401
    except (requests.RequestException, RuntimeError) as exc:
        return jsonify(error=str(exc)), 502

    upstream_key = secrets.token_urlsafe(32)
    UPSTREAM_SESSIONS[upstream_key] = {
        **result,
        "username": username,
        "created": time.time(),
    }
    session["upstream_key"] = upstream_key
    return jsonify(student_id=result["student_id"])


@app.get("/api/history")
def history():
    try:
        upstream = current_upstream()
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401

    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(50, max(1, int(request.args.get("pageSize", "10"))))
    except ValueError:
        return jsonify(error="页码参数无效"), 400

    try:
        return jsonify(history_request(upstream, page, page_size))
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"历史记录请求失败：{exc}"), 502


@app.get("/api/appointment/config")
def appointment_config():
    """Return appointment form configuration and the user's recent visitors."""
    try:
        upstream = current_upstream()
        status = appointment_request(upstream, "GET", "getSqzt", params={"flag": ""})
        contacts = appointment_request(upstream, "GET", "getJqyyrList")
        return jsonify(status=status, contacts=contacts)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"预约配置读取失败：{exc}"), 502


@app.get("/api/appointment/check-date")
def appointment_check_date():
    try:
        upstream = current_upstream()
        date = str(request.args.get("date", "")).strip()
        if not date:
            return jsonify(error="请选择预约日期"), 400
        return jsonify(appointment_request(upstream, "GET", "checkSqrq", params={"sqrq": date}))
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"预约日期检查失败：{exc}"), 502


@app.get("/api/appointment/detail")
def appointment_detail():
    try:
        upstream = current_upstream()
        sqxxid = str(request.args.get("sqxxid", "")).strip()
        if not sqxxid:
            return jsonify(error="缺少申请编号"), 400
        return jsonify(
            appointment_request(upstream, "GET", "getSqxx", params={"sqxxid": sqxxid})
        )
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"预约申请读取失败：{exc}"), 502


APPOINTMENT_FIELDS = {
    "sqxxid",
    "lxdh",
    "yyrq",
    "yysj",
    "yyxm",
    "yysy",
    "byyrxm",
    "byyrzjh",
    "byyrlxdh",
}


def appointment_payload(body: dict) -> dict:
    payload = {
        key: str(body.get(key, "")).strip()
        for key in APPOINTMENT_FIELDS
        if body.get(key) is not None
    }
    if not payload.get("sqxxid"):
        payload.pop("sqxxid", None)
    if not payload.get("lxdh"):
        raise ValueError("请选择或填写预约人联系电话")
    required = {
        "yyrq": "请选择预约日期",
        "yysj": "请选择预约时间",
        "yyxm": "请选择预约校门",
        "yysy": "请输入预约事由",
        "byyrxm": "请输入被预约人姓名",
        "byyrzjh": "请输入被预约人证件号",
        "byyrlxdh": "请输入被预约人联系电话",
    }
    for key, message in required.items():
        if not payload.get(key):
            raise ValueError(message)
    return payload


def parse_beijing_time(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("请选择自动提交时间")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("自动提交时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    else:
        parsed = parsed.astimezone(BEIJING_TZ)
    return parsed.timestamp()


def beijing_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, BEIJING_TZ).isoformat(timespec="seconds")


def public_job(job: dict) -> dict:
    item = dict(job)
    for field in (
        "scheduled_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    ):
        item[field] = beijing_iso(item.get(field))
    return item


def submit_existing_appointment(upstream: dict, remote_id: str) -> dict:
    verification = appointment_request(
        upstream, "GET", "sendEcyzCode", params={"sqxxid": remote_id}
    )
    row = verification.get("row") or {}
    if not isinstance(row, dict):
        raise RuntimeError("预约验证接口返回格式异常")
    verification_type = str(row.get("type", "")).lower()
    if verification_type == "byz":
        code = ""
    elif verification_type in {"opt", "otp"}:
        if row.get("flag") is False:
            raise RuntimeError(row.get("errmsg") or "预约令牌验证初始化失败")
        code = configured_totp().now()
    else:
        description = {
            "sms": "短信验证码",
            "wx": "微信验证码",
        }.get(verification_type, verification_type or "未知验证")
        raise RuntimeError(f"当前预约要求{description}，无法自动填写")

    submitted = appointment_request(
        upstream,
        "GET",
        "submitSqxx",
        params={"sqxxid": remote_id, "code": code},
    )
    return {"verification_type": verification_type, "submitted": submitted}


def perform_queued_appointment(job: dict) -> tuple[str, dict]:
    """Submit a queued remote draft using a fresh login session."""
    upstream = login_from_local_config()
    appointment_payload(job["payload"])
    remote_id = str(job.get("remote_id") or "").strip()
    if not remote_id:
        raise RuntimeError("队列任务缺少已有的预约申请编号")
    detail = appointment_request(
        upstream, "GET", "getSqxx", params={"sqxxid": remote_id}
    )
    detail_row = detail.get("row") or {}
    if isinstance(detail_row, dict) and str(detail_row.get("tjbz", "")).lower() == "y":
        return remote_id, {
            "saved": True,
            "reconciled": True,
            "submitted": detail,
        }

    return remote_id, {"saved": True, **submit_existing_appointment(upstream, remote_id)}


def send_success_email(job: dict) -> None:
    send_appointment_success(configured_smtp(), job)


def notify_completed_job(job_id: int) -> dict | None:
    job = QUEUE.get(job_id)
    if not job or job["status"] != "completed":
        return job
    try:
        configured_smtp()
    except RuntimeError as exc:
        return QUEUE.set_email_result(job_id, "skipped", str(exc))
    try:
        send_success_email(job)
        return QUEUE.set_email_result(job_id, "sent")
    except Exception as exc:
        return QUEUE.set_email_result(job_id, "failed", str(exc))


def process_queue_job(job: dict) -> None:
    try:
        remote_id, result = perform_queued_appointment(job)
        QUEUE.complete(job["id"], remote_id=remote_id, result=result)
        notify_completed_job(job["id"])
    except Exception as exc:
        QUEUE.fail(job["id"], str(exc))


SCHEDULER = QueueScheduler(QUEUE, process_queue_job)


@app.get("/api/app-status")
def app_status():
    try:
        configured_account()
        account_ready = True
        account_error = ""
    except RuntimeError as exc:
        account_ready = False
        account_error = str(exc)
    try:
        configured_totp()
        totp_ready = True
        totp_error = ""
    except RuntimeError as exc:
        totp_ready = False
        totp_error = str(exc)
    try:
        configured_smtp()
        smtp_ready = True
        smtp_error = ""
    except (TypeError, ValueError, RuntimeError) as exc:
        smtp_ready = False
        smtp_error = str(exc)
    return jsonify(
        account_configured=account_ready,
        account_error=account_error,
        totp_configured=totp_ready,
        totp_error=totp_error,
        smtp_configured=smtp_ready,
        smtp_error=smtp_error,
        scheduler_running=SCHEDULER.running,
        counts=QUEUE.counts(),
        timezone="Asia/Shanghai",
    )


@app.get("/api/stack")
def stack_list():
    return jsonify(jobs=[public_job(job) for job in QUEUE.list()])


@app.post("/api/stack/from-history")
def stack_add_from_history():
    body = request.get_json(silent=True) or {}
    remote_id = str(body.get("sqxxid", "")).strip()
    if not remote_id:
        return jsonify(error="请选择一条历史预约草稿"), 400
    existing = QUEUE.active_for_remote(remote_id)
    if existing:
        return jsonify(error=f"该申请已在任务 #{existing['id']} 中等待提交"), 409
    try:
        scheduled_at = parse_beijing_time(body.get("scheduled_at"))
        max_attempts = int(body.get("max_attempts", 3))
        upstream = current_upstream()
        detail = appointment_request(
            upstream, "GET", "getSqxx", params={"sqxxid": remote_id}
        )
        row = detail.get("row") or {}
        if not isinstance(row, dict):
            raise RuntimeError("历史预约详情格式异常")
        if str(row.get("tjbz", "")).lower() == "y":
            return jsonify(error="该预约已经提交，不能再次加入队列"), 409
        payload = appointment_payload(row)
        payload.pop("sqxxid", None)
        job = QUEUE.add(
            payload,
            scheduled_at,
            max_attempts=max_attempts,
            remote_id=remote_id,
        )
        SCHEDULER.wake()
        return jsonify(job=public_job(job)), 201
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    except (requests.RequestException, RuntimeError) as exc:
        return jsonify(error=f"历史预约读取失败：{exc}"), 502


@app.patch("/api/stack/<int:job_id>")
def stack_reschedule(job_id: int):
    body = request.get_json(silent=True) or {}
    try:
        scheduled_at = parse_beijing_time(body.get("scheduled_at"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not QUEUE.reschedule(job_id, scheduled_at):
        return jsonify(error="只有等待或重试中的任务可以改期"), 409
    SCHEDULER.wake()
    return jsonify(job=public_job(QUEUE.get(job_id)))


@app.post("/api/stack/<int:job_id>/cancel")
def stack_cancel(job_id: int):
    if not QUEUE.cancel(job_id):
        return jsonify(error="任务不存在，或当前状态不能取消"), 409
    return jsonify(job=public_job(QUEUE.get(job_id)))


@app.post("/api/stack/<int:job_id>/retry")
def stack_retry(job_id: int):
    if not QUEUE.retry(job_id):
        return jsonify(error="只有失败或已取消的任务可以重试"), 409
    SCHEDULER.wake()
    return jsonify(job=public_job(QUEUE.get(job_id)))


@app.post("/api/stack/<int:job_id>/email")
def stack_resend_email(job_id: int):
    job = QUEUE.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify(error="只有已完成任务可以发送成功通知"), 409
    updated = notify_completed_job(job_id)
    if updated and updated.get("email_status") == "sent":
        return jsonify(job=public_job(updated))
    return jsonify(error=(updated or {}).get("email_error") or "邮件发送失败"), 502


@app.delete("/api/stack/<int:job_id>")
def stack_delete(job_id: int):
    if not QUEUE.delete(job_id):
        return jsonify(error="只能删除已完成、失败或已取消的任务"), 409
    return jsonify(ok=True)


@app.post("/api/appointment/save")
def appointment_save():
    try:
        upstream = current_upstream()
        payload = appointment_payload(request.get_json(silent=True) or {})
        return jsonify(appointment_request(upstream, "POST", "saveSqxx", data=payload))
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"预约保存失败：{exc}"), 502


@app.post("/api/appointment/submit-automatic")
def appointment_submit_automatic():
    body = request.get_json(silent=True) or {}
    remote_id = str(body.get("sqxxid", "")).strip()
    if not remote_id:
        return jsonify(error="请先保存预约申请"), 400
    try:
        upstream = current_upstream()
        detail = appointment_request(
            upstream, "GET", "getSqxx", params={"sqxxid": remote_id}
        )
        row = detail.get("row") or {}
        if not isinstance(row, dict):
            raise RuntimeError("预约详情格式异常")
        if str(row.get("tjbz", "")).lower() == "y":
            return jsonify(error="该预约已经提交"), 409
        payload = appointment_payload(row)
        result = submit_existing_appointment(upstream, remote_id)

        try:
            smtp = configured_smtp()
        except RuntimeError as exc:
            email_status = "skipped"
            email_error = str(exc)
        else:
            try:
                send_appointment_success(
                    smtp,
                    {"id": "即时提交", "remote_id": remote_id, "payload": payload},
                )
                email_status = "sent"
                email_error = ""
            except Exception as exc:
                email_status = "failed"
                email_error = str(exc)
        return jsonify(
            sqxxid=remote_id,
            result=result,
            email_status=email_status,
            email_error=email_error,
        )
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (requests.RequestException, RuntimeError) as exc:
        return jsonify(error=f"预约自动提交失败：{exc}"), 502


@app.post("/api/appointment/send-code")
def appointment_send_code():
    try:
        upstream = current_upstream()
        body = request.get_json(silent=True) or {}
        sqxxid = str(body.get("sqxxid", "")).strip()
        if not sqxxid:
            return jsonify(error="请先保存预约申请"), 400
        return jsonify(
            appointment_request(upstream, "GET", "sendEcyzCode", params={"sqxxid": sqxxid})
        )
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"发送预约验证码失败：{exc}"), 502


@app.post("/api/appointment/submit")
def appointment_submit():
    try:
        upstream = current_upstream()
        body = request.get_json(silent=True) or {}
        sqxxid = str(body.get("sqxxid", "")).strip()
        code = str(body.get("code", "")).strip()
        if not sqxxid:
            return jsonify(error="请先保存预约申请"), 400
        return jsonify(
            appointment_request(
                upstream,
                "GET",
                "submitSqxx",
                params={"sqxxid": sqxxid, "code": code},
            )
        )
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"预约提交失败：{exc}"), 502


@app.post("/api/logout")
def logout():
    key = session.pop("upstream_key", None)
    if key:
        UPSTREAM_SESSIONS.pop(key, None)
    return jsonify(ok=True)


if __name__ == "__main__":
    SCHEDULER.start()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
