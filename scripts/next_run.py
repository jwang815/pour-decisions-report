#!/usr/bin/env python3
"""Print the next run number based on PD_RUN_LOG history. Used by CI to stamp run dirs."""
import json, os, sys

LOG = os.environ.get('PD_RUN_LOG', 'state/run_log.json')
n = 1
if os.path.exists(LOG):
    try:
        log = json.load(open(LOG))
        history = log.get('history', [])
        if history:
            n = max(h.get('run_number', 0) for h in history) + 1
    except Exception as e:
        print(f'warn: could not read {LOG}: {e}', file=sys.stderr)
print(n)
