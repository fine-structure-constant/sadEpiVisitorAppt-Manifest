"""Validated access to local secrets stored outside version control."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyotp


class LocalConfig:
    def __init__(self, path: Path | str, *, totp_interval: int = 30):
        self.path = Path(path)
        self.totp_interval = totp_interval

    def read(self) -> dict:
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError("尚未创建本地 config.json") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("本地 config.json 无法读取") from exc
        if not isinstance(config, dict):
            raise RuntimeError("本地 config.json 格式无效")
        return config

    def account(self) -> tuple[str, str]:
        account = self.read().get("account")
        if not isinstance(account, dict):
            raise RuntimeError("尚未在 config.json 中配置账户")
        username = str(account.get("username", "")).strip()
        password = str(account.get("password", ""))
        if not username or not password:
            raise RuntimeError("config.json 中的账户或密码为空")
        return username, password

    def totp(self) -> pyotp.TOTP:
        configured_secret = self.read().get("totp_secret")
        secret = (
            "".join(configured_secret.split()).upper()
            if isinstance(configured_secret, str)
            else ""
        )
        if not secret:
            raise RuntimeError("尚未配置 TOTP 密钥")
        token = pyotp.TOTP(
            secret,
            digits=6,
            interval=self.totp_interval,
            digest=hashlib.sha1,
        )
        try:
            token.at(0)
        except Exception as exc:
            raise RuntimeError("TOTP 密钥格式无效") from exc
        return token

    def smtp(self) -> dict:
        smtp = self.read().get("smtp")
        if not isinstance(smtp, dict):
            raise RuntimeError("尚未在 config.json 中配置 SMTP")
        try:
            port = int(smtp.get("port", 587))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("SMTP 端口无效") from exc
        values = {
            "host": str(smtp.get("host", "")).strip(),
            "port": port,
            "username": str(smtp.get("username", "")).strip(),
            "password": str(smtp.get("password", "")),
            "from": str(smtp.get("from", "")).strip(),
            "to": str(smtp.get("to") or smtp.get("username") or "").strip(),
            "starttls": bool(smtp.get("starttls", True)),
        }
        required = ("host", "username", "password", "from", "to")
        if not all(values[key] for key in required):
            raise RuntimeError("config.json 中的 SMTP 配置不完整")
        if not 1 <= values["port"] <= 65535:
            raise RuntimeError("SMTP 端口无效")
        return values
