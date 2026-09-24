# 数据、工具与实现规范

## 1. 仓库与运行入口

推荐生产仓库：
```text
src/phctx/{db,migrations,objects,records,retrieval,tools,mcp_server,importers,worker,model,policy,cli}
tests/{unit,integration,security,e2e}
ios/HealthSyncHelper/
contracts/ prompts/ evals/ ops/ delivery/
pyproject.toml dependency-lock-file config.example.toml README.md AGENTS.md
```

模块可以按复杂度合并，不要求机械创建空文件。`reference_core/phctx` 是可复用的底层实现：事务/回执、记录、原件、观测、受限SQL、候选、备份恢复。生产CLI应提供 `doctor`、`mcp`、`import-apple`、`ingest-server`、`worker --once`、`worker`、`backup`、`restore --new-root`、`export`、`verify`；命令导入不得启动服务。后台 worker 以 launchd 管理，不允许 shell 暗留进程。

## 2. 最小持久模型

参考 `reference_core/phctx/schema.sql`。生产迁移在此基础上增补以下字段/表，避免先设计几十类健康实体。

| 实体 | 核心内容 | 不变量 |
|---|---|---|
| sources | 来源ID、供应商/设备、policy、连接状态、成功/样本/尝试时间、coverage、cursor | 来源政策只能可信 importer/operator修改，不是LLM工具 |
| records | event/routine/question/analysis/note/attachment、原话、结构化payload、事件时间、时区、时间精度、source、supersedes | 改正新增修订，不静默覆盖来源；active视图排除旧版本 |
| observations | provider/native UUID、metric、开始/结束、值/单位、原始字段、源设备、删除标志 | 同UUID幂等；不同设备同值不是天然重复 |
| objects | SHA256、长度、MIME、原文件名、不可变路径 | 数据库指向原件前，文件已经写完、fsync并校验 |
| evidence_links | analysis/question到record/observation/query snapshot/object page的引用 | 含版本或digest，支持失效；摘要可追原资料 |
| receipts | request_id、规范请求hash、返回值、提交时间 | 同key同body重复返回原结果；同key不同body冲突 |
| questions | 用record payload承载状态、关注结果、窗口、已有证据、重访条件、测量缺口 | 无需另建复杂问答图谱；closed/superseded不可继续自动唤醒 |
| insights | why_now、变化、未知、下一步、来源版本、指纹、状态 | silence不等于生成一条“无事”洞见；重复/失效内容不发 |
| preferences | proactivity、timezone及少数明确沟通偏好 | fresh chat及worker共用；关闭后现有pending也不展示 |
| jobs | id、type、dedupe_key、state、lease owner/expiry、attempt、next_run、watermark、last_error_code | 可恢复、至少一次执行 + 副作用幂等；不宣称网络 exactly-once |
| sync_sessions | installation_id、stream、batch/sequence、cursor、ack | 手机增量重放和顺序恢复，不依赖模型记住状态 |

时间存 UTC + 原时区；原始输入同时保留，以免“今晚”被接收日期覆盖。`occurred_at`若由模型推断在payload标记 approximation；统计窗口使用半开区间[start,end)，DST按本地日划分再转UTC。临床/设备单位先保留原字段，再用明确转换表；无法转换就分组，不由模型心算混合。

**不要保存**：完整私有思考链、无意义每轮聊天复制、永久签名URL、密钥、原始敏感调试日志、每个无变化tick的长分析、真实vendor限制数据、没有决策价值的无限“可能有关”猜测。保存可供用户核查的分析摘要与理由，不保存模型隐含推理轨迹。

## 3. 写入与对象事务

所有模型写操作提供 request_id；服务端用规范化参数计算hash。在 `BEGIN IMMEDIATE` 中检查/插入结果与回执，COMMIT后返回成功。工具连接断开但事务可能已提交时以同key查询/重试，不能生造第二条记录。原件以hash命名，临时写入+fsync+atomic rename+目录fsync，然后写数据库引用；失败产生未引用对象可延后GC，不能反向产生不存在的“已保存”附件。

首次连接/schema migration在单进程控制下；每请求独立连接、WAL、busy_timeout、foreign_keys、明确事务。迁移前快照；禁止自动降级。生产补充 schema_version/migration ledger、受控升级锁、磁盘预检；失败不继续写一半表。不要对iCloud中的 live DB 开 WAL。

## 4. 广泛工具集与权限

`contracts/tools.json` 是生产接口规范，不是声称已连接的工具清单。

