# Proposal: Beautify Auth Pages

## Why

AChat 的登录/注册页当前是一个裸 Card 居中在纯色背景上，完全没有利用项目精心设计的 "2026 暖灰米 / Warm Greige" 主题色体系（陶土褐 primary、暖琥珀 warning、inset-hi 高光等）。作为用户进入产品的第一印象页面，视觉品质不足，缺乏品牌识别度。

## What Changes

- 登录页 (`src/app/login/page.tsx`) 改为 **分屏布局**：
  - 左侧品牌展示面板（`bg-primary` 渐变 + 暖光晕 + 品牌名 + 标语），移动端隐藏
  - 右侧表单区域（暖色 mesh 渐变背景 + 毛玻璃浮卡 `backdrop-blur` + `shadow-md` + `inset-hi`）
- 注册页 (`src/app/register/page.tsx`) 同步采用相同的分屏布局
- 注册关闭页面同步适配新布局
- 新增 `AuthBrandPanel` 可复用组件，供 login / register 共享左侧品牌面板
- 按钮、图标容器、表单间距等视觉细节优化
- 复用已有 CSS 变量（`--primary`、`--warning`、`--inset-hi`、`--shadow-md`），不引入新依赖

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: 登录/注册页视觉要求从「提供居中 Card 表单」升级为「分屏品牌布局 + 毛玻璃浮卡」，增加视觉设计层面的 spec 要求

## Impact

- **前端代码**：`src/app/login/page.tsx`、`src/app/register/page.tsx`、新增 `src/components/auth-brand-panel.tsx`
- **CSS 变量**：全部复用已有 `globals.css` 定义的 `--primary`、`--warning`、`--inset-hi`、`--shadow-md`，不修改主题色
- **依赖**：无新增依赖
- **后端**：无改动
- **API**：无改动
- **响应式**：移动端（< lg）隐藏左侧品牌面板，右侧表单全宽居中
