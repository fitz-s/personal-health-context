# 部署、隐私、维护和恢复

## 1. 路径和权限

专用仓库与data root分离。真实数据默认 `~/Library/Application Support/PersonalHealthContext`，目录0700、DB/objects/config秘密0600。主机FileVault启用状态由本地doctor报告；未启用不能说“磁盘已加密”。生产与synthetic不同profile和目录，工具启动时清晰绑定，避免把测试记录写进真实库。

API key、设备token、tunnel控制平面key存Keychain，进程启动时读取；不得出现在.plist、git、模型上下文、截图或命令历史。密钥值不入日志；只记录凭据引用名/到期状态。shell tracing必须关闭。worker外部请求默认按 allowlist；MCP外的原始数据库只给用户级文件权限，不把它暴露为HTTP下载。

## 2. 安装/启动/关闭

本地agent生成并实际验证 `ops/install.sh`、`ops/uninstall.sh`、`ops/status.sh`、`ops/backup.sh`、`ops/restore.sh`、`ops/rollback.sh`。包内.example只能作为模板，不能把未替换变量的脚本当安装成功。

launchd拆分：tunnel/stdIO MCP owner一个服务；worker一个定时或常驻服务；iPhone ingest按实际路由单独服务。路径绝对化，日志脱敏轮转；重启不能依赖激活venv的交互shell。卸载仅停止/删除该系统服务和程序，不默认删除个人数据或Keychain；彻底删除需要明确单独指令。

偏好 `proactivity=off`只关闭主动候选呈现，不删除记忆或阻断用户主动问答。全系统停止用status/install生成的launchctl命令；不要以杀所有python进程处理。

## 3. 数据来源和模型边界

local-first是档案控制权，不是云处理豁免。onboarding写明：哪些内容保存本地、哪些会送ChatGPT/API、哪些来源限制存储、哪些后台功能调用付费服务。研究查询尽量仅含一般问题，不把姓名、完整化验单、设备id等发给搜索引擎。模型工具读权限允许广泛调查，但仅限已经授权可访问的数据域。

来源文档/照片中的指令不能改系统规则、发请求、读秘密或改变source policy。对附件、API错误、外部研究网页做数据/指令隔离。禁止“为帮助排查把完整db发出去”。支持导出公开数据字典与机器可读JSONL/CSV以及原文件索引，用户可离开系统而不失去档案。

Oura限制不能只靠LLM标签执行。源import层过滤、会话读写隔离、日志/backup过滤、operator policy共同落实；若宿主无法提供足够隔离则缺功能也不违规。现场测试能证明连接，不需要把真实vendor响应存到交付证据里。

## 4. 备份与恢复

live DB不得直接放iCloud同步文件夹，也不得只copy主.sqlite文件漏WAL。使用SQLite online backup/受控一致快照，取得该snapshot引用的不可变objects，再计算manifest哈希；未完成快照不能标可恢复。

默认本地每日一致快照，7日+4周轮转；云端仅加密完成快照包，不同步live root。选用已有可信加密工具/系统方案，记录版本、密钥恢复方法、退出码；加密key不能和备份一起明文保存。普通iCloud同步成功本身不是完整性/可恢复证明。

原件不静默压缩、裁剪或重编码来省空间。对象去重依hash；解析文本/缩略图/索引可重建。GC先查所有active/历史引用及未结束backup；清理未引用临时对象需保守宽限，不从磁盘占用反推“可以删原始资料”。

恢复演练必须实际：新空目录→验证manifest与每个hash→SQLite integrity/FK检查→恢复DB和objects→新进程读取文字/原件/开放问题/偏好→对照计数与hash。不要恢复覆盖live root；通过后再停写、切换、保留旧root直到验收。加密云端路线也要实际下载/解密/验hash后恢复一次，不只是本地明文测试。

## 5. 故障策略

| 故障 | 行为 | 恢复证据 |
|---|---|---|
| Mac离线/tunnel断 | ChatGPT明确无法保存/读取，不伪造；手机outbox保留 | 恢复连接同key重试，只有一条记录 |
| iPhone锁屏/拒权限 | 标coverage未知/延迟，不解读为缺运动/异常睡眠 | 解锁/授权后增量，权限状态不伪报 |
| API超时/限流/费用上限 | 前台记忆核心继续可用；后台任务退避、不日报 | pending可续跑，未完成不推进分析水位 |
| SQLite busy | 有界退避，同idempotency key重试 | 无部分写、明确可重试错误 |
| 磁盘不足/只读 | 先保原数据，拒绝新写并给明确失败 | 释放可重建缓存后原件hash与DB完整 |
| 文件部分下载/解析失败 | 不承认未保存字节；已保存原件单独成功 | 重试后同hash，抽取状态可更新 |
| 旧分析依赖被删/修订 | stale标记，pending失效 | 重算引用当前revision |
| 凭据失效 | 去重系统错误/最小重连动作，不循环骚扰 | 新token通过最小探针，无敏感日志 |

## 6. 可观测性而不是另一块dashboard

CLI status输出组件状态、最近成功/尝试时间、coverage摘要、queue长度、模型调用计数/预算、backup时间、code/config版本；不输出健康正文。日志用trace/request/job id和错误码，截去signed URL/authorization/body。保留本地诊断的必要时长；导出诊断默认只含元数据。

用户看到的普通对话只在有相关性时说明数据不完整，别每次粘贴长系统健康报告。Oura不可用与Apple未同步分别陈述，不用“所有数据已经更新”。

## 7. 回滚

应用版本与DB schema版本分别记录。部署前快照；兼容迁移优先。新版本失败先停新服务，保留证据、恢复旧程序与配置；若schema不可回退，则还原到新root后切换，明确部署期间新记录如何合并/导出，不能直接丢弃。不把“git checkout”当数据库回滚。
