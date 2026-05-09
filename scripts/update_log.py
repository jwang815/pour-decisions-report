#!/usr/bin/env python3
"""Append the new run's metrics to /home/user/workspace/cron_tracking/dc4e59f2/run_log.json.

USAGE:
  python3 update_log.py <run_number>
"""
import json, sys, os
from datetime import datetime, timezone

LOG = os.environ.get('PD_RUN_LOG', '/home/user/workspace/cron_tracking/dc4e59f2/run_log.json')

def main(run_n):
    base_data = os.environ.get('PD_DATA_DIR', '/home/user/workspace/square_data')
    final_path = os.path.join(base_data, f'run{run_n}', 'final.json')
    if not os.path.exists(final_path):
        print(f'ERROR: {final_path} not found'); sys.exit(1)
    d = json.load(open(final_path))
    t = d['totals']; bs = d['by_store']
    
    now = datetime.now(timezone.utc)
    
    log = {}
    if os.path.exists(LOG):
        log = json.load(open(LOG))
    
    snapshot = {
        'cron_id': 'dc4e59f2',
        'last_run': now.isoformat().replace('+00:00', 'Z'),
        'run_number': run_n,
        'status': 'completed',
        'report_week': d.get('report_week'),
        'deployment': {
            'platform': 'Vercel',
            'url': 'https://pour-decisions-report.vercel.app',
            'password_protected': True,
            'pnl_preserved': True,
        },
        'key_metrics': {
            'total_payments': t['payments'],
            'net_sales': t['net_sales'],
            'transactions': t['transactions'],
            'aov': t['aov'],
            'tips': t['tips'],
            'labor_cost': t['labor_cost'],
            'labor_hours': t['labor_hours'],
            'labor_pct_net': t['labor_pct_net'],
            'cogs': t['cogs'],
            'op_profit': t['op_profit'],
            'op_margin_pct': t['op_margin_pct'],
            'mtd_total': d.get('mtd_total'),
            'ytd_total': d.get('ytd_total'),
        },
        'wow': d.get('wow', {}),
        'by_store': {c.lower(): {
            'payments': bs[c]['payments'], 'net': bs[c]['net_sales'],
            'txns': bs[c]['transactions'], 'aov': bs[c]['aov'], 'tips': bs[c]['tips'],
            'labor': bs[c]['labor_cost'], 'labor_hours': bs[c]['labor_hours'],
            'labor_pct': bs[c]['labor_pct_net'], 'op_profit': bs[c]['op_profit'],
            'margin': f"{bs[c]['op_margin_pct']:.1f}%",
        } for c in ['SJ', 'MV', 'FM']},
        'top_products': d.get('top_products', {}).get('overall', [])[:5],
        'insights_generated': len(d.get('insights', [])) >= 5,
    }
    
    log.setdefault('history', [])
    # Replace if same run_number, else append
    log['history'] = [h for h in log['history'] if h.get('run_number') != run_n]
    log['history'].append(snapshot)
    log.update(snapshot)  # also update top-level "latest"
    
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(log, open(LOG, 'w'), indent=2)
    print(f'Updated run_log.json for run #{run_n}')

if __name__ == '__main__':
    run_n = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    main(run_n)
