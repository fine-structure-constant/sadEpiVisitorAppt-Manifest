# PKU 自动预约机（Go）

这是一个独立的 Go 实现，编译后可作为单个可执行文件长期运行。页面和静态资源已嵌入二进制，运行时只需要 `config.json` 和可写的数据库目录。

## 功能

- `/topt`：显示 SHA-1、6 位、30 秒 TOTP。
- `/history`：自动登录并读取预约历史。
- `/appointment`：填写预约，保存学校系统草稿，或立即提交。
- `/stack`：只能从学校历史记录中选择一个未提交草稿，在指定北京时间自动提交。
- SQLite 持久化队列，进程重启后恢复未完成任务。
- 提交失败按 1、2、4 分钟指数退避重试（最长 10 分钟）。
- 提交成功后发送 SMTP 邮件；邮件失败不会再次提交预约，可在队列页面重发邮件。

## 配置

复制 `config.example.json` 为 `config.json`，填写账户、TOTP 和 SMTP。默认读取规则：

1. 可执行文件同目录存在 `config.json` 时优先读取；
2. 否则读取当前工作目录的 `config.json`。

默认在当前工作目录创建 `appointments.db`。端口位于配置的 `server.port`，建议保持 `server.host` 为 `127.0.0.1`，仅通过 SSH 端口转发访问。

## 构建与运行

源码构建需要 Go 1.24 或更高版本；直接使用 `dist` 中的成品则不需要在服务器安装 Go。

```bash
go build -o pku-appointment ./cmd/appointment
chmod +x pku-appointment
./pku-appointment
```

常用选项：

```text
-config /path/config.json       指定配置文件
-db /path/appointments.db      指定数据库
-listen 127.0.0.1:9000         临时覆盖监听地址
-mode all|web|worker            全部、仅网页、仅后台调度
```

日常部署建议使用 `-mode all`。网页本身空闲时开销很小，没有必要另设 WebUI 开关；若确实不想监听端口，可运行 `-mode worker`，需要页面时再切回 `all`，或另起一个 `web` 进程并共用同一数据库。

## Linux systemd 示例

将二进制和配置放到 `/opt/pku-appointment`，数据库放到 `/var/lib/pku-appointment`。配置文件应限制权限：

```bash
chmod 600 /opt/pku-appointment/config.json
```

`/etc/systemd/system/pku-appointment.service`：

```ini
[Unit]
Description=PKU appointment worker and local Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pku-appointment
Group=pku-appointment
WorkingDirectory=/var/lib/pku-appointment
ExecStart=/opt/pku-appointment/pku-appointment -config /opt/pku-appointment/config.json -db /var/lib/pku-appointment/appointments.db -mode all
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

访问时从自己的电脑建立隧道：

```bash
ssh -N -L 8000:127.0.0.1:8000 user@server
```

然后打开 `http://127.0.0.1:8000/stack`。不要将服务监听到公网地址；本应用没有额外的 Web 登录层，`config.json` 也含明文凭据。
