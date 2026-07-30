# 🔄 Bearer Token 刷新指南

## 📊 当前状态

- **Token 有效期**: 约 3-4 小时
- **当前剩余**: 1.81 小时
- **过期时间**: 2026-07-25 04:39:15 UTC

## ⏰ Token 失效后会发生什么

### 自动行为

当 Bearer Token 过期后，系统会：

1. **自动检测过期**
   - `load_bearer_token()` 返回 `None`（Token 过期）
   - 或返回 `bearer.is_expired = True`

2. **自动回退到密码认证**
   - 如果设置了 `WQ_PASSWORD` 环境变量
   - 系统会尝试使用用户名密码登录
   - **但通常会失败**（平台不支持密码登录）

3. **循环会报告认证失败**
   - 显示 401 或认证错误
   - 进入退避状态
   - 停止生成新的 Alpha

## 🔧 手动刷新 Token（推荐）

### 方法 1: 使用现有脚本（最简单）

```powershell
# 1. 在浏览器登录 platform.worldquantbrain.com
# 2. 按 F12 打开开发者工具 -> Network 标签
# 3. 刷新页面，找到任意请求
# 4. 在 Request Headers 中找到 Cookie，复制完整值

# 5. 在 PowerShell 中执行:
$env:WQ_BROWSER_COOKIE = '粘贴完整的Cookie字符串'
& $env:AGENT_PYTHON test_wq_auth.py --cookie-env WQ_BROWSER_COOKIE
Remove-Item Env:\WQ_BROWSER_COOKIE

# 6. 验证
& $env:AGENT_PYTHON -c "
from alpha_mining.platform.bearer_auth import load_bearer_token
import os
os.environ['WQ_USERNAME'] = 'pengweisun048@gmail.com'
bearer = load_bearer_token()
print(f'Token 有效期: {bearer.remaining_seconds/3600:.1f} 小时' if bearer else 'Token 无效')
"
```

### 方法 2: 手动提取 JWT Token

如果只需要更新 Token，不想导入整个 Cookie：

```powershell
# 1. 从浏览器 Cookie 中找到 't' 字段的值（这就是 JWT Token）

# 2. 直接更新认证状态
& $env:AGENT_PYTHON -c "
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

username = 'pengweisun048@gmail.com'
jwt_token = '你的JWT_Token'  # 从浏览器复制

# 加载现有状态
fingerprint = hashlib.sha256(username.strip().casefold().encode()).hexdigest()
state_path = Path('.wq_auth_state.json')

if state_path.exists():
    state = json.loads(state_path.read_text())
    
    # 更新 JWT Token
    import win32crypt
    import base64
    
    rows = [
        {'name': 't', 'value': jwt_token},
        {'name': 'cf_clearance', 'value': state.get('cf_clearance', '')}
    ]
    
    encrypted = win32crypt.CryptProtectData(
        json.dumps(rows).encode('utf-8'), None, None, None, None, 0
    )
    blob = base64.b64encode(encrypted).decode('ascii')
    
    state['cookie_blob_dpapi_b64'] = blob
    state['generation'] = state.get('generation', 0) + 1
    state['validated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    state_path.write_text(json.dumps(state, indent=2))
    print('✓ Token 已更新')
else:
    print('✗ 状态文件不存在')
"
```

## 🔔 设置提醒（可选）

### 自动提醒脚本

创建一个定时任务，在 Token 快过期时提醒你：

```powershell
# check_token_expiry.ps1
& $env:AGENT_PYTHON -c "
from alpha_mining.platform.bearer_auth import load_bearer_token
import os
os.environ['WQ_USERNAME'] = 'pengweisun048@gmail.com'

bearer = load_bearer_token()
if bearer:
    hours = bearer.remaining_seconds / 3600
    if hours < 0.5:
        print('⚠️ URGENT: Token 剩余 {:.0f} 分钟，请立即刷新！'.format(hours * 60))
        exit(1)
    elif hours < 1.0:
        print('⚠️ Token 剩余 {:.1f} 小时，建议尽快刷新'.format(hours))
        exit(1)
    else:
        print('✓ Token 状态良好，剩余 {:.1f} 小时'.format(hours))
        exit(0)
else:
    print('✗ Token 不可用或已过期')
    exit(2)
"

# 使用 Windows 任务计划程序每小时运行一次
```

