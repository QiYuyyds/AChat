## ADDED Requirements

### Requirement: 主色 SHALL 仅用于 CTA 与选中态

电光靛主色（`--primary` 及其映射类 `text-primary`/`bg-primary`/`ring-primary`）MUST 仅出现在主操作按钮（CTA）与「当前选中/active」状态。未选中的导航项、普通图标、置顶/装饰性元素 MUST NOT 使用主色，应使用 `muted-foreground` 或中性 token。目标是让彩色成为「指向注意力」的信号而非背景噪音。

#### Scenario: 未选中导航项为中性色
- **WHEN** 图标轨中某 mode 未被选中
- **THEN** 其图标使用 `muted-foreground` 或等价中性色
- **AND** 不使用 `text-primary`/`bg-primary`/`ring-primary`。

#### Scenario: 选中态使用主色锚定
- **WHEN** 某导航项或会话项为当前选中
- **THEN** 其 active 锚定（左色条/指示）使用主色
- **AND** 同屏的非选中同类项不带主色。

### Requirement: 层次 SHALL 优先依赖边框对比与间距节奏

桌面端 figure-ground 分隔 MUST 优先通过清晰细边框（`--border` 提供足够对比）与一致的间距节奏表达，而非依赖阴影堆叠。列表项、tab、header 的纵向内边距 MUST 给出可感知的呼吸空间，避免全局采用最紧凑值。

#### Scenario: 边框对比可独立分隔区块
- **WHEN** 相邻区块（图标轨/列表栏/主区）以边框分隔
- **THEN** `--border` 的对比足以在不依赖阴影的情况下区分区块边界。

#### Scenario: 列表项具备呼吸间距
- **WHEN** 渲染会话列表项
- **THEN** 其纵向内边距大于变更前的紧凑值（不再是 `py-1.5`/`py-2` 级别的最小值）。
