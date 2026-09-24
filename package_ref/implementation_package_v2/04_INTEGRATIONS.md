# 接入操作、能力探针与替代路径

平台事实核查日期：2026-09-23；官方来源编号见08。所有账户/设备能力按目标环境再实测，不将本文时点当永久API承诺。

## 1. ChatGPT：账户能力优先于文档推断

官方帮助页与开发者guide对Pro写入范围的描述不一致[S1,S2]。因此先在**实际使用的账户、workspace、客户端、模型模式**完成无敏感内容测试，而不是根据“Pro”名字宣布可写/不可写，也不先要求升级。

能力探针步骤：
1. 选择官方支持的developer mode/私有app配置入口；注册本地测试MCP，只含合成数据。
2. 读取bootstrap并验证唯一nonce；通过capture写一条nonce，再从服务端独立查询确认COMMIT。由ChatGPT说“记下了”不是证明。
3. 新对话重新读取该nonce。保存客户端版本/日期、工具receipt与关联trace id（无健康正文）。
4. 上传预制小文件；从Mac原对象独立计算SHA256，与测试fixture比较。测试图片、带原生文字PDF、普通文档各一份，不能只测文件名。
5. 语音转写作为文字capture测；原始语音实时模式/自定义app若不支持，明确不同能力，不混淆“转写可用”和“实时语音模式可调用工具”。
6. 目标日常包含手机端时独立测手机；web-only成功不代表mobile支持。所用模型模式也独立记录；deep research只读不等于普通会话可写。

结果保存为capabilities，FAIL/BLOCKED必须带真实错误与已尝试fallback。host强制写操作确认是平台行为；设计不增加自己的重复确认，但不能保证平台永远不提示。

## 2. Mac到ChatGPT：首选官方 Secure MCP Tunnel

官方文档支持私有本地MCP通过出站连接接入，不必先暴露Mac公网端口[S3]。先下载当前官方 tunnel-client 并记录版本、来源、校验；平台账户的tunnel权限和ChatGPT app权限分别验证。

示例仅依据官方命令形状；安装时运行help核实：
```sh
# 控制平面API key由Keychain注入此进程；不要写进项目、聊天、plist或shell history。
tunnel-client init --sample sample_mcp_stdio_local --profile phctx-local \
  --tunnel-id tunnel_... --mcp-command '/absolute/venv/bin/python -m phctx mcp'
tunnel-client doctor --profile phctx-local --explain
tunnel-client run --profile phctx-local
```

stdio stdout只允许MCP协议；诊断去stderr并脱敏。不要同时由两个launchd job分别启动同一个stdio server；tunnel作为owner启动它。若采用local HTTP transport则服务仅绑定loopback、鉴权且管理独立生命周期。

ChatGPT创建私有app时选择真实tunnel；Responses API路径按官方 `tunnel_id`配置，不能把tunnel URL随意放入server_url。现场服务端记录非敏感request trace并独立检查数据库。

替代：现有经过审计的OAuth保护HTTPS MCP；复用成熟认证组件，不现场自制OAuth。匿名健康 endpoint、把静态token拼进URL、公共临时隧道、把源限制伪装成readonly都不接受。任何新公开路由都需要明确安全验收。

## 3. 文件参数与真正的附件接入

按当前官方reference[S4]，MCP工具 `_meta["openai/fileParams"]` 列出顶层参数名。工具inputSchema中的file对象声明：
```json
{
  "type":"object",
  "properties":{
    "download_url":{"type":"string"},
    "file_id":{"type":"string"},
    "mime_type":{"type":"string"},
    "file_name":{"type":"string"}
  },
  "required":["download_url","file_id"],
  "additionalProperties":false
}
```

**四个属性要声明，只有前两个必需**；平台可能不提供mime/name。这是宿主传递的文件引用，不是任意用户字符串。URL是临时传输手段，原件持久化后删除临时传输信息；MIME/filename使用验证后的安全默认值，不能当路径。

安全下载处理器（本地必须实现）：
- 只接收已认证工具调用中的已声明file param；HTTPS；严格解析hostname/port/userinfo；依据实测官方文件域名配置精确allowlist，不能默认允许任意互联网域。
- DNS解析后拒绝loopback/private/link-local/metadata/multicast等非公网地址，连接锁定已验证IP并保留TLS hostname校验；禁止重定向或逐跳重新验证；防DNS rebinding和SSRF。
- 流式下载，限制25MiB交互文件、超时、读取长度与解压比例；MIME只作提示、验magic；压缩包不自动展开到任意路径。Apple大导出走单独受控本地import，不受交互25MiB限制但要有容量上限。
- 记录hash/长度、不记录签名URL或全正文；失败返回安全错误码；URL过期可请求同一附件重新发一次引用，不能在数据库里伪装成功。
- 对象落盘后进行文本/页抽取；解析库在无网络、资源受限子进程里；文档里的“忽略之前规则”视作资料内容，不作为agent指令。

若宿主无法传自然附件，使用当前官方同一对话内上传控件（feature-detect selectFiles/uploadFile等）。不做另一个资料管理dashboard。仅有本地Inbox可用时它是工程降级，**原始A/I验收仍留缺口**。

## 4. Apple：历史回填与持续同步分开交付

