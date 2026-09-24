# 北大访客自动预约机

本地 Web 应用会使用保存在 `config.json` 中的账户和 TOTP 密钥登录门户。预约任务保存在本机 SQLite 队列中，并按北京时间自动保存、验证和提交。提交成功后可以通过 SMTP 给自己发送邮件。

## 配置

复制 `config.example.json` 为 `config.json`，填写以下内容：

- `account.username`、`account.password`：门户账户和密码。
- `totp_secret`：Base32 密钥；使用 SHA-1、6 位、30 秒周期。
- `smtp`：成功通知邮件配置。587 端口默认 STARTTLS，465 端口自动使用 SSL；`to` 省略时发送给 `username`。

`config.json`、`data/` 均已被 Git 忽略。账户密码、TOTP 密钥和预约队列不会进入版本库。

## 启动

```powershell
pip install -r requirements.txt
python pku_web.py
```

必须使用 `python pku_web.py` 启动，后台调度线程才会运行。应用只监听 `127.0.0.1`。

## 页面

- `http://127.0.0.1:5000/topt`：当前 TOTP 验证码。
- `http://127.0.0.1:5000/history`：过往预约记录。
- `http://127.0.0.1:5000/appointment`：填写并保存预约草稿，或立即提交。
- `http://127.0.0.1:5000/stack`：从历史记录选择已创建、尚未提交的草稿，设置北京时间，并查看、改期、取消、重试或删除任务。

队列保存在 `data/appointments.db`。队列只负责提交已经存在于学校系统中的草稿，不会在执行时重复创建预约。到期任务临时失败后最多按配置重试；应用重启时，未完成的运行中任务会恢复为等待重试。邮件失败不会导致预约重复提交，可在任务队列中单独重发邮件。

核心代码按职责拆分为：`pku_client.py`（学校接口）、`local_config.py`（本地配置）、`queue_store.py`（持久化队列）、`scheduler_service.py`（调度线程）和 `notification_mailer.py`（邮件通知）。
