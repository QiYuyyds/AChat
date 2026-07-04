## ADDED Requirements

### Requirement: ProfileSource SHALL 对 preference keys 排序

`ProfileSource.fetch()` 遍历 preference snapshot 时，MUST 对 keys 做 `sorted()` 后再构建 ContextItem 列表，确保渲染输出的字符序列跨 run 稳定。

#### Scenario: 多 preference keys 按字母序排列
- **WHEN** preference 表有 keys: "字体"、"姓名"、"喜好"、"视觉风格"
- **THEN** ProfileSource 返回的 ContextItem 顺序为: "喜好"、"字体"、"姓名"、"视觉风格"（sorted 后的顺序）

#### Scenario: 同一组 preference 跨 run 输出一致
- **WHEN** 两次不同的 conversation run 读取相同的 preference 数据
- **THEN** 两次生成的 static prompt 字节序列完全相同（cache HIT）

### Requirement: `_trim_by_budget` SHALL 超预算直接丢弃

`_trim_by_budget(items, budget)` MUST 在累计字符数超过 budget 时，丢弃当前及后续 items。MUST NOT 强制保留至少 1 条。MUST NOT 截断当前 item 使其恰好填满预算。

#### Scenario: 所有 items 都在预算内
- **WHEN** items 总字符数为 300，budget 为 500
- **THEN** 返回全部 items（不裁剪）

#### Scenario: 第二条超出预算
- **WHEN** items[0] 长 200 字符，items[1] 长 400 字符，budget 为 500
- **THEN** 返回 items[:1]（只保留第一条，丢弃第二条）

#### Scenario: 第一条就超出预算
- **WHEN** items[0] 长 600 字符，budget 为 500
- **THEN** 返回空列表 `[]`（不强制保留，不截断）
