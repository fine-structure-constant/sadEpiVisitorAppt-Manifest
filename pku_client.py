"""PKU IAAA login and SIMSO appointment API client."""

from __future__ import annotations

import base64
import os
import secrets
from urllib.parse import parse_qs, quote, urlparse

import requests
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key


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
HISTORY_URL = f"{SIMSO_API_ROOT}/stuaffair/epiVisitorAppt/getSqxxHis"
APPOINTMENT_API = f"{SIMSO_API_ROOT}/stuaffair/epiVisitorAppt"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
)


def new_http_session() -> requests.Session:
    client = requests.Session()
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
    return base64.b64encode(encrypted).decode("ascii")


def prepare_iaaa_session(username: str) -> tuple[requests.Session, dict]:
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
    return client, verify.json()


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
    client.get(token_url, allow_redirects=True, timeout=30).raise_for_status()

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


def appointment_request(
    upstream: dict,
    method: str,
    action: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
) -> dict:
    client: requests.Session = upstream["client"]
    request_params = {"sid": upstream["sid"], "_sk": upstream["student_id"]}
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


def history_request(upstream: dict, page: int, page_size: int) -> dict:
    client: requests.Session = upstream["client"]
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
        raise RuntimeError(data.get("msg") or "历史记录读取失败")
    return data
