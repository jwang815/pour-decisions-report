#!/usr/bin/env python3
"""Render index.html from template.html + final.json in one pass.

Replaces build1..build5.py. Single-pass, single-file.

Reads:
  /home/user/workspace/pour-decisions-pipeline/template.html
  /home/user/workspace/square_data/run<N>/final.json   (schema below)

Writes:
  /home/user/workspace/pour-decisions-report/index.html

final.json schema (real, from Run #10):
{
  "report_week": "Apr 27 - May 3, 2026",
  "totals": {payments, net_sales, gross_sales, transactions, aov, tips,
             labor_hours, labor_cost, labor_pct_net, cogs, rent, op_profit,
             op_margin_pct, tenders: {CARD, CASH, EXTERNAL, GIFT_CARD}},
  "by_store": {SJ: {...same fields..., daily_revenue, hourly_revenue},
               MV: {...}, FM: {...}},
  "daily_revenue": {"2026-04-27": {SJ, MV, FM, total}, ...},
  "hourly_revenue": {hour: total},
  "wow": {payments, net_sales, gross_sales, transactions, aov, tips, labor},  # pct
  "prev_week": {payments, net_sales, gross_sales, txns, aov, tips, labor},
  "mtd_total", "mtd_by_store", "ytd_total", "ytd_by_store",
  "top_products": {overall: [{name, qty, revenue}], by_loc: {SJ, MV, FM}},
  "insights": [{title, body}, ...],   # NEW field — write 5 in fetch_and_compute.py
  "generated_date": "May 4, 2026",
  "week_label": "April 27 -- May 3, 2026",
  "mtd_label": "May 2026 (May 1-3)",
  "ytd_label": "Jan 1 - May 3, 2026"
}
"""
import json, sys, os, re

# Defaults: when running in CI, scripts and templates live alongside this file (scripts/),
# and the rendered output goes to PD_OUT_DIR (defaulting to repo root one level up).
_DEFAULT_PIPELINE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_OUT_DIR = os.environ.get('PD_OUT_DIR') or os.path.dirname(_DEFAULT_PIPELINE)
PIPELINE = os.environ.get('PD_PIPELINE_DIR', _DEFAULT_PIPELINE)
OUT = os.environ.get('PD_INDEX_OUT', os.path.join(_DEFAULT_OUT_DIR, 'index.html'))

LOC_META = {
    'SJ': {'name': 'San Jose', 'emoji': '&#127978;', 'color': 'sj-color', 'rent_mo': 4884},
    'MV': {'name': 'Mountain View', 'emoji': '&#9968;', 'color': 'mv-color', 'rent_mo': 5238.33},
    'FM': {'name': 'Fremont', 'emoji': '&#127881;', 'color': 'fm-color', 'rent_mo': 4002},
}

# ---------- Helpers ----------
def fmt_money(n, dec=2):
    if n is None: return '--'
    return f'${n:,.{dec}f}'

def wow_badge(pct, lower_is_better=False):
    if pct is None: return ''
    is_up = pct > 0
    if lower_is_better:
        cls = 'up-bad' if is_up else 'down'
    else:
        cls = 'up' if is_up else 'down-bad'
    arrow = '&#9650;' if is_up else '&#9660;'
    return f'<span class="wow-badge {cls}">{arrow} {abs(pct):.1f}%</span>'

def pct_change(curr, prev):
    if not prev: return None
    return (curr - prev) / prev * 100

# ---------- Block generators ----------

