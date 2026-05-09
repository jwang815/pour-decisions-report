#!/usr/bin/env python3
"""Pull Square data, compute all metrics, write final.json.

USAGE:
  python3 fetch_and_compute.py <run_number>

Reads stable config from runbook (hardcoded constants below).
Writes raw Square dumps to /home/user/workspace/square_data/run<N>/raw/
Writes computed metrics to /home/user/workspace/square_data/run<N>/final.json

The agent should NEVER read the raw dumps. Only final.json.

Date logic:
  Today is Monday — covers LAST Monday through LAST Sunday (PDT, UTC-7).
  Prev week = the week before that.
  MTD = first day of current month → today.
  YTD = Jan 1 → today.
"""
import os, sys, json, urllib.request, urllib.error, time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ---------- Config ----------
TOKEN = os.environ.get('SQUARE_API_TOKEN')
if not TOKEN:
    print('ERROR: SQUARE_API_TOKEN env var required', file=sys.stderr)
    sys.exit(2)
BASE = 'https://connect.squareup.com/v2'
LOCATIONS = {
    'SJ': 'LS0NPBF8N48GB',
    'MV': 'LPCGB060NPFRV',
    'FM': 'LKZE7XB7AH284',
}
RENT_MONTHLY = {'SJ': 4884.00, 'MV': 5238.33, 'FM': 4002.00}
COGS_RATE = 0.224  # Blended across categories

PDT = timezone(timedelta(hours=-7))

def headers():
    return {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json',
            'Square-Version': '2024-12-18'}

# ---------- HTTP ----------
def get(url, params=None):
    if params:
        q = '&'.join(f'{k}={v}' for k, v in params.items() if v is not None)
        url = url + ('&' if '?' in url else '?') + q
    req = urllib.request.Request(url, headers=headers(), method='GET')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt); continue
            raise

def post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers(), method='POST')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt); continue
            raise

# ---------- Date math ----------
def week_bounds(today=None):
    """Returns (week_start, week_end) where today=Monday and we cover last Mon-Sun in PDT.
    Returns UTC ISO strings."""
    if today is None:
        today = datetime.now(PDT).date()
    # last Monday
    days_since_mon = today.weekday()  # Mon=0
    # if today is Monday, we want PRIOR week's Mon-Sun
    last_mon = today - timedelta(days=7 if days_since_mon == 0 else days_since_mon)
    last_sun = last_mon + timedelta(days=6)
    start = datetime.combine(last_mon, datetime.min.time(), tzinfo=PDT)
    end = datetime.combine(last_sun, datetime.max.time().replace(microsecond=0), tzinfo=PDT)
    return start, end

# ---------- Square pulls ----------
def pull_payments(loc_id, start_iso, end_iso):
    out = []; cursor = None
    while True:
        params = {
            'location_id': loc_id, 'begin_time': start_iso, 'end_time': end_iso,
            'limit': 100, 'sort_order': 'ASC',
        }
        if cursor: params['cursor'] = cursor
        r = get(f'{BASE}/payments', params)
        out.extend(r.get('payments', []))
        cursor = r.get('cursor')
        if not cursor: break
    return out

def pull_orders(loc_ids, start_iso, end_iso):
    out = []; cursor = None
    body = {
        'location_ids': loc_ids,
        'query': {
            'filter': {
                'date_time_filter': {'closed_at': {'start_at': start_iso, 'end_at': end_iso}},
                'state_filter': {'states': ['COMPLETED']},
            },
            'sort': {'sort_field': 'CLOSED_AT', 'sort_order': 'ASC'},
        },
        'limit': 500,
    }
    while True:
        if cursor: body['cursor'] = cursor
        r = post(f'{BASE}/orders/search', body)
        out.extend(r.get('orders', []))
        cursor = r.get('cursor')
        if not cursor: break
    return out

def batch_retrieve_orders(order_ids):
    """For payments referencing orders not in our window."""
    out = []
    for i in range(0, len(order_ids), 100):
        chunk = order_ids[i:i+100]
        r = post(f'{BASE}/orders/batch-retrieve', {'order_ids': chunk})
        out.extend(r.get('orders', []))
    return out

