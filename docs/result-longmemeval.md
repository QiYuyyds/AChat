# LongMemEval 数据集测试结果


两者大框架相同：**喂对话 → 提问 → LLM 裁判**，但细节不同：

||LongMemEval|BEAM|
|---|---|---|
|数据规模|500 个 item，每个 item 1 个问题|几十个 case，每个 case 十几个问题|
|裁判方式|二选一（对/错）|rubric 评分细则（0/0.5/1.0）|
|auto_dream|关闭|关闭（配置里直接注释掉）|
|时间过滤|有（过滤未来会话）|无|
|问题类型|6 种|10 种|

---
1. Agentic Answer（智能体回答）

Agent 自主决定搜几次、搜什么关键词，用 ReAct 模式（最多 10 轮迭代），基于搜到的记忆卡片回答。启动了 auto_memory

2. Prompted-based Answer（提示式回答）

不做任何自主决策，直接拿原始问题搜一次记忆库，固定召回 N 个记忆片段（早期 10 个，最终版 15 个），塞给 LLM 回答。启动了 auto_memory

3. Golden Session（黄金会话）

**跳过整个记忆系统**意思就是不进行消息记录的塞入，直接拿数据集标注的"含答案的原始对话"喂给 LLM。golden session 是 haystack_sessions 的子集——就是所有会话中真正跟答案相关的那几段。考的是"信息直接给你，你能不能读懂"。

4. Golden Session + Time Filter（黄金会话 + 时间过滤）

在 Golden Session 基础上，把时间晚于问题时间的 golden session 过滤掉，只用"问题发生之前"的对话来回答，防止用未来信息作弊。过滤掉了 75 个 session，44 个问题受影响，20 个问题 golden session 全被过滤没了。

5. Recall@K（检索召回率）

关闭 auto_memory，直接用原始问题搜原始 session，看搜回来的 top-K 结果里有没有命中标准答案所在的 session。**不回答问题，只测检索引擎本身的能力**。

## cleaned-s

**basic settings**

1. 使用修改后的auto-memory prompt，关闭auto-dream机制
2. reme-memory中的全部session的时间一定早于question的时间

**results **

最终groundtruth

### agentic + prompted（最终GT，2026-07-16）


| Category                  | Total   | Agentic             | Prompted limit=15   |
| ------------------------- | ------- | ------------------- | ------------------- |
| single-session-assistant  | 56      | 56/56 (100.0%)      | 54/56 (96.4%)       |
| single-session-user       | 70      | 64/70 (91.4%)       | 62/70 (88.6%)       |
| knowledge-update          | 78      | 72/78 (92.3%)       | 67/78 (85.9%)       |
| temporal-reasoning        | 133     | 118/133 (88.7%)     | 117/133 (88.0%)     |
| multi-session             | 133     | 110/133 (82.7%)     | 101/133 (75.9%)     |
| single-session-preference | 30      | 20/30 (66.7%)       | 10/30 (33.3%)       |
| **Overall**               | **500** | **440/500 (88.0%)** | **411/500 (82.2%)** |

Prompted token 消耗：总 input 13,111,421 (平均 26,275/题)，总 output 313,370 (平均 628/题)。
平均 sessions_ingested: 44.8，dreams_triggered: 0。

