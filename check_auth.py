from alpha_mining.auth.session_manager import load_session_cookies
import requests

cookies = load_session_cookies()
print(f'Session cookies: {len(cookies)} items')

if cookies:
    # 测试 cookies 是否有效
    resp = requests.get(
        'https://api.worldquantbrain.com/users/self',
        cookies=cookies,
        timeout=10
    )
    print(f'GET /users/self: {resp.status_code}')
    if resp.status_code == 200:
        data = resp.json()
        print(f'   Username: {data.get("username")}')
        print(f'   Email: {data.get("email")}')
    else:
        print(f'   Error: {resp.text[:100]}')
else:
    print('No cookies found')
