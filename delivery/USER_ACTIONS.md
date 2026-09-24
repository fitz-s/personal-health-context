# 需要你本人做的事（一次性清单）

只列账户授权、设备签名、付费/隐私决定。每项后面写了完成后我（或你）执行的命令。其余工程事项已完成或已在 KNOWN_LIMITATIONS 里说明。

## 1. 让 ChatGPT 连上本机（解锁：真实写读、新会话读取、原文件 hash、语音转写实测）— 约 10 分钟
为什么必须你做：要在你的 OpenAI 账户里新建凭据和 ChatGPT app，这是账户变更。

1. 打开 https://platform.openai.com/settings/organization/tunnels → **Create tunnel**，名字建议 `phctx-mac`，记下 `tunnel_…` ID。
2. 打开 https://platform.openai.com/settings/organization/api-keys → 新建一个 runtime key（只需 Tunnels Read + Use）。
3. 在终端把 key 存进 Keychain（会提示你粘贴，不会进命令历史）：
   ```
   security add-generic-password -s phctx-tunnel-key -a phctx -w
   ```
4. 运行：
   ```
   ~/personal-health-context/ops/tunnel.sh init tunnel_你的ID
   ~/personal-health-context/ops/tunnel.sh install
   ```
5. ChatGPT → https://chatgpt.com/plugins → 新建 developer-mode app：Connection 选 **Tunnel**，选 `phctx-mac`，名称 `Personal Health Context`。
6. 告诉我"tunnel 好了"。我会用合成内容跑：写入 nonce → 服务端独立查库 → 新会话读回 → 上传合成照片/PDF 验 hash → 手机端同样测一次，并把观察到的真实文件下载域名写进 `allowed_download_hosts`（在此之前任何附件下载都会被拒绝，这是有意的）。

## 2. iPhone 后台同步（解锁：Apple 持续增量、删除、离线恢复实测）— 约 30–60 分钟
为什么必须你做：需要安装 Xcode、用你的 Apple ID 签名、在你的 iPhone 上授权 HealthKit。

1. 从 App Store 安装 Xcode（约 10+ GB；本机现有 83 GB 可用）。
2. `brew install xcodegen`，然后按 `ios/BUILD_ON_DEVICE.md` 生成工程、选你的 Team、装到 iPhone。
3. Mac 上：`~/personal-health-context/ops/install.sh --with-ingest`，再 `PYTHONPATH=~/personal-health-context/src ~/personal-health-context/.venv/bin/python -m phctx pair-device`，把输出的配对链接/码输入手机 helper。
4. 首次授权界面可见；之后日常无需打开。

## 3. Apple Health 历史回填（可选，立即可用）
iPhone 健康 App → 头像 → 导出所有健康数据 → 把 export.zip 放到 Mac，然后：
```
PYTHONPATH=~/personal-health-context/src ~/personal-health-context/.venv/bin/python -m phctx import-apple ~/Downloads/export.zip --dry-run
PYTHONPATH=~/personal-health-context/src ~/personal-health-context/.venv/bin/python -m phctx import-apple ~/Downloads/export.zip
```
Oura 镜像条目会被自动过滤并只计数。这只是历史回填，不是持续同步。

## 4. 后台推理是否启用（隐私/配额决定）
现在后台 worker 已运行但**不调用模型**（`[model] enabled = false`，shadow 模式）。可选：
- A. 用你已登录的 Codex（ChatGPT 账户，无新增付费）做后台调查：在 `~/.config/phctx/config.toml` 设
  `enabled = true`、`backend = "codex_cli"`、`model_id = "gpt-5.6-sol"`。这会把**你本地已授权的健康记录**（经只读工具）发送给 OpenAI，并消耗你的 ChatGPT/Codex 配额；默认每日上限 12 次。
- B. 保持关闭：前台对话照常可用，后台只做同步/抽取/调度，不会有主动发现。
- （OpenAI API 付费路线未启用：平台账户显示需先充值。）

## 5. 加密 iCloud 备份（隐私决定）
本机已在合成数据上实测"加密包写入 iCloud Drive → 读回 → 解密 → 恢复"通过。要对生产数据启用：在 config 的 `[backup]` 取消注释 `cloud_dir = …iCloud…/PersonalHealthContextBackups`，并把 launchd 备份改为加密（`ops/backup.sh --encrypt`）。密钥在 Keychain `phctx-backup`；**请把这个密钥另存到你的密码管理器**，否则换机后无法解密。

## 6. Oura
没有找到你账户可用的 Oura 官方 MCP 接入入口。如果以后 ChatGPT 里出现 Oura 官方 app 并且你启用了它：
- 本系统不会保存 Oura 数据；但同一对话里如果同时开着本系统的**可写**连接，服务端无法判断你粘贴/转述的内容是否来自 Oura。
- 因此请另建第二个只读连接（服务端不暴露任何写工具）：再建一个 tunnel，运行 `ops/tunnel.sh init tunnel_另一个ID readonly && ops/tunnel.sh install readonly`，在 ChatGPT 建 app "Personal Health Context (read-only)"。**凡是用到 Oura 的对话只启用这个只读 app**；需要记录新事实时在另一个不含 Oura 的对话里说。
