"""Local-only web UI for PKU visitor appointment history."""

from __future__ import annotations

import os
import secrets
import time
from urllib.parse import parse_qs, quote, urlparse

import requests
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from flask import Flask, jsonify, render_template, request, session


APP_ID = "portal2017"
APP_NAME = "北京大学校内信息门户新版"
REDIRECT_URL = "https://portal.pku.edu.cn/portal2017/ssoLogin.do"
OAUTH_URL = (
    "https://iaaa.pku.edu.cn/iaaa/oauth.jsp"
    f"?appID={APP_ID}"
    f"&appName={quote(APP_NAME)}"
    f"&redirectUrl={quote(REDIRECT_URL, safe='')}"
)
PUBLIC_KEY_URL = "https://iaaa.pku.edu.cn/iaaa/getPublicKey.do"
VERIFY_URL = "https://iaaa.pku.edu.cn/iaaa/isMobileAuthen.do"
LOGIN_URL = "https://iaaa.pku.edu.cn/iaaa/oauthlogin.do"
PORTLET_URL = (
    "https://portal.pku.edu.cn/portal2017/util/portletRedir.do"
    "?portletId=sadEpiVisitorAppt"
)
SIMSO_LOGIN_URL = "https://simso.pku.edu.cn/ssapi/simsoLogin"
SIMSO_API_ROOT = "https://simso.pku.edu.cn/ssapi"
HISTORY_URL = "https://simso.pku.edu.cn/ssapi/stuaffair/epiVisitorAppt/getSqxxHis"
APPOINTMENT_API = f"{SIMSO_API_ROOT}/stuaffair/epiVisitorAppt"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
)

