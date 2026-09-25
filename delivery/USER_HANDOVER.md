# Personal Health Context — 使用交接

## 一条启动方式
服务已安装并在运行（launchd）。查看状态：
```
~/personal-health-context/ops/status.sh
```
连上 ChatGPT 后，日常就是在 ChatGPT 里正常说话；无需打开任何其他界面。连接步骤见 `USER_ACTIONS.md` §1（需要你在 OpenAI 账户里建一个 tunnel 和 key，约 10 分钟）。

## 现在已经能用的
- 本地长期上下文库（SQLite + 原件对象库）：`~/Library/Application Support/PersonalHealthContext/production`，目录 0700、文件 0600，FileVault 已开启。
- MCP 服务（官方 Python SDK，stdio）：11 个工具——读取地图、搜索、读记录、受限只读 SQL、读原件/页、保存事实、修订、保存附件原件、偏好、确认候选洞见、按 request_id 查回执。
- 每次"保存"都有数据库提交后的回执；附件只有真实字节落盘并校验 SHA-256 后才算保存；抽取失败不影响原件。
- Apple Health 历史回填：`phctx import-apple export.zip`（不再过滤 Oura 条目——Oura 现在是自己的持久来源，见下）。
- Oura：通过 API v2 每小时自动同步（`phctx sync-oura`，launchd 常驻），生产库中约 25 万条观测；WHOOP 同理。
- 后台 worker：每 15 分钟检查变化；无相关新证据不调用模型；默认 shadow 模式、模型关闭。
- 每日一致快照备份（本地，7 天 + 4 周轮转）；加密 iCloud 备份已实测可用，需你开启。
- 在 Mac 上用 Codex CLI 也能直接对话使用同一套工具（评测就是这样跑的）。

## 正常对话示例（连上 ChatGPT 后）
- "今天 supplements 就 the usual。" → 按你当前 routine 记一次，回一句"记下了"。第一次没有 routine 时，只问缺的内容。
- 发一张照片 + "Dinner" → 原图保存并返回 hash；份量不确定就标不确定，不编热量。
- 发化验单 PDF + "帮我记下来" → 原件保存，按页抽取数值/单位/参考区间，每项带页码。
- "这两个月训练到底有没有效果？" → 自己去查训练、可比测试、身体测量、睡眠等；区分已知/未知；数据不够时说明哪种测量能回答。
- "少说一点" → 持久改为 quiet；"别主动提醒了" → off（已排队的也不再展示）。
新对话不需要旧聊天记录：模型会先调用 `context_bootstrap` 读取当前 routine、开放问题、偏好和来源状态。

## 关闭主动性
在对话里说"少说一点"或"别主动提醒了"即可。或关闭后台：`~/personal-health-context/ops/uninstall.sh`（只停服务，不删数据）。

## 取回数据
```
PYTHONPATH=~/personal-health-context/src ~/personal-health-context/.venv/bin/python -m phctx export ~/Desktop/phctx-export
```
得到 records.jsonl、observations.csv、originals/（原字节）、originals_index.json、extracted_pages.jsonl、DATA_DICTIONARY.md。

## 备份与恢复
- 立即备份：`~/personal-health-context/ops/backup.sh`（加 `--encrypt` 写加密包到 iCloud，需先在 config 设 `cloud_dir`）。
- 恢复（永远恢复到新目录，不覆盖现有数据）：`~/personal-health-context/ops/restore.sh <快照目录或 .phbk> <新目录>`，再停服务、把 config 的 `[app] root` 指向新目录、重新 `ops/install.sh`。旧目录保留。

## 回滚
- 程序回滚：`~/personal-health-context/ops/rollback.sh <git版本>`（不动数据）。数据库比旧程序新时，旧程序会拒绝打开；这时用升级前自动快照（`production/migrations/pre-v*.sqlite3`）或备份恢复到新目录。
- 卸载：`ops/uninstall.sh` 只移除服务。彻底删除数据是单独的手动操作：删除 `~/Library/Application Support/PersonalHealthContext`、`~/.config/phctx`，以及 Keychain 中 `phctx-*` 条目。

## 日志与状态（不含健康正文）
`~/Library/Logs/PersonalHealthContext/`；`ops/status.sh` 显示服务、最近 worker 运行、来源同步时间、待展示候选数量。

## 限制
见 `KNOWN_LIMITATIONS.md`（按原始 A–L 逐项）。最重要的三条：iPhone 持续同步未编译安装（本仓库自带的 helper 或开源 Life Dashboard Companion 二选一，都需要你用 Xcode 和自己的签名装到手机上）；ChatGPT 官方 Oura 接入对这个账号仍不可用（Oura 现在走自己的 API v2 同步，不受此影响）；后台主动发现在当前代码树上未重跑评测（模型调用默认关闭，见 USER_ACTIONS §4）。