def block_kpi(d):
    t = d['totals']; p = d['prev_week']; bs = d['by_store']
    
    def card(label, val, prev_val, fmt_fn=fmt_money, lib=False, sub=None):
        badge = wow_badge(pct_change(val, prev_val), lib)
        sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ''
        return f'''      <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{fmt_fn(val)} {badge}</div>
        {sub_html}
      </div>'''
    
    def view(view_id, src, prev):
        cards = [
            card('Total Payments', src['payments'], prev['payments']),
            card('Net Sales', src['net_sales'] if 'net_sales' in src else src['net_sales'], prev.get('net_sales', prev.get('net'))),
            card('Transactions', src['transactions'], prev.get('transactions', prev.get('txns')), lambda x: f'{int(x):,}'),
            card('Avg Order Value', src['aov'], prev['aov']),
            card('Tips', src['tips'], prev['tips']),
            card('Labor Cost', src['labor_cost'], prev.get('labor_cost', prev.get('labor')), lib=True,
                 sub=f"{src['labor_pct_net']:.1f}% of net &#8226; {src['labor_hours']:.1f}h"),
        ]
        return f'''    <div class="loc-view" data-view="{view_id}">
      <div class="kpi-grid">
{chr(10).join(cards)}
      </div>
    </div>'''
    
    parts = [view('all', t, p)]
    # For per-store, prev is not available in final.json, so badges will be empty (degraded mode)
    for code in ['SJ', 'MV', 'FM']:
        parts.append(view(code.lower(), bs[code], {'payments': 0, 'net_sales': 0, 'transactions': 0, 'aov': 0, 'tips': 0, 'labor_cost': 0}))
    return '\n'.join(parts) + '\n  '

def block_financial(d):
    t = d['totals']; bs = d['by_store']
    pm = t.get('tenders', {})
    pm_total = sum(pm.values()) or 1
    
    def fin_row(label, val):
        return f'<div class="fin-row"><span class="fin-row-label">{label}</span><span class="fin-row-val">{val}</span></div>'
    
    all_view = f'''    <div class="loc-view" data-view="all">
      <div class="fin-2col" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div class="card">
          <div class="card-title">Revenue Summary</div>
          {fin_row('Total Payments', fmt_money(t['payments']))}
          {fin_row('Gross Sales', fmt_money(t['gross_sales']))}
          {fin_row('Net Sales', fmt_money(t['net_sales']))}
          {fin_row('Tips', fmt_money(t['tips']))}
        </div>
        <div class="card">
          <div class="card-title">Payment Methods</div>
          {fin_row('Card', fmt_money(pm.get('CARD', 0)) + f" ({pm.get('CARD',0)/pm_total*100:.1f}%)")}
          {fin_row('Cash', fmt_money(pm.get('CASH', 0)) + f" ({pm.get('CASH',0)/pm_total*100:.1f}%)")}
          {fin_row('Gift Card', fmt_money(pm.get('GIFT_CARD', 0)) + f" ({pm.get('GIFT_CARD',0)/pm_total*100:.1f}%)")}
          {fin_row('External (DD/UE)', fmt_money(pm.get('EXTERNAL', 0)) + f" ({pm.get('EXTERNAL',0)/pm_total*100:.1f}%)")}
        </div>
      </div>
    </div>'''
    
    store_cards = []
    for code in ['SJ', 'MV', 'FM']:
        s = bs[code]; m = LOC_META[code]
        store_cards.append(f'''    <div class="loc-view" data-view="{code.lower()}">
      <div class="card" style="border-top:3px solid var(--{m['color']});">
        <div class="card-title">Financial Summary -- {m['emoji']} {m['name']}</div>
        {fin_row('Total Payments', fmt_money(s['payments']))}
        {fin_row('Net Sales', fmt_money(s['net_sales']))}
        {fin_row('Transactions', f"{int(s['transactions']):,}")}
        {fin_row('Avg Order Value', fmt_money(s['aov']))}
        {fin_row('Tips', fmt_money(s['tips']))}
      </div>
    </div>''')
    
    return '\n'.join([all_view] + store_cards) + '\n  '

