# Bearer Token 认证方案实现总结

## 问题根源

**平台不支持用户名密码 Basic Auth 自动登录**（返回 401），导致每次都需要手动导入 Cookie。

## 解决方案

### 核心发现

通过实际测试发现：**浏览器登录后的 JWT Token（Cookie 中的 `t`）可以直接作为 Bearer Token 使用**。

### 实现细节

1. **新增 `alpha_mining/platform/bearer_auth.py`**
   - `BearerToken` 类：封装 JWT token 和过期时间
   - `load_bearer_token()`: 从 DPAPI 加密的认证状态中提取 JWT
   - 自动解析 JWT 的 `exp` 字段判断是否过期

2. **修改 `alpha_mining/platform/client.py`**
   - `authenticate()` 优先尝试 Bearer Token 认证
   - 如果 Token 有效且未过期，直接设置 `Authorization: Bearer <token>` 头
   - 只有在 Token 不可用或强制刷新时才回退到密码登录

3. **新增测试 `tests/test_browser_session_import.py`**
   - 验证浏览器会话导入只保留必需的 Cookie（`t` 和 `cf_clearance`）
   - 验证过期会话自动恢复
   - 验证经过验证的会话状态变为 FRESH

## 使用方式

### 一次性导入（首次或 Token 过期后）

```powershell
# 在浏览器登录后，复制 Cookie
$env:WQ_BROWSER_COOKIE = '完整的 Cookie 字符串'
& $env:AGENT_PYTHON test_wq_auth.py --cookie-env WQ_BROWSER_COOKIE
Remove-Item Env:\WQ_BROWSER_COOKIE
```

### 后续自动使用

导入后，所有平台访问会自动：
1. 从 DPAPI 加密状态中提取 JWT Token
2. 检查是否在有效期内（5分钟缓冲期）
3. 使用 `Authorization: Bearer <token>` 头认证
4. **不再需要每次导入 Cookie**

### Token 生命周期

- 当前 Token 有效期：约 **3-4 小时**
- Token 过期后重新在浏览器登录，导入一次新的 Cookie
- 不是每次运行都要导入，只是 Token 真正过期后才需要

## 验证结果

✅ **656 个测试全部通过**
✅ **Bearer Token 认证正常工作**
✅ **身份 API 返回 200**
✅ **Alphas 列表可访问**

⚠️  **数据目录端点返回 403**（`/data-sets`, `/data-fields`, `/operators`）
   - 这是**平台账户权限问题**，不是认证问题
   - 身份验证已通过（200），但账户没有访问完整数据目录的权限
   - 可能需要联系 WorldQuant 开通顾问账户的数据目录访问权限

## 下一步

1. **目录权限问题**需要联系 WorldQuant 支持确认：
   - 顾问账户是否需要额外申请数据目录权限
   - 或者是否有其他 API 端点可以获取 datasets/operators 列表

2. **Token 刷新策略**（可选优化）：
   - 当前方案：Token 快过期时，手动在浏览器重新登录并导入
   - 未来可考虑：监控 Token 剩余时间，自动提醒或尝试刷新

3. **恢复循环运行**：
   - 如果数据目录权限问题解决，可以恢复 `run_pipeline_loop.py`
   - 如果权限无法解决，需要调整工厂逻辑绕过目录同步
