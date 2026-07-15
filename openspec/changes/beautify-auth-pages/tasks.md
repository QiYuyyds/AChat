# Tasks: Beautify Auth Pages

## 1. 共享品牌面板组件

- [x] 1.1 创建 `src/components/auth-brand-panel.tsx`：左侧品牌展示面板，包含 `bg-gradient-to-br from-primary to-primary/85` 背景、`--warning` 暖光晕 overlay、品牌名 "AChat"（text-4xl font-bold text-primary-foreground）、副标题 "多 Agent 协作平台"、标语 "把多 Agent 协作做成 IM 群聊体验"
- [x] 1.2 在品牌面板底部添加几何装饰线条（用 `primary-foreground` 极低透明度 3-8% 的 CSS pattern）
- [x] 1.3 确保组件纯展示型、无 props、无状态，`'use client'` 不需要（无交互）

## 2. 改造登录页

- [x] 2.1 将 `src/app/login/page.tsx` 外层布局从单 Card 居中改为分屏 flex 布局（`flex`，左侧 `AuthBrandPanel` + 右侧表单区）
- [x] 2.2 右侧表单区背景：用内联 `style` 实现多层 `radial-gradient`（`var(--primary)` 3% + `var(--warning)` 2%），底色 `var(--background)`
- [x] 2.3 右侧卡片：改为 `bg-card/80 backdrop-blur-sm shadow-md` + `inset-hi` 高光（`style={{ boxShadow: 'var(--shadow-md), var(--inset-hi)' }}`）
- [x] 2.4 图标容器：从 `size-12 rounded-xl bg-primary/10` 改为 `size-14 rounded-full bg-primary/10 ring-1 ring-primary/20`，图标从 `size-6` 放大到 `size-7`
- [x] 2.5 按钮：添加 `bg-gradient-to-b from-primary to-primary/90 hover:brightness-110` 渐变效果
- [x] 2.6 确保移动端（< `lg`）左侧面板隐藏（`AuthBrandPanel` 加 `hidden lg:flex`），右侧全宽居中

## 3. 改造注册页

- [x] 3.1 将 `src/app/register/page.tsx` 外层布局同步改为分屏布局（复用 `AuthBrandPanel`）
- [x] 3.2 右侧表单区背景、卡片、图标、按钮样式与登录页保持一致
- [x] 3.3 注册关闭状态页（`!allowRegistration` 分支）同步适配分屏布局
- [x] 3.4 确保表单功能（提交、验证、错误提示）不受影响

## 4. 验证与收尾

- [x] 4.1 运行 `pnpm typecheck` 确保无类型错误
- [x] 4.2 运行 `pnpm lint` 确保无 lint 错误
- [x] 4.3 手动验证：light/dark 模式下登录页和注册页视觉效果
- [x] 4.4 手动验证：移动端窗口宽度下左侧面板隐藏、右侧表单正常显示
- [x] 4.5 确认无残留 `console.log` 或调试代码