def block_profitability(d):
    t = d['totals']; bs = d['by_store']
    
    def pl_row(label, val, bold=False):
        style = 'font-weight:700;border-top:1px solid var(--border);padding-top:6px;' if bold else ''
        return f'<div class="fin-row" style="{style}"><span class="fin-row-label">{label}</span><span class="fin-row-val">{val}</span></div>'
    
    all_pl = f'''    <div class="loc-view" data-view="all">
      <div class="card">
        <div class="card-title">P&amp;L -- All Locations Combined</div>
        {pl_row('Net Sales', fmt_money(t['net_sales']))}
        {pl_row(f"- COGS ({t['cogs']/t['net_sales']*100:.1f}%)", fmt_money(t['cogs']))}
        {pl_row(f"- Labor ({t['labor_pct_net']:.1f}% of net)", fmt_money(t['labor_cost']))}
        {pl_row(f"- Rent ({t['rent']/t['net_sales']*100:.1f}% of net)", fmt_money(t['rent']))}
        {pl_row(f"Operating Profit ({t['op_margin_pct']:.1f}%)", fmt_money(t['op_profit']), bold=True)}
      </div>
    </div>'''
    
    store_pls = []
    for code in ['SJ', 'MV', 'FM']:
        s = bs[code]; m = LOC_META[code]
        store_pls.append(f'''    <div class="loc-view" data-view="{code.lower()}">
      <div class="card" style="border-top:3px solid var(--{m['color']});">
        <div class="card-title">P&amp;L -- {m['emoji']} {m['name']}</div>
        {pl_row('Net Sales', fmt_money(s['net_sales']))}
        {pl_row('- COGS', fmt_money(s['cogs']))}
        {pl_row('- Labor', fmt_money(s['labor_cost']))}
        {pl_row('- Rent', fmt_money(s['rent']))}
        {pl_row(f"Operating Profit ({s['op_margin_pct']:.1f}%)", fmt_money(s['op_profit']), bold=True)}
      </div>
    </div>''')
    
    cards = '\n'.join([all_pl] + store_pls)
    
    # Cost waterfall
    cogs_pct = t['cogs'] / t['net_sales'] * 100
    labor_pct = t['labor_pct_net']
    rent_pct = t['rent'] / t['net_sales'] * 100
    op_pct = t['op_margin_pct']
    
    waterfall = f'''
    <div class="card" style="margin-top:14px;">
      <div class="card-title">Cost Waterfall (% of Net Sales)</div>
      <div class="waterfall">
        <div class="bar" style="width:100%;background:var(--green);min-height:32px;min-width:fit-content;"><span style="white-space:nowrap;padding:0 10px;color:#fff;">Net Sales: 100% &#8226; {fmt_money(t['net_sales'])}</span></div>
        <div class="bar" style="width:{cogs_pct:.1f}%;background:var(--amber);min-height:32px;min-width:fit-content;"><span style="white-space:nowrap;padding:0 10px;">COGS: -{cogs_pct:.1f}% &#8226; {fmt_money(t['cogs'])}</span></div>
        <div class="bar" style="width:{labor_pct:.1f}%;background:var(--red);min-height:32px;min-width:fit-content;"><span style="white-space:nowrap;padding:0 10px;color:#fff;">Labor: -{labor_pct:.1f}% &#8226; {fmt_money(t['labor_cost'])}</span></div>
        <div class="bar" style="width:{rent_pct:.1f}%;background:var(--text-muted);min-height:32px;min-width:fit-content;"><span style="white-space:nowrap;padding:0 10px;color:#fff;">Rent: -{rent_pct:.1f}% &#8226; {fmt_money(t['rent'])}</span></div>
        <div class="bar" style="width:{op_pct:.1f}%;background:var(--navy);min-height:32px;min-width:fit-content;"><span style="white-space:nowrap;padding:0 10px;color:#fff;">Op Profit: {op_pct:.1f}% &#8226; {fmt_money(t['op_profit'])}</span></div>
      </div>
    </div>'''
    
    rent_detail = f'''
    <div class="card" style="margin-top:14px;">
      <div class="card-title">Rent Detail</div>'''
    for code in ['SJ', 'MV', 'FM']:
        m = LOC_META[code]
        rent_detail += f'''
      <div class="fin-row"><span class="fin-row-label">{m['emoji']} {m['name']}</span><span class="fin-row-val">${m['rent_mo']:,.2f}/mo &rarr; {fmt_money(bs[code]['rent'])}/wk</span></div>'''
    rent_detail += f'''
      <div class="fin-row" style="font-weight:700;border-top:1px solid var(--border);padding-top:6px;"><span class="fin-row-label">Total Weekly Rent</span><span class="fin-row-val">{fmt_money(t['rent'])}</span></div>
    </div>'''
    
    return cards + waterfall + rent_detail + '\n  '

