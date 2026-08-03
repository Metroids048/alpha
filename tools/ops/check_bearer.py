import os
from pathlib import Path
from alpha_mining.platform.bearer_auth import load_bearer_token

os.environ['WQ_USERNAME'] = 'pengweisun048@gmail.com'

state_path = 'alpha_state.sqlite3'
username = os.environ['WQ_USERNAME']

bearer = load_bearer_token(state_path, username)

if bearer:
    print(f'Bearer token found:')
    print(f'   Token: {bearer.token[:50]}...')
    print(f'   Expires: {bearer.expires_at}')
    print(f'   Is expired: {bearer.is_expired}')
else:
    print('No bearer token found')
