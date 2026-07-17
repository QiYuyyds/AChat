# Design: VIP Login Shortcut

## Context

AChat 已有邮箱密码登录、bcrypt 密码哈希、JWT、HttpOnly cookie、`token_version` 全局会话撤销，以及迁移时创建默认账号的机制。默认账号邮箱由 `DEFAULT_USER_EMAIL` 配置，当前约定为 `admin@local`。

本变更只简化该账号的登录入口。VIP 不是角色，也不代表新的权限等级。

## Goals / Non-Goals

**Goals:**

- 保持原登录界面不变，在页面右下角提供“VIP 登录”入口
- VIP 登录只要求输入密码，不向前端暴露默认账号邮箱配置
- 登录后复用现有用户、JWT、cookie 和权限逻辑
- 初始密码通过服务器 `.env` 配置为 `123456`
- 密码只能由服务器维护者通过脚本修改

**Non-Goals:**

- 不新增 VIP、管理员或 RBAC 字段
- 不新增用户表或修改数据库结构
- 不在前端内置、回传或自动填充密码
- 不限制 VIP 账号功能；其权限与普通账号一致
- 不提供页面内修改 VIP 密码的入口

## Decisions

### D1: 复用默认账号而非新增 VIP 用户类型

`POST /api/auth/vip-login` 根据 `DEFAULT_USER_EMAIL` 查询现有用户，默认值为 `admin@local`。认证成功后调用现有 token 生成和响应逻辑。这样不会产生两套身份体系，也不需要数据库迁移。

### D2: 使用独立密码登录接口

请求体仅包含：

```json
{"password": "123456"}
```

后端流程：

1. 检查 `VIP_LOGIN_ENABLED`；未启用时返回 404。
2. 使用 `DEFAULT_USER_EMAIL` 查询用户。
3. 使用现有 bcrypt 校验密码。
4. 失败统一返回 401 `Invalid credentials`。
5. 成功后返回与普通登录完全相同的用户资料、token 和 HttpOnly cookie。

前端永远不接收默认账号邮箱或服务器密码。

### D3: 入口由公开认证配置控制

公开认证配置增加 `vipLoginEnabled` 布尔值。登录页加载该配置，仅在值为 `true` 时显示右下角按钮。后端接口独立检查同一配置，避免仅隐藏 UI 而接口仍可调用。

### D4: 服务器端重置密码

新增脚本读取 `DEFAULT_USER_EMAIL` 和 `DEFAULT_USER_PASSWORD`：

- 拒绝空密码
- 查询默认账号
- 使用现有 bcrypt 工具更新 `password_hash`
- `token_version += 1`，使旧 token 全部失效
- 不打印明文密码

修改 `.env` 本身不会在应用重启时自动覆盖数据库密码；维护者必须显式执行脚本，避免意外重置。

### D5: 登录页保持原样

- 原邮箱密码表单不变
- “VIP 登录”按钮固定在页面右下角，并保留安全边距
- 弹窗标题为“VIP 登录”
- 弹窗仅包含密码输入框、取消按钮和登录按钮
- 不显示“体验空间”“演示账号”或“管理员”等额外文案
- 支持 Enter 提交、提交中禁用、通用错误提示和移动端布局

## Error Handling

- 功能关闭或默认账号不存在：接口不泄露内部配置；分别返回 404 或通用认证失败
- 密码错误：401，前端显示“密码错误”
- 网络错误：前端显示“登录失败，请稍后重试”
- 重复提交：提交期间禁用输入和按钮

## Security Notes

- 初始密码 `123456` 仅存在服务器 `.env`，数据库只保存 bcrypt 哈希
- 前端代码、认证配置响应和日志不得包含密码
- VIP 账号权限与普通账号一致，因此服务器维护者应在面试结束后关闭 `VIP_LOGIN_ENABLED` 或重置密码
- 密码重置必须递增 `token_version`

## Testing

- 后端测试：功能开关、正确密码、错误密码、账号不存在、JWT/cookie、token 失效
- 前端测试：按钮显隐、弹窗交互、成功跳转、错误状态、重复提交
- 验证：`pytest`、`ruff check .`、`pnpm typecheck`、`pnpm lint`、相关 Vitest 测试