### 历史回填

Apple用户指南支持导出全部Health数据XML[S5]。本地agent实现 `import-apple /path/to/export.zip --dry-run`，安全列出文件/预计容量/来源计数，确认允许来源后流式解析。保留允许的观测原字段及导入manifest；导出包若混有受限vendor数据，不把整个原包复制进canonical或backup。对这种混合包只保存过滤后的允许记录、结构/计数和无敏感审计摘要，并保留用户原文件在其原位置。

ZIP预检路径穿越/symlink/压缩炸弹；XML使用安全解析器（正常Apple DTD不是自动恶意，但ENTITY/external fetch禁止）；streaming、批量事务、可恢复检查点。无nativeUUID时生成来源+类型+时间+单位+值+稳定metadata指纹，并明确完全相同真实记录可能无法区分。文件导入重复应幂等；“这次导出缺少之前记录”不能等同于删除。

### 持续增量

原生iPhone helper是默认路径，详见ios规范。按类型先授权，HKObserverQuery收到变化提示后执行HKAnchoredObjectQuery，采集add/deleted UUID。后台调度/锁屏读取是系统控制的best effort，不能承诺每分钟实时到达。授权拒绝/无数据不可直接推断用户健康数据为零。

每个安装和类型维持anchor、顺序batch、durable outbox。收到HealthKit页面后，把页面和next anchor一起保存到手机outbox；Mac事务ACK之前保留可重传batch。允许本地query anchor前进的条件是对应数据已持久入outbox；只有服务器ACK后可删outbox。进程崩溃在任一点均不能丢页。Mac按installation+stream+batch幂等，记录server cursor/coverage。多页有序发送避免旧页覆盖新值；anchor失效时有重建策略，保留人工事实。

只申请read必要类型；初始范围睡眠、活动/锻炼、心率/静息心率/HRV、用户已有体重/体成分测量及来源元数据。具体类型按本机SDK/设备可用性选择，未知类型保留为未来扩展不报错。不能用心率推算未测到的肌肉量。用户删样本要传tombstone，并使依赖分析失效。

### iPhone到Mac网络

Secure MCP Tunnel负责ChatGPT路径，**不是默认通用手机HTTP隧道**。手机独立ingest通道首选现有私有网络/LAN上的TLS+设备token；首次配对时绑定installation和来源。没有可靠外出路由时先本地网络上传，离线outbox持续保留，返回同网后同步；明确not live outside LAN。全时外网同步作为独立网络配置，由现有可信VPN/经鉴权TLS入口完成，不能为赶进度开裸端口。

## 5. Oura：官方即时读取，不进行未经许可的长期镜像

当前API协议在4(d)限制通过非官方MCP路径向LLM提供Oura数据，并在3(m)限制MCP数据缓存/存储；其他条款亦限制真实数据eval/embedding等用途[S6]。自用或用户同意不能自动推翻这些约束。

本地执行：查找**当前官方公开且账户可用**的MCP启用方式，不猜endpoint；验证鉴权与最小即时查询，按其规定处理响应/保留；无官方可用路线则将Oura标blocked，不用REST/抓取替代。条款提到官方MCP不证明该用户一定已有接入权限。

默认不向本地库、文件、备份、traces、模型评测集保存Oura的原值、完整响应或含其内容的衍生分析。Apple中的Oura镜像也不能因为中转就自动视作Apple原创；source metadata保留来源，受限样本在入库前丢弃并计数，不无限保存在“隔离区”。正式授权或后来明确许可规则才可改变source policy，且仅operator可做。

**跨app taint边界**：普通MCP服务无法仅凭任意自然文本证明它没有引用另一个app的Oura输出。不能把 `source=user` 当洗白工具。Oura辅助调查采用本地只读工具配置；持久化放到独立、不含Oura输入的写入会话/执行路径。若宿主无法切换会话权限，Oura集成暂不启用到日常可写app，最终报告缺口。用户手工输入自己的事实与复制vendor响应不是同一来源。

因此，原始“所有来源形成同一永久上下文”的Oura部分不是可以无条件实现的承诺。可完成的是 permitted local archive + 官方允许的即时vendor context。不能在最终A–L验收中悄悄将此限制当完全达标。

## 6. 后台模型及消息

默认使用已授权API的Responses能力，前台ChatGPT订阅是否涵盖API不作假设；明确独立凭据/可用预算。配置 `model_id`、可调用工具、最大循环/输出量、费用上限、timeout、retry。先用合成数据评测和成本探针再处理真实允许数据；不把capable模型静默换弱模型。

对允许的数据尽量最小必要输入；官方 `store=false`不是零留存保证，files有独立生命周期[S7,S8]。没有必要别把原图全量发送多次；但不要因节省token隐藏重要证据。文件留在本地可供未来读取；远端临时上传完成后按API实际规则清理，记录清理状态而不是笼统承诺删除。

后台生成的是local outbox，下一次对话读取。官方Tasks限制及当前插件兼容性须独立核实[S9]；不能假定任意Mac后台可插入已有ChatGPT线程。可选macOS通知只说“有一项值得回看的更新”，不带病名、数值或正文；用户没启用就不发送。基于普通cron发日报不属于本项目。
