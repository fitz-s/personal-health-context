# 本地实现最终交付合同

## 1. 最终交付物

在专用仓库 `delivery/` 输出：

| 文件/目录 | 必需内容 |
|---|---|
| `USER_HANDOVER.md` | 中文：一条启动方式、正常对话示例、已可用能力、关闭主动性、导出/备份/恢复、限制 |
| `release.json` | 按schema列P0–P8门槛、证据、代码版本/hash、schema、状态、明确缺口 |
| `capabilities.json` | 当前账户/client/model、实际read/write/file/fresh chat/mobile/tunnel/Apple/Oura/API状态 |
| `EXECUTION_LEDGER.md` | 实际尝试、命令、失败、修复、回归、替代、未完成原因 |
| `test-report/` | 工程测试结果、实际命令/退出码/时间、source revision |
| `eval-report/` | model/prompt/schema版本、开发/holdout分项分子分母、失败修复、无真实vendor数据 |
| `live-evidence/` | 合成ChatGPT写读/file hash、iPhone同步ACK元数据、source policy审计；不存真实健康正文 |
| `recovery-report/` | 备份恢复、重启、断网、缺空间/token故障的真实结果 |
| `source-policy.md` | 允许/不可保留数据，Oura限制与当前批准状态 |
| `KNOWN_LIMITATIONS.md` | 完整、具体，关联原始A–L；没有缺口才写none |
| `USER_ACTIONS.md` | 仅真正待用户操作的授权/设备/明确付费项；已完成则空 |
| `manifest.sha256` | 对代码/配置模板/证据的完整性索引，不包含生产数据 |

另外交付实际生产代码、依赖锁、工具schema、生产prompt、iOS Xcode工程和构建/安装记录、launchd与安装/卸载/回滚脚本、数据字典/导出格式。分发包禁止包含API key、设备token、live SQLite/病历/原图、真实vendor响应、未经脱敏的logs。用户个人数据仍在其授权本地root，不因为打包而被复制到repo。

## 2. 状态的严格定义

- `READY`：全部mandatory checks真实PASS，原始A–L中无未披露功能缺口；Apple/目标ChatGPT/Oura（按当前授权路线）真实验收；模型与运维过关；所声称主动功能已完成实际观察。条款造成的目标变更只有用户明确接受才能按接受后的范围READY，不能由agent自己删要求。
- `READY_WITH_GAPS`：核心存储/安全/本地恢复已PASS，未完成的集成/质量/观察项明确列为BLOCKED或NOT_RUN并附原因。不得把存在缺陷的安全测试fail“接受”为gap。
- `BLOCKED`：核心数据安全、权限、持久化或恢复失败/未验证，不能正常使用真实资料。
- `OFFLINE_REFERENCE_VERIFIED`：只用于本包基础测试，不是部署release状态。

`NOT_RUN`永远不是PASS；安装/编译/配置成功不等于产品可用；5分钟模拟不是7天shadow。没有官方Oura可用route时C/K不完全达标，即便其余部分可用。手机端若是用户目标而未通过，I存在缺口。

## 3. Release checks固定集合

`unit_regression, auth_and_policy, durable_capture, original_file_integrity, local_restore, chatgpt_read_write, chatgpt_files, fresh_conversation, apple_device_sync, oura_official_access, model_eval, sparse_proactivity, background_shadow, restart_recovery, encrypted_backup_restore, single_interface`。

前五项为core blockers。每项结构：status、evidence（delivery根目录下相对文件路径）、evidence_sha256、notes。所有PASS必须有可读取证据文件及匹配hash。文件存在只能证明提交了证据，不能自动证明测试真实性；还要审查报告内容与声称能力对应。

本包提供release gate用于防止遗漏/NOT_RUN混淆和路径伪造；它不是对自填报告的密码学“可信执行”证明。本地agent仍须产出真实日志和独立工具观察。

## 4. 最终用户展示

给用户直接可用的两三种示例：“今天 supplements the usual”“Dinner + 照片”“这两个月训练怎么样”“talk less”。说明保存回执如何对应durable context；正常情况下不让用户输入record ID或SQL。后台不稳定时说明前台哪些仍能用，不能用一段体系架构图替代操作交付。

尚未授权事项按确切平台入口/设备操作整理成一次性清单，附为什么必须用户本人操作与完成后可执行命令。绝不要求用户重述dossier中已有偏好。
