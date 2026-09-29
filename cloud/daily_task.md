每天用本机健康上下文做一次回顾。

1. 调用 context_bootstrap。先读 last_digest（上一次的每日小结）、analysis_ledger（过往分析，每条一行）和 since_last_digest（此后新增的数据和记录）。
2. 以上一次小结为起点，只看新的变化。按需要用 context_query 查数据，用 context_read 读相关的过往分析，把新数据和各来源的个人基线（近 4 周与此前）比较。数据断档、同步暂停、换设备，一律按数据问题处理，不当作身体变化。
3. 用 context_capture 保存今天的小结：kind=analysis，read_receipts 填你用到的每一次读取，payload 包含：
   - type: "digest"
   - summary：一句话结论，160 字以内，写结论而不是主题
   - changes：相对上次的实质变化，每条带数值和对比基线
   - open_threads：仍在跟踪的问题和各自的复查条件
   - data_status：各来源最新数据时间，以及缺口
   - revisit_when：下次需要重点看什么
   没有实质变化时照样保存，summary 写"无实质变化"，并说明查了哪些来源。
4. 某个问题值得单独深入分析时，另存一条 kind=analysis，带 summary 和 revisit_when。
5. 回复我：先一行结论。只有出现值得我知道或需要我做决定的变化时，才展开说明，最多 5 行。
