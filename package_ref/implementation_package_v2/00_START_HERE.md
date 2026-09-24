# Personal Health Context — end-to-end implementation package v2

**交接日期：2026-09-23｜产品语言：中文｜执行对象：有本地代码、终端和浏览器权限的 coding agent。**

本包把原始 dossier 收敛为确定的实施决策、可执行工作序列、接口契约、离线参考代码、测试、评估集、接入和运维验收。不是新的头脑风暴，也不把本地账户尚未接通的功能冒充已经上线。

## 最短使用方法

把整个文件夹放到本地 coding agent 可读的位置，给它 `01_LOCAL_AGENT_EXECUTION_PROMPT.md` 全文。让它在新的 `~/personal-health-context` 仓库执行，或沿用你明确指定的专用仓库。正常使用界面仍然只有 ChatGPT；iPhone helper 只承担首次授权和后台同步。

本地 agent 应连续完成 P0–P8，并在遇到问题时先验证、修复、替代和继续独立工作，不把工程默认值退回用户。只有账户授权、设备签名、明确付费/隐私决定这些无法代办的事项才可集中请求一次操作。

```sh
cd /path/to/personal_health_implementation_package_v2
python3 scripts/verify.py
PYTHONPATH=reference_core python3 -m phctx demo
```

以上仅运行合成数据，不安装依赖、不访问网络、不启动后台服务。Python 3.11+；此次运行环境见 `evidence/offline_verification.json`。输出报告在 `evidence/`。

## 文件导航

| 文件 | 用途 |
|---|---|
| `01_LOCAL_AGENT_EXECUTION_PROMPT.md` | 直接交给本地执行的完整指令，含失败恢复、验收和停止边界 |
| `02_ARCHITECTURE_AND_FLOWS.md` | 架构决定、A–G 完整用户流程、模型分工和数据边界 |
| `03_BUILD_SPEC.md` | 数据、事务、工具、上下文、后台机制、工程顺序 |
| `04_INTEGRATIONS.md` | ChatGPT/MCP/Tunnel、真实附件、Apple、Oura、后台模型接入 |
| `05_TEST_EVAL_AND_TUNING.md` | 单元/集成/真实会话测试、原始 A–L 验收、调参与 shadow 要求 |
| `06_OPERATIONS_AND_SECURITY.md` | 部署、隐私、备份恢复、可观测性、维护与回滚 |
| `07_FINAL_DELIVERY_CONTRACT.md` | 本地 agent 最终必须交付的东西、证据和状态规则 |
| `08_DECISIONS_AND_SOURCE_NOTES.md` | 原始问题的确定答复、平台核实结果及官方来源 |
| `contracts/`、`prompts/` | 工具、文件参数、同步、候选洞见、模型行为契约 |
| `reference_core/`、`tests/`、`scripts/` | 已执行验证的离线基础，不是已完成的生产连接器 |
| `evals/` | 完全合成的行为测试，不能将样例文件存在当成模型通过 |
| `ios/`、`ops/` | 本地必须实现/构建的真机 helper 规范与部署模板 |
| `source_dossier/` | 输入 dossier 原文，全部保留；其中 02 为权威产品合同 |
| `evidence/` | 本次真实测试结果、明确 NOT_RUN 的现场能力矩阵 |
| `IMPLEMENTATION_PACKAGE.md` | 主要文档合订本，适合一次性阅读 |

## 本包的确切状态

- 已完成：产品到工程的决策收敛；本地执行合同；纯标准库持久化基础；合成单元测试及复跑脚本；测试/评估/接入/交付标准。
- 未在此环境执行：Mac 部署、ChatGPT 写入与文件传输实测、iPhone HealthKit 编译和真机授权、Oura 官方 MCP 实际连接、付费 API 模型质量评测、真实多日静默运行。
- 这些未执行项不是留白：其操作、断言、证据和替代方案已给定，本地 agent 必须补齐。`evidence/live_capabilities.template.json` 不是通过报告。
- Oura 原始构想存在外部条款限制。默认不实现 Oura REST → LLM → 永久归档。合规的官方 MCP 即时查询与本地长期存储必须分开；这一差异须对最终用户显式披露。

**禁止把 `OFFLINE_REFERENCE_VERIFIED` 换成产品 `READY`。只有真实设备、账户、行为评估和运维门槛都满足，最终产品才可标为 READY。**
