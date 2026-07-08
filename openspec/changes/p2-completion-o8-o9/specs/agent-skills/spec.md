# Spec Delta: Agent Skills

## MODIFIED Requirements

### Requirement: Skill 文件契约

一个 skill SHALL 是一个目录，根目录 MUST 含一个 `SKILL.md` 文件。`SKILL.md` MUST 以 YAML frontmatter 开头，且 frontmatter MUST 同时包含非空的 `name` 与 `description` 两个字段；frontmatter MAY 包含可选的 `trigger_keywords` 字段（字符串列表，最多 10 项），用于声明该 skill 的自动激活触发关键词。frontmatter 之后是 Markdown 正文。skill 目录 MAY 含任意附带文件（脚本、模板、参考文档），系统对附带文件不做内容解析，仅原样存储。

#### Scenario: 合法 skill 含 trigger_keywords

- **WHEN** 一个 `SKILL.md` 的 frontmatter 含非空 `name`、`description` 和 `trigger_keywords: ["python", "pytest"]`
- **THEN** 系统判定该 skill 合法并接受存储
- **AND** `SkillMeta` 的 `trigger_keywords` 字段包含 `["python", "pytest"]`

#### Scenario: 合法 skill 不含 trigger_keywords

- **WHEN** 一个 `SKILL.md` 的 frontmatter 含非空 `name` 和 `description`，但不含 `trigger_keywords`
- **THEN** 系统判定该 skill 合法并接受存储
- **AND** `SkillMeta` 的 `trigger_keywords` 字段为空列表

#### Scenario: trigger_keywords 超过 10 项

- **WHEN** `SKILL.md` 的 frontmatter 含 12 个 `trigger_keywords` 条目
- **THEN** 系统截取前 10 项，忽略多余的
- **AND** 日志记录截断警告

#### Scenario: trigger_keywords 格式非法

- **WHEN** `trigger_keywords` 的值不是字符串列表（如 `"python"` 单字符串或 `123` 数字）
- **THEN** 系统将 `trigger_keywords` 视为空列表，不拒绝 skill 存储
- **AND** 日志记录解析警告

### Requirement: Skill Registry

系统 SHALL 提供 skill registry，扫描 `<data_dir>/skills/` 下每个含合法 `SKILL.md` 的目录，解析其 frontmatter，输出 `(slug, name, description, trigger_keywords)` 列表。registry MUST 跳过不含合法 `SKILL.md` 的目录而非报错。`trigger_keywords` 为可选字段，缺失时默认为空列表。registry 是绑定选择与运行时注入的唯一 skill 来源。

#### Scenario: 列出含 trigger_keywords 的 skill

- **WHEN** `<data_dir>/skills/` 下有 1 个含 `trigger_keywords` 的 skill 和 1 个不含的 skill
- **THEN** registry 返回 2 个条目，每条含 slug、name、description、trigger_keywords（第二条的 trigger_keywords 为空列表）

#### Scenario: trigger_keywords 字段在 SkillMeta 中可用

- **WHEN** `list_skills()` 被调用
- **THEN** 返回的每个 `SkillMeta` 对象包含 `trigger_keywords: list[str]` 字段
- **AND** `skill_auto_activator` 可直接读取该字段构建规则表
