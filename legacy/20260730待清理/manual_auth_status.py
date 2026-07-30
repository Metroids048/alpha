#!/usr/bin/env python3
"""Test current authentication status."""
import os
import sys
from alpha_mining.platform.client import ReadOnlyPlatformClient

try:
    print('Testing authentication status...')
    client = ReadOnlyPlatformClient()

    print('\nFetching user identity...')
    user_info = client.fetch_identity()

    print(f'✓ Authentication successful!')
    print(f'  Username: {user_info.get("username")}')
    print(f'  Tier: {user_info.get("tier")}')
    print(f'  Status: {user_info.get("status")}')
    print(f'  User ID: {user_info.get("id")}')

    sys.exit(0)

except Exception as e:
    print(f'✗ Authentication failed: {e}')
    sys.exit(1)