def pull_shifts(loc_ids, start_iso, end_iso):
    out = []; cursor = None
    body = {
        'query': {'filter': {
            'location_ids': loc_ids,
            'start': {'start_at': start_iso, 'end_at': end_iso},
        }},
        'limit': 200,
    }
    while True:
        if cursor: body['cursor'] = cursor
        r = post(f'{BASE}/labor/shifts/search', body)
        out.extend(r.get('shifts', []))
        cursor = r.get('cursor')
        if not cursor: break
    return out

# ---------- Compute ----------
def compute_metrics(payments, orders_by_id, shifts, wages, prev_payments=None, prev_shifts=None):
    """Compute all metrics from raw data. Returns the final.json dict."""
    by_loc_payments = defaultdict(list)
    
    code_by_loc_id = {v: k for k, v in LOCATIONS.items()}
    for p in payments:
        loc = code_by_loc_id.get(p.get('location_id'))
        if loc:
            by_loc_payments[loc].append(p)
    
    def loc_metrics(loc_payments, loc_code):
        # Filter $0 no-sale
        valid = [p for p in loc_payments
                 if (p.get('total_money', {}).get('amount', 0) > 0 or
                     p.get('tip_money', {}).get('amount', 0) > 0)]
        
        total_money = sum(p['total_money']['amount'] for p in valid) / 100
        refunded = sum(p.get('refunded_money', {}).get('amount', 0) for p in valid) / 100
        net_payments = total_money - refunded
        
        # Tips, excluding fully-refunded
        tips = sum(p.get('tip_money', {}).get('amount', 0) for p in valid
                   if p.get('refunded_money', {}).get('amount', 0) < p['total_money']['amount']) / 100
        
        txns = len(valid)
        aov = net_payments / txns if txns else 0
        
        # Tenders
        tenders = defaultdict(float)
        for p in valid:
            t = p.get('source_type', 'CARD')
            for tend in p.get('processing_fee', []) or [{}]:
                pass  # not needed
            kind = p.get('source_type', 'CARD')
            if kind == 'CARD':
                # Detect gift card sub-type
                card = p.get('card_details', {}).get('card', {})
                if card.get('card_brand') == 'SQUARE_GIFT_CARD':
                    kind = 'GIFT_CARD'
            elif kind == 'EXTERNAL':
                kind = 'EXTERNAL'
            elif kind == 'CASH':
                kind = 'CASH'
            tenders[kind] += p['total_money']['amount'] / 100
        
        # Daily/hourly breakdowns
        daily = defaultdict(float)
        hourly = defaultdict(float)
        for p in valid:
            ts = p.get('created_at', '')
            if not ts: continue
            dt_obj = datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(PDT)
            daily[dt_obj.strftime('%Y-%m-%d')] += p['total_money']['amount'] / 100
            hourly[dt_obj.hour] += p['total_money']['amount'] / 100
        
        # Net sales / gross / discounts via linked orders
        gross = 0; net = 0; discounts = 0
        for p in valid:
            oid = p.get('order_id')
            o = orders_by_id.get(oid)
            if not o: continue
            gross += sum(li.get('gross_sales_money', {}).get('amount', 0) for li in o.get('line_items', [])) / 100
            net_o = sum(li.get('total_money', {}).get('amount', 0) - li.get('total_tax_money', {}).get('amount', 0)
                        for li in o.get('line_items', [])) / 100
            net += net_o
            discounts += sum(d.get('amount_money', {}).get('amount', 0) for d in o.get('discounts', [])) / 100
        
        return {
            'payments': round(net_payments, 2),
            'gross_sales': round(gross, 2),
            'net_sales': round(net, 2),
            'transactions': txns,
            'aov': round(aov, 2),
            'tips': round(tips, 2),
            'tenders': dict(tenders),
            'daily_revenue': dict(daily),
            'hourly_revenue': dict(hourly),
        }
    
    by_store = {code: loc_metrics(by_loc_payments[code], code) for code in LOCATIONS}
    
    # Labor
    wage_by_member = {w['team_member_id']: w.get('hourly_rate', {}).get('amount', 0) / 100
                      for w in wages}
    
    def labor_for_loc(loc_id):
        loc_shifts = [s for s in shifts if s.get('location_id') == loc_id and s.get('end_at')]
        hours = sum(
            (datetime.fromisoformat(s['end_at'].replace('Z', '+00:00')) -
             datetime.fromisoformat(s['start_at'].replace('Z', '+00:00'))).total_seconds() / 3600
            for s in loc_shifts)
        cost = sum(
            ((datetime.fromisoformat(s['end_at'].replace('Z', '+00:00')) -
              datetime.fromisoformat(s['start_at'].replace('Z', '+00:00'))).total_seconds() / 3600)
            * wage_by_member.get(s.get('team_member_id'), 18.0)
            for s in loc_shifts)
        return round(hours, 1), round(cost, 2), len({s.get('team_member_id') for s in loc_shifts})
    
    total_hours = total_cost = 0; staff = set()
    for code, lid in LOCATIONS.items():
        h, c, _ = labor_for_loc(lid)
        by_store[code]['labor_hours'] = h
        by_store[code]['labor_cost'] = c
        by_store[code]['labor_pct_net'] = round(c / by_store[code]['net_sales'] * 100, 1) if by_store[code]['net_sales'] else 0
        by_store[code]['rev_per_hour'] = round(by_store[code]['net_sales'] / h, 2) if h else 0
        total_hours += h; total_cost += c
        for s in shifts:
            if s.get('location_id') == lid: staff.add(s.get('team_member_id'))
    
    # COGS, rent, op profit per store
    for code in LOCATIONS:
        s = by_store[code]
        s['cogs'] = round(s['payments'] * COGS_RATE, 2)
        s['rent'] = round(RENT_MONTHLY[code] * 12 / 52, 2)
        s['op_profit'] = round(s['net_sales'] - s['cogs'] - s['labor_cost'] - s['rent'], 2)
        s['op_margin_pct'] = round(s['op_profit'] / s['net_sales'] * 100, 1) if s['net_sales'] else 0
    
    # Totals
    totals = {
        'payments': round(sum(by_store[c]['payments'] for c in LOCATIONS), 2),
        'gross_sales': round(sum(by_store[c]['gross_sales'] for c in LOCATIONS), 2),
        'net_sales': round(sum(by_store[c]['net_sales'] for c in LOCATIONS), 2),
        'transactions': sum(by_store[c]['transactions'] for c in LOCATIONS),
        'tips': round(sum(by_store[c]['tips'] for c in LOCATIONS), 2),
        'labor_hours': round(total_hours, 1),
        'labor_cost': round(total_cost, 2),
        'staff_count': len(staff),
        'cogs': round(sum(by_store[c]['cogs'] for c in LOCATIONS), 2),
        'rent': round(sum(by_store[c]['rent'] for c in LOCATIONS), 2),
        'tenders': {k: round(sum(by_store[c]['tenders'].get(k, 0) for c in LOCATIONS), 2)
                    for k in {kk for c in LOCATIONS for kk in by_store[c]['tenders']}},
    }
    totals['aov'] = round(totals['net_sales'] / totals['transactions'], 2) if totals['transactions'] else 0
    totals['labor_pct_net'] = round(totals['labor_cost'] / totals['net_sales'] * 100, 1) if totals['net_sales'] else 0
    totals['op_profit'] = round(totals['net_sales'] - totals['cogs'] - totals['labor_cost'] - totals['rent'], 2)
    totals['op_margin_pct'] = round(totals['op_profit'] / totals['net_sales'] * 100, 1) if totals['net_sales'] else 0
    
    # Daily aggregated
    daily_combined = defaultdict(lambda: {'SJ': 0, 'MV': 0, 'FM': 0, 'total': 0})
    for code in LOCATIONS:
        for date, amt in by_store[code]['daily_revenue'].items():
            daily_combined[date][code] = round(amt, 2)
            daily_combined[date]['total'] = round(daily_combined[date]['total'] + amt, 2)
    
    hourly_combined = defaultdict(float)
    for code in LOCATIONS:
        for h, amt in by_store[code]['hourly_revenue'].items():
            hourly_combined[h] += amt
    hourly_combined = {h: round(v, 2) for h, v in hourly_combined.items()}
    
    return {
        'totals': totals,
        'by_store': by_store,
        'daily_revenue': dict(daily_combined),
        'hourly_revenue': hourly_combined,
    }