## 🚀 未来优化方向（可选）

### 1. 自动刷新机制

可以实现一个后台脚本，定期检查并刷新 Token：

```python
# auto_refresh_token.py
import time
from datetime import datetime, timedelta
from alpha_mining.platform.bearer_auth import load_bearer_token
import os

os.environ['WQ_USERNAME'] = 'pengweisun048@gmail.com'

while True:
    bearer = load_bearer_token()
    
    if not bearer or bearer.is_expired:
        print(f'{datetime.now()}: Token 已过期，需要刷新')
        # 这里可以添加通知逻辑（邮件、Webhook等）
    elif bearer.remaining_seconds < 1800:  # 少于30分钟
        print(f'{datetime.now()}: Token 即将过期，剩余 {bearer.remaining_seconds/60:.0f} 分钟')
    else:
        print(f'{datetime.now()}: Token 状态正常，剩余 {bearer.remaining_seconds/3600:.1f} 小时')
    
    # 每30分钟检查一次
    time.sleep(1800)
```

### 2. 使用浏览器自动化（高级）

如果需要完全自动化，可以使用 Selenium 或 Playwright 自动登录浏览器并提取 Cookie：

```python
# auto_extract_cookie.py (需要安装 playwright)
from playwright.sync_api import sync_playwright

def extract_cookie():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # 导航到登录页面
        page.goto('https://platform.worldquantbrain.com')
        
        # 等待用户手动登录（或实现自动登录逻辑）
        input('请在浏览器中完成登录，然后按 Enter...')
        
        # 提取 Cookie
        cookies = page.context.cookies()
        jwt_token = next((c['value'] for c in cookies if c['name'] == 't'), None)
        
        browser.close()
        
        return jwt_token
```

## 📋 快速检查清单

当你注意到 Alpha 生成停止时：

- [ ] 检查 Token 状态
  ```powershell
  & $env:AGENT_PYTHON -c "from alpha_mining.platform.bearer_auth import load_bearer_token; import os; os.environ['WQ_USERNAME'] = 'pengweisun048@gmail.com'; bearer = load_bearer_token(); print(f'剩余: {bearer.remaining_seconds/3600:.1f}h' if bearer and not bearer.is_expired else 'Token 无效')"
  ```

- [ ] 如果 Token 过期，从浏览器获取新 Cookie

- [ ] 运行导入脚本
  ```powershell
  $env:WQ_BROWSER_COOKIE = '新Cookie'
  & $env:AGENT_PYTHON test_wq_auth.py --cookie-env WQ_BROWSER_COOKIE
  Remove-Item Env:\WQ_BROWSER_COOKIE
  ```

- [ ] 验证 Token 已更新

- [ ] 检查循环是否恢复正常

## 💡 重要提示

1. **不需要重启循环**
   - Token 更新后，下一次 API 请求会自动使用新 Token
   - 循环会自动恢复

2. **Token 有效期**
   - 通常为 3-4 小时
   - 建议在剩余 1 小时时刷新

3. **备份策略**
   - `.wq_auth_state.json` 包含加密的 Token
   - 定期备份此文件可以快速恢复

4. **安全建议**
   - 不要将 Cookie 或 JWT Token 提交到 Git
   - `.wq_auth_state.json` 已在 `.gitignore` 中

## 🔍 故障排查

如果刷新后仍然无法认证：

1. **检查 Cookie 是否完整**
   ```powershell
   # Cookie 应该包含 't' 和 'cf_clearance' 字段
   ```

2. **验证账户状态**
   - 在浏览器中确认可以正常访问平台
   - 检查账户是否被锁定或暂停

3. **检查认证状态文件**
   ```powershell
   Get-Content .wq_auth_state.json | ConvertFrom-Json | Select-Object generation, validated_at
   ```

4. **强制重新导入**
   ```powershell
   # 删除旧状态，强制重新导入
   Remove-Item .wq_auth_state.json -Force
   # 然后重新导入 Cookie
   ```

---

**总结**: Token 失效后只需 2 分钟就能刷新，整个流程非常简单！
