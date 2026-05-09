#!/usr/bin/env python3
"""Reconcile Square data and write data_quality findings into final.json.

Three checks:
  1. Cross-validate Payments API totals vs Orders API totals (linked orders only)
  2. Pull Square Dashboard Sales summary report endpoint and compare
  3. Sanity bounds: KPI vs prior 4-week average; labor% in 15-40%

All discrepancies are appended to final.json under data_quality. The run never
fails on a discrepancy — the report renders with a Data Quality panel showing
the issues so they're visible on the dashboard.

USAGE:
  python3 reconcile.py <run_dir>
  e.g. python3 reconcile.py /home/user/workspace/square_data/run11
"""
import json, os, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

PDT = timezone(timedelta(hours=-7))

def load(path):
    with open(path) as f: return json.load(f)

def check_payments_vs_orders(run_dir):
    """Sum payment.total_money - refunded_money against orders' linked totals."""
    raw = os.path.join(run_dir, 'raw')
    payments = load(os.path.join(raw, 'payments.json'))
    orders = load(os.path.join(raw, 'orders.json'))  # dict by id
    
    pay_total = sum(
        (p['total_money']['amount'] - p.get('refunded_money', {}).get('amount', 0)) / 100
        for p in payments
        if (p.get('total_money', {}).get('amount', 0) > 0
            or p.get('tip_money', {}).get('amount', 0) > 0)
    )
    
    # Orders total (from linked orders only — same set used for net sales)
    pay_oids = {p.get('order_id') for p in payments if p.get('order_id')}
    orders_total = sum(
        o.get('total_money', {}).get('amount', 0) / 100
        for oid, o in orders.items() if oid in pay_oids
    )
    
    delta = pay_total - orders_total
    delta_pct = (delta / pay_total * 100) if pay_total else 0
    
    return {
        'check': 'Payments API vs Orders API (linked)',
        'payments_total': round(pay_total, 2),
        'orders_total': round(orders_total, 2),
        'delta': round(delta, 2),
        'delta_pct': round(delta_pct, 2),
        'status': 'PASS' if abs(delta_pct) < 1.0 else 'WARN' if abs(delta_pct) < 3.0 else 'FAIL',
        'tolerance': '<1.0% PASS, <3.0% WARN, else FAIL',
    }