def compute_top_products(orders_by_id, payments):
    overall = defaultdict(lambda: {'qty': 0, 'revenue': 0.0})
    by_loc = {c: defaultdict(lambda: {'qty': 0, 'revenue': 0.0}) for c in LOCATIONS}
    code_by_id = {v: k for k, v in LOCATIONS.items()}
    
    payment_orders = {p.get('order_id') for p in payments if p.get('order_id')}
    for oid, o in orders_by_id.items():
        if oid not in payment_orders: continue
        loc = code_by_id.get(o.get('location_id'))
        for li in o.get('line_items', []):
            name = li.get('name', 'Unknown')
            qty = int(float(li.get('quantity', 1)))
            rev = li.get('total_money', {}).get('amount', 0) / 100
            overall[name]['qty'] += qty
            overall[name]['revenue'] += rev
            if loc:
                by_loc[loc][name]['qty'] += qty
                by_loc[loc][name]['revenue'] += rev
    
    def topn(d, n):
        items = [{'name': k, 'qty': v['qty'], 'revenue': round(v['revenue'], 2)}
                 for k, v in d.items()]
        return sorted(items, key=lambda x: -x['revenue'])[:n]
    
    return {
        'overall': topn(overall, 15),
        'by_loc': {c: topn(by_loc[c], 5) for c in LOCATIONS},
    }

