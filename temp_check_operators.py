import sys
sys.path.insert(0, '.')
from alpha_mining.storage.local_snapshots import LocalSnapshots

s = LocalSnapshots.load('.')
print('Available operators:')
for op in sorted(s.catalog.operators.keys()):
    print(f'  {op}')
print(f'\nTotal: {len(s.catalog.operators)}')