def check_dashboard_summary(run_dir):
    """Pull Square's Sales Summary report and compare to our totals.
    
    Square doesn't have a single 'dashboard summary' API, so we replicate the
    Dashboard 'Net Sales' calculation by re-querying Payments via list endpoint
    and confirming we got the same set. This catches pagination bugs.
    """
    token = os.environ.get('SQUARE_API_TOKEN')
    if not token:
        return {'check': 'Dashboard summary', 'status': 'SKIP', 'note': 'no token'}
    
    final = load(os.path.join(run_dir, 'final.json'))
    raw_payments = load(os.path.join(run_dir, 'raw', 'payments.json'))
    
    # Re-fetch payment count for each location and compare cardinality
    LOCATIONS = {'SJ': 'LS0NPBF8N48GB', 'MV': 'LPCGB060NPFRV', 'FM': 'LKZE7XB7AH284'}
    
    # Reconstruct date range from final.json week_label
    # Use the same begin_time/end_time stored alongside raw if available
    bounds_path = os.path.join(run_dir, 'raw', 'bounds.json')
    if not os.path.exists(bounds_path):
        return {'check': 'Dashboard summary', 'status': 'SKIP', 'note': 'no bounds file'}
    bounds = load(bounds_path)
    
    def fetch_count(loc_id, begin, end):
        """Fetch just the first page to get the cursor; iterate to get total count."""
        cnt = 0; cursor = None
        while True:
            url = (f'https://connect.squareup.com/v2/payments?location_id={loc_id}'
                   f'&begin_time={begin}&end_time={end}&limit=100')
            if cursor: url += f'&cursor={cursor}'
            req = urllib.request.Request(url, headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json',
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
            except Exception as e:
                return None, str(e)
            cnt += len(data.get('payments', []))
            cursor = data.get('cursor')
            if not cursor: break
        return cnt, None
    
    discrepancies = []
    code_by_id = {v: k for k, v in LOCATIONS.items()}
    saved_count_by_loc = defaultdict(int)
    for p in raw_payments:
        c = code_by_id.get(p.get('location_id'))
        if c: saved_count_by_loc[c] += 1
    
    for code, lid in LOCATIONS.items():
        cnt, err = fetch_count(lid, bounds['week_start'], bounds['week_end'])
        if err:
            discrepancies.append(f'{code}: refetch error {err[:80]}')
        elif cnt != saved_count_by_loc[code]:
            discrepancies.append(f'{code}: refetch={cnt}, saved={saved_count_by_loc[code]}')
    
    return {
        'check': 'Dashboard summary (re-query payments cardinality)',
        'discrepancies': discrepancies,
        'status': 'PASS' if not discrepancies else 'WARN',
        'note': 'Confirms pagination retrieved all payments',
    }

def check_sanity_bounds(run_dir):
    """Compare current week to prior 4-week average from cron tracking history."""
    final = load(os.path.join(run_dir, 'final.json'))
    log_path = os.environ.get('PD_RUN_LOG', '/home/user/workspace/cron_tracking/dc4e59f2/run_log.json')
    
    findings = []
    t = final['totals']
    
    # Labor % bounds
    lp = t['labor_pct_net']
    if lp < 15:
        findings.append({'metric': 'labor_pct_net', 'value': lp, 'severity': 'WARN',
                         'note': f'Labor {lp}% unusually LOW (target 25-28%, hard floor 15%)'})
    elif lp > 40:
        findings.append({'metric': 'labor_pct_net', 'value': lp, 'severity': 'WARN',
                         'note': f'Labor {lp}% unusually HIGH (target 25-28%, hard ceiling 40%)'})
    
    # Compare to 4-week trailing average
    if os.path.exists(log_path):
        log = load(log_path)
        history = log.get('history', [])
        # Take last 4 runs (excluding current)
        recent = [h for h in history if h.get('run_number', 0) < final.get('run_number', 999)][-4:]
        if len(recent) >= 2:
            avg_payments = sum(h['key_metrics']['total_payments'] for h in recent) / len(recent)
            avg_txns = sum(h['key_metrics']['transactions'] for h in recent) / len(recent)
            avg_labor_pct = sum(h['key_metrics']['labor_pct_net'] for h in recent) / len(recent)
            
            for label, curr, avg, key in [
                ('Total Payments', t['payments'], avg_payments, 'payments'),
                ('Transactions', t['transactions'], avg_txns, 'txns'),
                ('Labor %', t['labor_pct_net'], avg_labor_pct, 'labor_pct'),
            ]:
                if avg == 0: continue
                pct_off = abs(curr - avg) / avg * 100
                if pct_off > 50:
                    findings.append({
                        'metric': key, 'value': curr, 'avg_4w': round(avg, 2),
                        'pct_off': round(pct_off, 1), 'severity': 'WARN',
                        'note': f'{label} {pct_off:.0f}% off 4-week avg ({avg:.2f})',
                    })
                elif pct_off > 25:
                    findings.append({
                        'metric': key, 'value': curr, 'avg_4w': round(avg, 2),
                        'pct_off': round(pct_off, 1), 'severity': 'INFO',
                        'note': f'{label} {pct_off:.0f}% off 4-week avg ({avg:.2f})',
                    })
    
    return {
        'check': 'Sanity bounds (vs prior 4-week average)',
        'findings': findings,
        'status': 'PASS' if not findings else
                  ('WARN' if any(f['severity'] == 'WARN' for f in findings) else 'INFO'),
    }

def main(run_dir):
    final_path = os.path.join(run_dir, 'final.json')
    if not os.path.exists(final_path):
        print(f'ERROR: {final_path} not found'); sys.exit(1)
    final = load(final_path)
    
    checks = [
        check_payments_vs_orders(run_dir),
        check_dashboard_summary(run_dir),
        check_sanity_bounds(run_dir),
    ]
    
    overall = 'PASS'
    for c in checks:
        if c['status'] == 'FAIL': overall = 'FAIL'
        elif c['status'] == 'WARN' and overall != 'FAIL': overall = 'WARN'
    
    final['data_quality'] = {
        'overall_status': overall,
        'checks': checks,
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    
    with open(final_path, 'w') as f:
        json.dump(final, f, indent=2)
    
    print(f'Data Quality: {overall}')
    for c in checks:
        print(f"  [{c['status']}] {c['check']}")
        if c.get('discrepancies'):
            for d in c['discrepancies']: print(f"      - {d}")
        if c.get('findings'):
            for f in c['findings']:
                print(f"      [{f['severity']}] {f['note']}")

if __name__ == '__main__':
    run_dir = sys.argv[1] if len(sys.argv) > 1 else '/home/user/workspace/square_data/run10'
    main(run_dir)
