"""SMTP notification delivery for completed appointment jobs."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage


def send_appointment_success(smtp: dict, job: dict) -> None:
    payload = job["payload"]
    message = EmailMessage()
    message["Subject"] = f"访客预约提交成功：{payload.get('byyrxm', '')}"
    message["From"] = smtp["from"]
    message["To"] = smtp["to"]
    message.set_content(
        "\n".join(
            [
                "自动预约机已成功提交预约。",
                "",
                f"队列编号：{job['id']}",
                f"申请编号：{job.get('remote_id') or ''}",
                f"被预约人：{payload.get('byyrxm', '')}",
                f"预约日期：{payload.get('yyrq', '')}",
                f"预约时间：{payload.get('yysj', '')}",
                f"预约校门：{payload.get('yyxm', '')}",
                f"预约事由：{payload.get('yysy', '')}",
                "",
                "此邮件由本机自动预约机发送。",
            ]
        )
    )

    if smtp["port"] == 465:
        with smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=30) as client:
            client.login(smtp["username"], smtp["password"])
            client.send_message(message)
        return

    with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as client:
        client.ehlo()
        if smtp["starttls"]:
            client.starttls()
            client.ehlo()
        client.login(smtp["username"], smtp["password"])
        client.send_message(message)