- `context_bootstrap`：偏好、来源状态、少量当前routine/open questions/变化索引、pending洞见、可进一步读取的方法。
- `context_search`：自然关键词与类型/时间过滤、稳定游标、中文substring fallback、明确是否截断。可实现 FTS5 trigram；在中文/混合语料未验证前不能只用英文分词器。
- `context_read`：读真实ID对应的完整记录与来源、修订状态；不得从ID猜内容。
- `context_query`：供强模型自行拓展的受限只读SQL/绑定参数，公开数据字典；结果带完整窗口、source、unit、coverage建议和查询hash。
- `context_read_original`：原件/页/文字片段或经宿主允许的文件返回。敏感文件不能永久公开URL；页号/抽取状态清楚。
- `context_capture`、`context_revise`：自然事实、routine、问题、重要分析的有限写入口；不暴露任意SQL写入、任意路径读写或shell。
- `context_capture_file`：真正文件对象→受控原字节下载→commit；不是让LLM把文件名插进数据库。
- `context_set_preference`：少量沟通偏好写入；`context_ack_insight`只处理呈现/忽略状态。

生产 wrapper 调 Store 之前校验契约、身份、来源策略、大小和时间；工具metadata必须与副作用一致。源注册、批量导入、备份/恢复/删除、远程通知、key操作只在 operator/sync通道，不作为普通模型工具。不要把 readOnlyHint 当权限系统。

参考只读查询使用 read-only连接 + query_only + SQLite authorizer + function/table白名单 +禁扩展 +执行时限/长度/深度/行数/输出字节上限。生产加客户端取消、row/byte分页、错误分类；禁止 PRAGMA、ATTACH、write CTE、secret tables、file读函数、加载扩展。模型不必预设固定窄SQL模板，但不能给整个文件系统权限。

## 5. 上下文加载与证据账本

第一次 capture/serious question 调用 bootstrap。已有会话上下文不代表durable facts最新，记录变化watermark后增量刷新。bootstrap是地图，不是最终过滤；模型可连续搜索、按时间读表、回看原文件、补查不同来源。说明已检索范围和不可用来源；空记录/最近样本时间不等于完整覆盖。

分析证据账本至少记录 source id、record/observation revision、object/page、time window、query hash、fetched_at、policy、assumption。模型生成的“结论”只能在所用证据范围内。保存分析时记录模型/提示版本和高层理由摘要；新数据修订、删除或单位映射改变后将关联分析标为stale，而不是永久正确。

事件输入先识别用户实际说了什么；只有影响结果的高风险或不可逆歧义才追问。查无记录时承认缺失，不能把常识/历史推断冒充该用户事实。

## 6. 后台 worker 算法

1. 获取单worker lease；一轮读 sources状态、全局变更序号、open questions、preferences；不把源离线当健康变化。
2. 合并新观测/附件抽取/用户修订；按问题关注域和时间选择调查候选，但主模型仍能扩大阅读范围。定期低频全域回看用于未知问题，不只订阅已有问题。
3. 检查调用预算和检查水位。重复同一证据版本直接结束；pending问题不因为API失败被标完成。
4. 同一强模型使用后台prompt调查，返回candidate JSON；必要的统计由确定性代码生成，检验可比来源/缺失/窗口，外部研究只发去标识化查询。
5. 独立gate验证证据存在且当前、来源可持久化、非重复、why_now/决策价值、注意力预算；不相信模型自报0.99置信度就通知。
6. `silence`仅推进检查水位和无正文状态；surface进入outbox，等下次前台呈现。偏好变化/证据修订即刻重新判定pending。
7. 任务和副作用回执提交后释放lease。异常按类型退避并限流；连续失败出一次系统状态，不每日健康提示。后台不得自动下医学结论、改药或替用户预约/购买。

时间调度不是医疗监测。产品不得承诺急症检测或24小时安全预警。前台用户实际报告危险症状时按当前医疗安全规范处理，不套用低打扰机制来忽略即时求助。

## 7. 生产增补清单（参考基础没有冒充这些已实现）

MCP SDK/身份与会话处理；安全文件下载与受控解析；iPhone构建与端到端同步；可靠source provenance过滤；observation级证据版本失效；job lease/水位；模型Responses调用和tool loop；真实行为评测；稳定分页/中文索引/100GB量级容量验证；加密云快照；launchd/Keychain；导出/删除和日志审计。这些是P1–P7本地必须完成的有界工作，不是隐含“可选”项。