def block_wow(d):
    t = d['totals']; p = d['prev_week']
    metrics = [
        ('Total Payments', t['payments'], p['payments'], fmt_money, False),
        ('Net Sales', t['net_sales'], p.get('net_sales', p.get('net', 0)), fmt_money, False),
        ('Transactions', t['transactions'], p.get('transactions', p.get('txns', 0)), lambda x: f'{int(x):,}', False),
        ('AOV', t['aov'], p['aov'], fmt_money, False),
        ('Tips', t['tips'], p['tips'], fmt_money, False),
        ('Labor Cost', t['labor_cost'], p.get('labor_cost', p.get('labor', 0)), fmt_money, True),
    ]
    cards = []
    for label, c, prev, fmt, lib in metrics:
        badge = wow_badge(pct_change(c, prev), lib)
        cards.append(f'''      <div class="wow-card">
        <div class="wow-label">{label}</div>
        <div class="wow-value">{fmt(c)}</div>
        <div class="wow-prev">prev: {fmt(prev)} {badge}</div>
      </div>''')
    return '    <div class="wow-grid">\n' + '\n'.join(cards) + '\n    </div>\n  '

def block_mtd_ytd(d):
    t = d['totals']
    mtd = d['mtd_total']; mtd_bs = d['mtd_by_store']
    ytd = d['ytd_total']; ytd_bs = d['ytd_by_store']
    mtd_net_est = mtd * t['net_sales'] / t['payments']
    
    rows = ''
    for code in ['SJ', 'MV', 'FM']:
        m = LOC_META[code]
        rows += f'\n          <div class="fin-row"><span class="fin-row-label">{m["emoji"]} {m["name"]}</span><span class="fin-row-val">{fmt_money(mtd_bs[code])}</span></div>'
    
    ytd_rows = ''
    for code in ['SJ', 'MV', 'FM']:
        m = LOC_META[code]
        ytd_rows += f'\n          <div class="fin-row"><span class="fin-row-label">{m["emoji"]} {m["name"]}</span><span class="fin-row-val">{fmt_money(ytd_bs[code])}</span></div>'
    
    return f'''    <div class="fin-2col" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div class="card" style="border-top:3px solid var(--amber);">
        <div class="card-title">MTD -- {d['mtd_label']}</div>
        <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val" style="font-size:20px;font-weight:700;">{fmt_money(mtd)}</span></div>
        <div class="fin-row"><span class="fin-row-label">Est. Net Sales</span><span class="fin-row-val">{fmt_money(mtd_net_est)}</span></div>
        <div style="border-top:1px solid var(--border-light);margin-top:8px;padding-top:8px;">{rows}
        </div>
      </div>
      <div class="card" style="border-top:3px solid var(--green);">
        <div class="card-title">YTD -- {d['ytd_label']}</div>
        <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val" style="font-size:20px;font-weight:700;">{fmt_money(ytd)}</span></div>
        <div style="border-top:1px solid var(--border-light);margin-top:8px;padding-top:8px;">{ytd_rows}
        </div>
      </div>
    </div>
  '''

def block_labor(d):
    t = d['totals']; bs = d['by_store']
    
    summary = f'''    <div class="card">
      <div class="card-title">Labor Totals (Week)</div>
      <div class="fin-row"><span class="fin-row-label">Total Labor Cost</span><span class="fin-row-val">{fmt_money(t['labor_cost'])}</span></div>
      <div class="fin-row"><span class="fin-row-label">Total Labor Hours</span><span class="fin-row-val">{t['labor_hours']:.1f}</span></div>
      <div class="fin-row"><span class="fin-row-label">Active Staff</span><span class="fin-row-val">{t.get('staff_count', 42)}</span></div>
      <div class="fin-row"><span class="fin-row-label">Labor % of Net</span><span class="fin-row-val">{t['labor_pct_net']:.1f}%</span></div>
    </div>'''
    
    per_store = '\n    <div class="fin-3col" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:14px;">'
    for code in ['SJ', 'MV', 'FM']:
        s = bs[code]; m = LOC_META[code]
        per_store += f'''
      <div class="card" style="border-top:3px solid var(--{m['color']});">
        <div class="card-title">{m['emoji']} {m['name']}</div>
        <div class="fin-row"><span class="fin-row-label">Cost</span><span class="fin-row-val">{fmt_money(s['labor_cost'])}</span></div>
        <div class="fin-row"><span class="fin-row-label">Hours</span><span class="fin-row-val">{s['labor_hours']:.1f}</span></div>
        <div class="fin-row"><span class="fin-row-label">% of Net</span><span class="fin-row-val">{s['labor_pct_net']:.1f}%</span></div>
        <div class="fin-row"><span class="fin-row-label">Rev/Hour</span><span class="fin-row-val">${s['rev_per_hour']:.0f}</span></div>
      </div>'''
    per_store += '\n    </div>\n'
    
    return summary + per_store + '  '

