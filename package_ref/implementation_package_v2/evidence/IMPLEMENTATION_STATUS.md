# 本包实际完成状态

- 日期：2026-09-23；实际环境：Linux，Python 3.13.5，SQLite 3.46.1。不是用户的Mac。
- 完整读取并保留原始dossier。确定P0–P8执行流程、产品行为、架构、工具/同步/候选/交付契约、真机接入、评测、调参和运维标准。
- 可运行基础：SQLite事务和幂等回执、不可变原对象校验、跨进程读取、修订历史、来源限制、受限只读查询、观测upsert/delete、偏好、稀疏候选、备份/新目录恢复。
- 最终离线测试：76项；0失败、0错误、0跳过。10个生产工具契约结构检查；56个合成行为场景结构检查；真实执行合成capture→新实例读取→silence demo，exit code 0。
- 模型调用次数：0。行为场景尚未由实际模型执行；没有给出模型质量分数。
- 未执行真实集成：Mac安装、官方SDK/Tunnel、ChatGPT原生写读/附件、iOS编译与真机、Oura账户、加密iCloud恢复、多日shadow。
- 生产必须增补的部分见03第7节；不能将reference_core模块导入成功当成完整产品部署。

## 实际发现与修复

首轮60项基础测试通过。增加release gate及更多对抗测试后，一个合法的SQLite cardinality-only读取被authorizer误拒绝。复现发现SQLite为这种读取报告 `database=None, column=''`；不是查询试图越权。修正只允许公开白名单表的这一限定形状，保留secret-table、ATTACH、PRAGMA、扩展和递归等拒绝。随后全部76项通过。保留 `regression_before_fix.log` 和最终 `unittest.log`，不删除失败证据。

同时为查询加SQL表达式长度/深度及增量输出上限；为pending洞见加问题修订失效；恢复时验证referenced objects与外键。对应回归测试均包含在最终76项。

`offline_verification.json`是本次执行证据；`live_capabilities.template.json`和`release.template.json`仅为本地填写模板。最终产品release需本地独立补证。
