# Design: Beautify Auth Pages

## Context

AChat 登录页 (`src/app/login/page.tsx`) 和注册页 (`src/app/register/page.tsx`) 当前是一个标准 shadcn Card 居中在 `bg-background` 上的极简布局。项目在 `globals.css` 中定义了一套考究的 "2026 暖灰米 / Warm Greige" 主题色体系（陶土褐 `--primary`、暖琥珀 `--warning`、高光 `--inset-hi`、`--shadow-md`），但登录/注册页完全没有利用这些色彩层次。

两个页面结构高度相似（Card + 表单 + 切换链接），唯一差异是注册页多了 name 字段和注册关闭状态。

## Goals / Non-Goals

**Goals:**

- 登录/注册页改为分屏布局：左侧品牌展示面板 + 右侧表单区域
- 右侧表单使用毛玻璃浮卡效果（`backdrop-blur` + `shadow-md` + `inset-hi`）
- 左侧面板利用 `--primary` 渐变背景 + `--warning` 暖光晕装饰
- 右侧背景使用多层 `radial-gradient` mesh，以 `--primary` 和 `--warning` 极低透明度叠加
- 移动端（< `lg`）隐藏左侧面板，右侧全宽居中
- 登录/注册页共享一个 `AuthBrandPanel` 组件，减少重复
- 注册关闭状态页同步适配新布局

**Non-Goals:**

- 不修改后端认证逻辑或 API
- 不修改主题色变量定义（`globals.css` 的 `:root` / `.dark`）
- 不新增自定义 Logo SVG（继续使用 lucide `MessageSquare` 图标，但加大尺寸并配品牌名）
- 不引入新依赖
- 不修改 `AuthGate`、`auth-store` 等认证逻辑代码

## Decisions

### Decision 1: 抽取 `AuthBrandPanel` 组件而非各自内联

**选择**: 新建 `src/components/auth-brand-panel.tsx`，封装左侧品牌面板（背景渐变、光晕、品牌名、标语），login 和 register 页共享。

**理由**: 两个页面的左侧面板完全一致，抽组件避免重复。组件只含视觉展示，无状态/无交互，纯展示型。

**备选**: 在每个页面内联重复。放弃——维护成本高且容易不一致。

### Decision 2: 分屏比例 55:45，断点 `lg`

**选择**: 左侧 `w-[55%]`（或 `lg:w-3/5`），右侧 `w-[45%]`（或 `lg:w-2/5`），移动端 `lg:hidden` 左侧。

**理由**: 左侧是品牌展示，需要更多空间放置品牌名 + 标语 + 装饰；右侧表单字段少（最多 3 个 input），不需要太宽。`lg` 断点（1024px）以下隐藏左侧，保证平板/手机上表单有足够空间。

**备选**: 50:50 等分。放弃——左侧品牌区会显得拥挤，右侧表单区有大量留白。

### Decision 3: 右侧背景用内联 `style` 实现多层 radial-gradient

**选择**: 在右侧容器上用 `style={{ background: ... }}` 叠加多层 `radial-gradient`，使用 `var(--primary)` 和 `var(--warning)` 极低透明度（3-5%），底色 `var(--background)`。

**理由**: Tailwind v4 不直接支持多层 `radial-gradient` 的工具类，内联 style 是最简洁的方式。使用 CSS 变量确保主题切换（light/dark）时颜色自动跟随。

**备选**: 在 `globals.css` 中新增 `.auth-bg` 类。放弃——仅两处使用，不够三处重复阈值，不增加抽象。

### Decision 4: 卡片用 `bg-card/80 backdrop-blur-sm` + 已有 shadow 变量

**选择**: 表单卡片使用 `bg-card/80 backdrop-blur-sm shadow-md`，并叠加 `inset-hi`（`var(--inset-hi)`）高光。

**理由**: `backdrop-blur` 让卡片透过底层 mesh 渐变，形成毛玻璃质感。`bg-card/80` 半透明让背景纹理隐约可见。`shadow-md` 和 `inset-hi` 都是已定义的 CSS 变量，复用零成本。

### Decision 5: 图标容器放大为圆形 + ring

**选择**: 图标容器从 `size-12 rounded-xl` 改为 `size-14 rounded-full bg-primary/10 ring-1 ring-primary/20`，内部 `MessageSquare` 从 `size-6` 放大到 `size-7`。

**理由**: 圆形容器 + ring 边框在毛玻璃背景上更有「品牌徽标」感，而非「功能图标」感。

### Decision 6: 按钮渐变 + hover 微亮

**选择**: 登录/注册按钮添加 `bg-gradient-to-b from-primary to-primary/90`，hover 时 `brightness-110`。

**理由**: 纯色按钮在精心设计的背景中显得扁平，渐变按钮增加一丝立体感，但不夸张。

## Risks / Trade-offs

- **[Risk] `backdrop-blur` 在低端设备性能** → 该效果仅在静态登录页使用，不涉及滚动/动画，性能影响可忽略
- **[Risk] 移动端隐藏左侧面板后品牌感丢失** → 移动端右侧表单卡片仍使用 mesh 渐变背景 + 毛玻璃效果，保留视觉品质；品牌名/标语在卡片 header 中保留
- **[Trade-off] 内联 style vs CSS 类** → 仅两处使用，选择内联 style 避免过早抽象；如未来有第三处使用可提取为类

## Open Questions

（无——设计方案已在 explore 阶段与用户确认）
