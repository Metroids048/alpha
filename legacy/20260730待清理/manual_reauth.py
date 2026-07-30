#!/usr/bin/env python
"""Test re-authentication with current credentials."""
import os
from alpha_mining.platform.client import ReadOnlyPlatformClient

username = os.getenv('WQ_USERNAME')
password = os.getenv('WQ_PASSWORD')

if not username or not password:
    print('Error: WQ_USERNAME or WQ_PASSWORD not set')
    exit(1)

print(f'Login as {username}...')

client = ReadOnlyPlatformClient()
try:
    client.authenticate(force=True)
    print('✓ Authentication successful!')

    print('\nTest fetch_identity...')
    user_info = client.fetch_identity()
    tier = user_info.get('tier', 'N/A')
    uname = user_info.get('username', 'N/A')
    status = user_info.get('status', 'N/A')
    print(f'User: tier={tier}, username={uname}, status={status}')

    print('\n✓ Authentication is valid and working!')

except Exception as e:
    print(f'✗ Auth failed: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
    exit(1)