app = Flask(__name__)
app.secret_key = os.environ.get("PKU_WEB_SECRET", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Upstream sessions stay server-side. The browser receives only an opaque key.
UPSTREAM_SESSIONS: dict[str, dict] = {}
PENDING_SESSIONS: dict[str, dict] = {}


def new_http_session() -> requests.Session:
    client = requests.Session()
    # This workstation exposes a dead localhost proxy via HTTP(S)_PROXY.
    # Ignore ambient proxy variables by default; set PKU_USE_ENV_PROXY=1 when
    # an explicitly configured working proxy is required.
    client.trust_env = os.environ.get("PKU_USE_ENV_PROXY", "0") == "1"
    client.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    return client


def parse_query_value(url: str, name: str) -> str:
    values = parse_qs(urlparse(url).query).get(name, [])
    if not values:
        raise RuntimeError(f"重定向地址缺少参数 {name}")
    return values[0]


def encrypt_password(password: str, pem_key: str) -> str:
    public_key = load_pem_public_key(pem_key.encode("utf-8"))
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise RuntimeError("IAAA 返回的不是 RSA 公钥")
    encrypted = public_key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    import base64

    return base64.b64encode(encrypted).decode("ascii")


def start_iaaa_session(username: str) -> tuple[str, requests.Session, dict]:
    client = new_http_session()
    client.get(OAUTH_URL, timeout=30).raise_for_status()
    client.cookies.set("userName", username, domain="iaaa.pku.edu.cn", path="/")
    client.cookies.set("remember", "true", domain="iaaa.pku.edu.cn", path="/")
    verify = client.get(
        VERIFY_URL,
        params={"userName": username, "appId": APP_ID, "_rand": secrets.randbits(53)},
        timeout=30,
    )
    verify.raise_for_status()
    info = verify.json()
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


def verify_summary(info: dict) -> dict:
    return {
        "success": bool(info.get("success")),
        "second_factor": bool(info.get("isMobileAuthen")),
        "mode": info.get("authenMode") or "",
        "trusted_device_option": bool(info.get("isUnuAuth")),
        "captcha": bool(info.get("showCode")),
    }


def perform_login(client: requests.Session, username: str, values: dict) -> dict:
    key_response = client.get(PUBLIC_KEY_URL, timeout=30)
    key_response.raise_for_status()
    key_data = key_response.json()
    if not key_data.get("success") or not key_data.get("key"):
        raise RuntimeError("无法获取 IAAA 公钥")

    payload = {
        "appid": APP_ID,
        "userName": username,
        "password": encrypt_password(values["password"], key_data["key"]),
        "randCode": values.get("rand_code", ""),
        "smsCode": values.get("sms_code", ""),
        "otpCode": values.get("otp_code", ""),
        "remTrustChk": "true" if values.get("trust_device", True) else "false",
        "redirUrl": REDIRECT_URL,
    }
    login_response = client.post(
        LOGIN_URL,
        data=payload,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://iaaa.pku.edu.cn",
            "Referer": OAUTH_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    login_response.raise_for_status()
    login_data = login_response.json()
    if not login_data.get("success"):
        error = login_data.get("errors") or {}
        raise ValueError(error.get("msg") or "IAAA 登录失败")

    token = login_data.get("token")
    if not token:
        raise RuntimeError("IAAA 登录响应缺少 token")
    token_url = f"{REDIRECT_URL}?_rand={secrets.randbits(53)}&token={quote(token, safe='')}"
    portal = client.get(token_url, allow_redirects=True, timeout=30)
    portal.raise_for_status()

    simso_entry = client.get(PORTLET_URL, allow_redirects=True, timeout=30)
    simso_entry.raise_for_status()
    if "simso.pku.edu.cn" not in simso_entry.url:
        raise RuntimeError("门户没有跳转到 SIMSO")

    simso_token = parse_query_value(simso_entry.url, "token")
    simso_login = client.get(
        SIMSO_LOGIN_URL,
        params={"token": simso_token, "page": "sadEpiVisitorAppt.html"},
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": simso_entry.url,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    simso_login.raise_for_status()
    simso_data = simso_login.json()
    if not simso_data.get("success"):
        raise ValueError(simso_data.get("msg") or "SIMSO 登录失败")
    if not simso_data.get("sid") or not simso_data.get("xh"):
        raise RuntimeError("SIMSO 登录响应缺少 sid 或学号")

    client.cookies.set("sid", simso_data["sid"], domain="simso.pku.edu.cn", path="/")
    final_url = (
        "https://simso.pku.edu.cn/pages/sadEpiVisitorAppt.html"
        f"?_sk={quote(str(simso_data['xh']), safe='')}#/index"
    )
    client.get(final_url, timeout=30).raise_for_status()
    return {"client": client, "student_id": str(simso_data["xh"]), "sid": simso_data["sid"]}


def current_upstream() -> dict:
    key = session.get("upstream_key")
    if not key or key not in UPSTREAM_SESSIONS:
        raise PermissionError("请先登录")
    return UPSTREAM_SESSIONS[key]


def appointment_request(
    upstream: dict,
    method: str,
    action: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
) -> dict:
    """Call one SIMSO appointment endpoint using the logged-in server session."""
    client: requests.Session = upstream["client"]
    request_params = {
        "sid": upstream["sid"],
        "_sk": upstream["student_id"],
    }
    if params:
        request_params.update(params)
    response = client.request(
        method,
        f"{APPOINTMENT_API}/{action}",
        params=request_params,
        json=data if method.upper() in {"POST", "PUT", "PATCH"} else None,
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (None, 1) and not result.get("success"):
        raise RuntimeError(result.get("msg") or "预约接口返回失败")
    if result.get("success") is False and result.get("code") != 1:
        raise RuntimeError(result.get("msg") or "预约接口返回失败")
    return result


@app.get("/")
def index():
    return render_template("index.html")


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

    client: requests.Session = upstream["client"]
    try:
        response = client.get(
            HISTORY_URL,
            params={
                "sid": upstream["sid"],
                "_sk": upstream["student_id"],
                "pageSize": page_size,
                "pageNum": page,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 1 and not data.get("success"):
            return jsonify(error=data.get("msg") or "历史记录读取失败"), 502
        return jsonify(data)
    except (requests.RequestException, ValueError) as exc:
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
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
