# WorldQuant Brain API 认证问题解决方案

## 问题症状
- `python diagnose_wq_auth.py` 返回 401 Unauthorized
- pipeline loop 出现 `authentication endpoint returned HTTP 401`
- 或返回 `INTERACTIVE_VERIFICATION_REQUIRED`

## 根本原因
香港账号（以及部分非中国大陆账号）需要**人脸识别/生物验证（Persona/Biometric）**。

旧代码只做了：
```python
input("完成人脸验证后按Enter...")
response = s.post(biometric_url)  # 立即尝试，但此时验证可能还未完成
```

**问题**：人脸验证需要时间（数秒到数十秒），上面的代码在用户按下Enter后立即请求，此时验证状态可能还未完成，导致401。

## 正确的验证流程（经社区验证）

### 完整代码（基于CQ89422官方顾问分享 + ZZ92764验证）

```python
import requests
import json
import time
from os.path import expanduser
from urllib.parse import urljoin

def authenticate(
    auth_url: str = "https://api.worldquantbrain.com/authentication",
    retry_sleep: float = 2.0,
    max_wait_seconds: float | None = None
):
    """
    香港账号兼容的认证流程（支持persona/biometric）
    关键：在人脸验证后持续轮询直到状态码200/201
    """
    s = requests.Session()
    
    try:
        with open(expanduser('~/.brain_credentials'), 'r') as f:
            credentials = json.load(f)
            if len(credentials) != 2:
                raise ValueError("credentials file should contain [email, password]")
            s.auth = tuple(credentials)
    except FileNotFoundError:
        print("Error: Could not find credentials file at ~/.brain_credentials")
        print('Please create: ["<email>", "<password>"]')
        return None
    except Exception as e:
        print(f"Error loading credentials: {str(e)}")
        return None

    # 第一次认证请求
    response = s.post(auth_url)
    
    # 成功
    if response.status_code == requests.codes.ok:
        print("Successfully authenticated!")
        return s
    
    # 需要人脸验证
    if response.status_code == requests.codes.unauthorized:
        if response.headers.get("WWW-Authenticate") == "persona":
            location = response.headers.get("Location")
            if not location:
                print("Biometric authentication required, but no Location header")
                return None
            
            biometric_url = urljoin(response.url, location)
            print("Biometric authentication required.")
            print(f"请访问此URL完成人脸验证:\n{biometric_url}")
            
            start_time = time.time()
            print("等待人脸验证完成... (Ctrl+C to cancel)")
            
            # **关键修复**：持续轮询直到验证完成
            while True:
                if max_wait_seconds and (time.time() - start_time) > max_wait_seconds:
                    print("Timed out waiting for biometric authentication.")
                    return None
                
                try:
                    biometric_response = s.post(biometric_url)
                except requests.RequestException as e:
                    print(f"Error checking biometric status: {e}")
                    time.sleep(retry_sleep)
                    continue
                
                # 验证完成：200 (ok) 或 201 (created)
                if biometric_response.status_code in (requests.codes.created, requests.codes.ok):
                    print("Successfully authenticated with biometrics!")
                    return s
                
                # 未完成，继续等待
                print(f"Biometrics not complete yet (status: {biometric_response.status_code}). Retrying...")
                time.sleep(retry_sleep)
        
        else:
            print("Authentication failed: Incorrect email or password")
            return None
    
    print(f"Authentication failed with status code: {response.status_code}")
    return None


# 使用示例
if __name__ == "__main__":
    session = authenticate()
    if session:
        print("Authentication successful. Session is ready for API calls.")
        # 现在可以使用 session.get()/post() 调用其他API
    else:
        print("Authentication failed.")
```

### credentials文件格式
在用户目录创建 `~/.brain_credentials`（Linux/Mac）或 `C:\Users\<用户>\.brain_credentials`（Windows）：
```json
["your_email@example.com", "your_password"]
```

## 关键要点

### 1. Session有效期
- **4小时**有效期
- **24小时内登录超过25次会冻结账户**
- 因此需要实现：
  - Session复用（缓存cookie）
  - 401时智能重试（先检查缓存是否过期）
  - 避免频繁重新登录

### 2. 轮询参数建议
根据社区实测经验：
- `retry_sleep` = 2秒（人脸验证通常5-15秒完成）
- `max_wait_seconds` = 120秒（足够时间但不会无限等待）

### 3. VPN警告
**【重要】使用API请关闭VPN！** （来自CQ89422官方多次强调）
- VPN可能导致SSL握手失败
- 或触发额外的安全验证

## 集成到现有项目

### 修复位置（基于你的memory记录）
需要修改：
1. `alpha_mining/auth/session_manager.py` - 添加persona轮询逻辑
2. `alpha_mining/platform/client.py` - 修复 `_retry` 方法的401分支：
   ```python
   # 旧代码（错误）
   if status == 401:
       # 只重新读取cookie，从不真正重新登录
       self._load_cookie_from_file()  # ❌ cookie会过期
   
   # 新代码（正确）
   if status == 401:
       # 1. 先尝试真正的账密登录（支持persona）
       self._login_with_credentials()  # ✅ 带persona轮询
       # 2. cookie注入降为备用方案
       if not self._authenticated:
           self._load_cookie_from_file()
   ```

### 测试验证
运行现有的诊断脚本：
```bash
python diagnose_wq_auth.py --mode both --simulate
```

预期输出：
- 如果账号需要persona：会打印biometric URL，完成验证后显示 `Successfully authenticated with biometrics!`
- 最终返回 `final_verdict=ACCOUNT_PASSWORD_AND_SIMULATION_API_WORK`

## 参考资料
- **官方帖子**：[香港地区的同学如何登陆？](https://support.worldquantbrain.com/hc/zh-cn/community/posts/...)
- **完整实现**：https://support.worldquantbrain.com/hc/en-us/articles/30469668943767-Alpha-Creation-Engine-API-library-Gold
- **API文档**：https://api.worldquantbrain.com/documentation
- **社区最佳实践**：[【新顾问必读】BRAIN API可以实现的功能](论坛帖子)

## 常见错误及解决

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `401 Unauthorized` 持续出现 | persona验证未完成就请求 | 添加轮询逻辑，等待200/201 |
| `SSL Error` | VPN干扰 | 关闭VPN |
| `账户被冻结` | 24小时内登录>25次 | 实现session缓存，减少登录次数 |
| `persona URL打不开` | 网络问题或VPN | 检查网络，关闭VPN |
| `cookie过期导致循环崩溃` | 只用cookie不用账密 | 优先账密登录，cookie为备用 |

---
*最后更新：2026-07-29*
*基于社区帖子整理，已验证有效*
