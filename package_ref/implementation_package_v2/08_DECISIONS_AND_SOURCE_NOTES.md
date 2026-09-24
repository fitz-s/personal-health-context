# 决策账本与官方来源核实

核实日期：2026-09-23。下面是工程选型和当日官方页面事实，不是永久产品能力担保。源码dossier全部保留在source_dossier；原始02优先。ZIP未被文件检索索引，因此已从附件原字节解压并阅读全部文件，没有借另一个同名文档替代。

## 已替用户决定的工程问题

| 问题 | 默认决定 | 为什么/更改触发条件 |
|---|---|---|
| 是否再造UI | 不造dashboard；ChatGPT一个入口 | helper仅同步/授权；原入口不可用才显式报告缺口 |
| 数据库 | SQLite + 原件objects +中文搜索 | 单人<约100GB；真实查询容量瓶颈才改索引/存储 |
| 向量库 | 暂不引入 | 先测SQL/全文/时间/原件读取，不能制造源限制embedding |
| 模型 | 一个强模型政策，前台+后台执行入口 | 不用弱planner裁证据；实际API模型经probe/eval锁定 |
| 端口/目录 | Mac专用路径、loopback、私有tunnel | 不把Mac健康库公开到互联网 |
| Apple | XML历史+原生iPhone增量helper | 手动export不能冒充持续sync |
| Oura | 官方允许的即时MCP，默认不本地镜像 | 条款限制需正式授权/规则变化才能解除 |
| 主动行为 | local outbox，少量、可silence；默认shadow | 不依赖不存在的任意ChatGPT线程push能力 |
| 备份 | 一致快照→加密→可选iCloud | live WAL文件不直接cloud sync；restore必须测 |
| 额外vendor/writeback | 不实现 | 先证实端到端价值；保留source抽象即可 |
| 普通录入纠错 | LLM理解+一次修订 | 不围绕一粒胶囊设计复杂审批链 |

## 官方来源（仅少量关键事实的摘要）

### S1 — ChatGPT developer mode help
https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt-beta

当日帮助页描述完整读写适用于部分工作区计划，并将Pro描述为read/fetch；web及模式限制也需注意。与S2不一致，不能据此单独断言用户账户权限。

### S2 — Developer mode guide
https://developers.openai.com/api/docs/guides/developer-mode

当日开发者guide描述web developer mode完整读写，列Pro/Plus及工作区计划。**S1/S2冲突的处理是实际账户写读探针，不是擅自要求升级或选择喜欢的一页。**

### S3 — Secure MCP Tunnel
https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

提供本地MCP私有连接、tunnel-client、stdio/local HTTP、账户权限、doctor、ChatGPT和Responses接入说明。实施时使用当时官方二进制与真实权限，示例命令再核help；不假设任何ChatGPT用户天然有平台tunnel权限。

### S4 — Plugins/App tool reference：fileParams
https://developers.openai.com/plugins/reference

当前reference定义top-level `openai/fileParams`，文件对象声明download_url/file_id/mime_type/file_name，前两项required。临时URL不等于原件存档。客户端实际支持需要用字节hash探针证明。

### S5 — Apple导出与HealthKit
https://support.apple.com/guide/iphone/share-your-health-data-iph5ede58c3d/ios
https://developer.apple.com/documentation/healthkit
https://developer.apple.com/documentation/healthkit/executing-anchored-object-queries
https://developer.apple.com/documentation/healthkit/hkhealthstore/enablebackgrounddelivery(for:frequency:withcompletion:)
https://developer.apple.com/videos/play/wwdc2020/10664/

Apple用户指南说明Health全部数据可导出XML。部分当前开发者页面为JS渲染，本环境未取得完整API正文；因此HealthKit签名/availability/entitlement必须由本地agent在当前Xcode官方文档与真机验证，**此包不声称Swift已编译或背景交付时效已证明**。

### S6 — Oura API协议：实现阻断项
https://cloud.ouraring.com/legal/api-agreement

重点复核3(m)、4(d)、4(a)及适用定义：LLM使用渠道、MCP存储/缓存、训练/eval/持久表示限制。默认方案不走REST入LLM、不存官方MCP输出或其受限衍生内容；镜像到Apple不自动移除原来源限制。条款提到官方MCP，但本次未核实该用户可用的endpoint/开通入口。自用/同意不是自动豁免；对法律适用有争议时先停受影响导入而不是自行宣告获准。

### S7 — API数据控制
https://developers.openai.com/api/docs/guides/your-data

API训练默认与保留/监控是不同问题；`store=false`不是零云留存保证。文件有独立生命周期。真实部署要解释数据处理边界并按配置核对。

### S8 — Responses文件输入
https://developers.openai.com/api/docs/guides/file-inputs

提供file_data/file_id/file_url等输入方式。Mac上的路径不会自动成为模型可读文件，需受控传输；解析结果不替代本地原件。

### S9 — ChatGPT Tasks
https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt

任务与客户端、GPTs/voice、触发能力有具体限制；不能假设任意后台进程可向旧ChatGPT线程推消息。默认采用local outbox，下次会话读取。

### S10 — 官方MCP实现
https://modelcontextprotocol.io/docs/develop/build-server
https://github.com/modelcontextprotocol/python-sdk

用官方SDK实现transport/lifecycle，锁现场验证版本。此环境未安装mcp SDK且没有可用容器外网，因此没有伪造SDK/隧道运行测试；本包参考基础刻意只依赖标准库。

## 需求与平台限制的差异账本

原始dossier要求Apple与Oura共同构成长期个人context；可自由持久化的Apple/用户资料可实现，Oura默认须限制为官方允许即时访问。单对话可写app与受限Oura读混合还存在跨app来源隔离问题。本包没有偷偷删除这个冲突，而将其列为本地release的显式缺口/授权依赖。

原始希望后台少量有用互动；主路径outbox在下次对话展示，主动push只有平台/用户批准真实可用才启用。原始自然附件意图保留，不能以“文件名已存”假装原件已入库。原始“iCloud足够备份”保留为加密完成快照，不是live DB同步。