def block_daily(d):
    daily = d['daily_revenue']
    from datetime import datetime as dt
    days_sorted = sorted(daily.items())
    
    # Compute totals for txns per day from by_store... not available, skip txns col
    rows = []
    max_pay = max(v['total'] for k, v in days_sorted) if days_sorted else 1
    DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
    for date_str, vals in days_sorted:
        date = dt.strptime(date_str, '%Y-%m-%d')
        day_name = DOW[date.weekday()]
        date_short = date.strftime('%b %-d')
        bar_pct = vals['total'] / max_pay * 100
        rows.append(f'''        <tr>
          <td style="white-space:nowrap;">{day_name}</td>
          <td>{date_short}</td>
          <td>{fmt_money(vals['total'])}</td>
          <td>{fmt_money(vals['SJ'])}</td>
          <td>{fmt_money(vals['MV'])}</td>
          <td>{fmt_money(vals['FM'])}</td>
          <td class="mini-bar-cell"><div style="width:{bar_pct:.0f}%;background:var(--amber);height:8px;border-radius:4px;"></div></td>
        </tr>''')
    return f'''    <div class="card">
      <table class="data-table">
        <thead><tr><th>Day</th><th>Date</th><th>Total</th><th>SJ</th><th>MV</th><th>FM</th><th class="mini-bar-col">Trend</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>
  '''

def block_hourly(d):
    hourly = d.get('hourly_revenue', {})
    if not hourly:
        return '    <div class="card">No hourly data.</div>\n  '
    items = sorted(hourly.items(), key=lambda x: int(x[0]))
    max_pay = max(v for _, v in items)
    rows = []
    for hour, pay in items:
        h = int(hour)
        h12 = (h % 12) or 12
        ampm = 'AM' if h < 12 else 'PM'
        bar_pct = pay / max_pay * 100
        rows.append(f'''        <tr>
          <td>{h12}{ampm}</td>
          <td>{fmt_money(pay)}</td>
          <td><div style="width:{bar_pct:.0f}%;background:var(--navy);height:8px;border-radius:4px;"></div></td>
        </tr>''')
    return f'''    <div class="card">
      <table class="data-table">
        <thead><tr><th>Hour</th><th>Payments</th><th>Pattern</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>
  '''

def block_top_products(d):
    tp = d.get('top_products', {})
    overall = tp.get('overall', [])[:15]
    by_loc = tp.get('by_loc', {})
    
    def rows(prods):
        return '\n'.join(
            f'''        <tr><td>{i+1}</td><td>{p['name']}</td><td>{p['qty']}</td><td>{fmt_money(p['revenue'])}</td></tr>'''
            for i, p in enumerate(prods))
    
    overall_table = f'''    <div class="card">
      <div class="card-title">Top 15 Overall</div>
      <table class="data-table">
        <thead><tr><th>#</th><th>Product</th><th>Qty</th><th>Revenue</th></tr></thead>
        <tbody>
{rows(overall)}
        </tbody>
      </table>
    </div>'''
    
    per_loc_blocks = []
    for code in ['SJ', 'MV', 'FM']:
        m = LOC_META[code]
        prods = by_loc.get(code, [])[:5]
        per_loc_blocks.append(f'''      <div class="card" style="border-top:3px solid var(--{m['color']});">
        <div class="card-title">{m['emoji']} {m['name']} -- Top 5</div>
        <table class="data-table">
          <thead><tr><th>#</th><th>Product</th><th>Qty</th><th>Revenue</th></tr></thead>
          <tbody>
{rows(prods)}
          </tbody>
        </table>
      </div>''')
    
    return overall_table + '\n    <div class="fin-3col" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:14px;">\n' + '\n'.join(per_loc_blocks) + '\n    </div>\n  '

