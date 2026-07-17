# Proposal: Add VIP Login Shortcut

## Why

AChat 将部署到服务器供面试官使用。现有登录页要求输入邮箱和密码，不便于快速进入。需要为现有默认账号 `admin@local` 提供一个只输入密码的快捷入口，同时继续复用现有认证、JWT 和权限体系。

## What Changes

- 在登录页右下角增加“VIP 登录”按钮，保持原登录表单和页面视觉不变
- 点击按钮后打开仅包含密码输入框的 VIP 登录弹窗
- 新增 `POST /api/auth/vip-login`，使用服务器配置的默认账号邮箱完成认证
- 新增 `VIP_LOGIN_ENABLED` 配置，用于同时控制前端入口和后端接口
- 初始账号继续使用 `DEFAULT_USER_EMAIL=admin@local` 与 `DEFAULT_USER_PASSWORD=123456`
- 新增服务器端密码重置脚本；仅服务器维护者可以修改 VIP 账号密码
- VIP 登录成功后签发与普通登录完全相同的 JWT，不新增角色、权限或数据库字段

## Capabilities

### Modified Capabilities

- `user-auth`: 增加默认账号的密码快捷登录接口及服务器端密码重置约束
- `frontend`: 增加登录页右下角 VIP 入口、密码弹窗和相应状态处理

## Impact

- **前端**：`src/app/login/page.tsx`、`src/stores/auth-store.ts`
- **后端**：`backend/app/api/auth.py`、`backend/app/auth/service.py`、`backend/app/config.py`
- **运维脚本**：新增默认账号密码重置脚本
- **配置**：`backend/.env.example` 增加 `VIP_LOGIN_ENABLED`
- **数据库**：无表结构变更
- **权限**：无权限模型变更