def main(run_n):
    base_data = os.environ.get('PD_DATA_DIR', '/home/user/workspace/square_data')
    out_dir = os.path.join(base_data, f'run{run_n}')
    raw_dir = os.path.join(out_dir, 'raw')
    os.makedirs(raw_dir, exist_ok=True)
    
    week_start, week_end = week_bounds()
    prev_start = week_start - timedelta(days=7)
    prev_end = week_end - timedelta(days=7)
    
    week_iso = (week_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                week_end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'))
    prev_iso = (prev_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                prev_end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'))
    
    print(f'Week: {week_start.date()} to {week_end.date()}')
    print(f'Prev: {prev_start.date()} to {prev_end.date()}')
    
    # Pulls
    payments = []
    for code, lid in LOCATIONS.items():
        ps = pull_payments(lid, *week_iso)
        payments.extend(ps)
        print(f'  {code} payments: {len(ps)}')
    
    prev_payments = []
    for code, lid in LOCATIONS.items():
        prev_payments.extend(pull_payments(lid, *prev_iso))
    
    orders = pull_orders(list(LOCATIONS.values()), *week_iso)
    orders_by_id = {o['id']: o for o in orders}
    # Batch-retrieve orders referenced by payments but not in window
    payment_oids = {p.get('order_id') for p in payments if p.get('order_id')}
    missing = list(payment_oids - set(orders_by_id))
    if missing:
        for o in batch_retrieve_orders(missing):
            orders_by_id[o['id']] = o
    print(f'Orders: {len(orders_by_id)}')
    
    shifts = pull_shifts(list(LOCATIONS.values()), *week_iso)
    print(f'Shifts: {len(shifts)}')
    
    wages_resp = get(f'{BASE}/labor/team-member-wages', {'limit': 200})
    wages = wages_resp.get('team_member_wages', [])
    
    # Save raw (agent never reads these)
    json.dump(payments, open(os.path.join(raw_dir, 'payments.json'), 'w'))
    json.dump(prev_payments, open(os.path.join(raw_dir, 'prev_payments.json'), 'w'))
    json.dump(orders_by_id, open(os.path.join(raw_dir, 'orders.json'), 'w'))
    json.dump(shifts, open(os.path.join(raw_dir, 'shifts.json'), 'w'))
    json.dump({'week_start': week_iso[0], 'week_end': week_iso[1],
               'prev_start': prev_iso[0], 'prev_end': prev_iso[1]},
              open(os.path.join(raw_dir, 'bounds.json'), 'w'))
    
    # Compute current week
    metrics = compute_metrics(payments, orders_by_id, shifts, wages)
    
    # Compute prev week (lighter — just totals)
    prev_metrics = compute_metrics(prev_payments, {}, [], [])
    
    # WoW
    pt = prev_metrics['totals']; ct = metrics['totals']
    def pct(a, b): return round((a - b) / b * 100, 1) if b else 0
    wow = {
        'payments': pct(ct['payments'], pt['payments']),
        'net_sales': pct(ct['net_sales'], pt['net_sales']),
        'gross_sales': pct(ct['gross_sales'], pt['gross_sales']),
        'transactions': pct(ct['transactions'], pt['transactions']),
        'aov': pct(ct['aov'], pt['aov']),
        'tips': pct(ct['tips'], pt['tips']),
        'labor': pct(ct['labor_cost'], pt['labor_cost']) if pt['labor_cost'] else 0,
    }
    prev_summary = {
        'payments': pt['payments'], 'net_sales': pt['net_sales'],
        'gross_sales': pt['gross_sales'], 'transactions': pt['transactions'],
        'aov': pt['aov'], 'tips': pt['tips'], 'labor_cost': pt['labor_cost'],
    }
    
    # MTD (1st of month → today PDT)
    today = datetime.now(PDT).date()
    month_start = datetime.combine(today.replace(day=1), datetime.min.time(), tzinfo=PDT)
    mtd_iso = (month_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
               datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    mtd_payments = []
    for lid in LOCATIONS.values():
        mtd_payments.extend(pull_payments(lid, *mtd_iso))
    mtd_by_loc = defaultdict(float)
    code_by_id = {v: k for k, v in LOCATIONS.items()}
    for p in mtd_payments:
        if p.get('total_money', {}).get('amount', 0) == 0 and p.get('tip_money', {}).get('amount', 0) == 0:
            continue
        net = (p['total_money']['amount'] - p.get('refunded_money', {}).get('amount', 0)) / 100
        c = code_by_id.get(p.get('location_id'))
        if c: mtd_by_loc[c] += net
    mtd_total = round(sum(mtd_by_loc.values()), 2)
    mtd_by_loc = {c: round(v, 2) for c, v in mtd_by_loc.items()}
    for c in LOCATIONS:
        mtd_by_loc.setdefault(c, 0)
    
    # YTD (load prior baseline + add current week)
    log_path = os.environ.get('PD_RUN_LOG', '/home/user/workspace/cron_tracking/dc4e59f2/run_log.json')
    ytd_prior = 0
    ytd_by_loc_prior = {'SJ': 0, 'MV': 0, 'FM': 0}
    if os.path.exists(log_path):
        log = json.load(open(log_path))
        ytd_prior = log.get('key_metrics', {}).get('ytd_total', 0) - log.get('key_metrics', {}).get('total_payments', 0)
        # If history exists, use last run's ytd_by_store minus its weekly contribution
        # Simpler: derive from log's by_store ytd if recorded — fall back to query
    # Pull YTD payments (only need totals; this is heavier — could optimize later)
    year_start = datetime(today.year, 1, 1, tzinfo=PDT)
    ytd_iso = (year_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
               datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    ytd_by_loc = defaultdict(float)
    for lid in LOCATIONS.values():
        for p in pull_payments(lid, *ytd_iso):
            if p.get('total_money', {}).get('amount', 0) == 0 and p.get('tip_money', {}).get('amount', 0) == 0:
                continue
            net = (p['total_money']['amount'] - p.get('refunded_money', {}).get('amount', 0)) / 100
            c = code_by_id.get(p.get('location_id'))
            if c: ytd_by_loc[c] += net
    ytd_total = round(sum(ytd_by_loc.values()), 2)
    ytd_by_loc = {c: round(v, 2) for c, v in ytd_by_loc.items()}
    
    top_products = compute_top_products(orders_by_id, payments)
    
    # Labels
    week_label = f"{week_start.strftime('%B %-d')} -- {week_end.strftime('%B %-d, %Y')}"
    today_label = today.strftime('%b %-d, %Y').replace(' 0', ' ')
    mtd_label = f"{today.strftime('%B %Y')} ({today.strftime('%B')} 1-{today.day})"
    ytd_label = f"Jan 1 - {today.strftime('%b %-d, %Y')}"
    
    final = {
        'report_week': f"{week_start.strftime('%b %-d')} - {week_end.strftime('%b %-d, %Y')}",
        'week_label': week_label,
        'generated_date': today_label,
        'mtd_label': mtd_label,
        'ytd_label': ytd_label,
        **metrics,
        'wow': wow,
        'prev_week': prev_summary,
        'mtd_total': mtd_total,
        'mtd_by_store': mtd_by_loc,
        'ytd_total': ytd_total,
        'ytd_by_store': ytd_by_loc,
        'top_products': top_products,
        'insights': [],  # populated separately by generate_insights.py or hand-written
    }
    
    json.dump(final, open(os.path.join(out_dir, 'final.json'), 'w'), indent=2)
    print(f'\nSaved {out_dir}/final.json')
    print(f'  Total Payments: ${final["totals"]["payments"]:,.2f}')
    print(f'  Op Margin: {final["totals"]["op_margin_pct"]}%')
    print(f'  Insights: empty — write generate_insights.py output or fill manually before build_report.py')

if __name__ == '__main__':
    run_n = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    main(run_n)