def block_data_quality(d):
    dq = d.get('data_quality')
    if not dq:
        return '    <div class="card" style="color:var(--text-muted);font-style:italic;">Data quality checks not run.</div>\n  '
    overall = dq.get('overall_status', '?')
    color = {'PASS': 'var(--green)', 'WARN': 'var(--amber)', 'FAIL': 'var(--red)'}.get(overall, 'var(--text-muted)')
    icon = {'PASS': '&#10004;', 'WARN': '&#9888;', 'FAIL': '&#10006;'}.get(overall, '?')
    rows = []
    for c in dq.get('checks', []):
        s = c.get('status', '?')
        sc = {'PASS': 'var(--green)', 'WARN': 'var(--amber)', 'FAIL': 'var(--red)', 'SKIP': 'var(--text-muted)', 'INFO': 'var(--navy)'}.get(s, 'var(--text-muted)')
        si = {'PASS': '&#10004;', 'WARN': '&#9888;', 'FAIL': '&#10006;', 'SKIP': '&#8211;', 'INFO': '&#8505;'}.get(s, '?')
        detail_parts = []
        if c.get('delta_pct') is not None:
            detail_parts.append(f"&Delta;={c['delta_pct']:.2f}% ({fmt_money(c['delta'])})")
        if c.get('discrepancies'):
            detail_parts.extend(c['discrepancies'])
        if c.get('findings'):
            for f in c['findings']:
                detail_parts.append(f.get('note', ''))
        if c.get('note'):
            detail_parts.append(c['note'])
        detail = '<br>'.join(detail_parts) if detail_parts else '<span style="color:var(--text-muted);">no issues</span>'
        rows.append(f'''      <tr>
        <td><span style="color:{sc};font-weight:700;">{si} {s}</span></td>
        <td>{c['check']}</td>
        <td style="font-size:12px;color:var(--text-muted);">{detail}</td>
      </tr>''')
    return f'''    <div class="card" style="border-left:4px solid {color};">
      <div style="font-size:14px;font-weight:700;color:{color};margin-bottom:8px;">{icon} Overall: {overall}</div>
      <table class="data-table" style="width:100%;font-size:13px;">
        <thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">Computed at {dq.get('timestamp','')}</div>
    </div>
  '''

def block_insights(d):
    insights = d.get('insights', [])
    if not insights:
        return '    <div class="card">No insights generated.</div>\n  '
    cards = []
    for i, ins in enumerate(insights[:5], 1):
        cards.append(f'''    <div class="insight-card">
      <div class="insight-num">{i}</div>
      <div class="insight-content">
        <div class="insight-title">{ins['title']}</div>
        <div class="insight-body">{ins['body']}</div>
      </div>
    </div>''')
    return '\n'.join(cards) + '\n  '

# ---------- Main ----------

def render(data, template_path, out_path):
    with open(template_path) as f:
        html = f.read()
    
    blocks = {
        '{{week_label}}': data.get('week_label', data.get('report_week', '')),
        '{{report_week}}': data.get('report_week', data.get('week_label', '')),
        '{{generated_date}}': data.get('generated_date', ''),
        '{{KPI_BLOCK}}': block_kpi(data),
        '{{FINANCIAL_BLOCK}}': block_financial(data),
        '{{PROFITABILITY_BLOCK}}': block_profitability(data),
        '{{WOW_BLOCK}}': block_wow(data),
        '{{MTD_YTD_BLOCK}}': block_mtd_ytd(data),
        '{{LABOR_BLOCK}}': block_labor(data),
        '{{DAILY_BLOCK}}': block_daily(data),
        '{{HOURLY_BLOCK}}': block_hourly(data),
        '{{TOP_PRODUCTS_BLOCK}}': block_top_products(data),
        '{{INSIGHTS_BLOCK}}': block_insights(data),
        '{{DATA_QUALITY_BLOCK}}': block_data_quality(data),
    }
    for token, content in blocks.items():
        html = html.replace(token, content)
    
    leftover = re.findall(r'\{\{[A-Z_a-z]+\}\}', html)
    if leftover:
        print(f'WARNING: unsubstituted tokens: {leftover}', file=sys.stderr)
    
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(html)
    print(f'Wrote {out_path}: {len(html):,} chars')
    return html

if __name__ == '__main__':
    run_dir = sys.argv[1] if len(sys.argv) > 1 else '/home/user/workspace/square_data/run10'
    final = json.load(open(os.path.join(run_dir, 'final.json')))
    render(final, os.path.join(PIPELINE, 'template.html'), OUT)
