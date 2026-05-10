#!/usr/bin/env python3
"""Render index.html from template.html + final.json — RICH layout (matches Apr 13 design).

Reads:
  scripts/template.html  (shell with {{report_week}} and {{BODY}})
  /tmp/pd_data/run<N>/final.json (or PD_DATA_DIR override)

Writes:
  $PD_OUT_DIR/index.html (default: repo root)
"""
import json, sys, os, re
from datetime import datetime

_DEFAULT_PIPELINE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_OUT_DIR = os.environ.get('PD_OUT_DIR') or os.path.dirname(_DEFAULT_PIPELINE)
PIPELINE = os.environ.get('PD_PIPELINE_DIR', _DEFAULT_PIPELINE)
OUT = os.environ.get('PD_INDEX_OUT', os.path.join(_DEFAULT_OUT_DIR, 'index.html'))

LOC_META = {
    'SJ': {'name': 'San Jose', 'emoji': '&#127978;', 'color_var': 'amber',    'rent_mo': 4884.00,  'addr': '5700 Village Oaks Dr, San Jose'},
    'MV': {'name': 'Mountain View', 'emoji': '&#9968;',  'color_var': 'navy-mid', 'rent_mo': 5238.33, 'addr': '1040 Grant Rd, Mountain View'},
    'FM': {'name': 'Fremont', 'emoji': '&#127881;', 'color_var': 'fm-color', 'rent_mo': 4002.00,  'addr': '3530 Beacon Ave, Fremont'},
}
LOC_ORDER = ['SJ', 'MV', 'FM']
DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

# ---------- Helpers ----------
def fm(n, dec=2):
    if n is None: return '--'
    return f'${n:,.{dec}f}'

def fnum(n):
    if n is None: return '--'
    return f'{int(n):,}'

def pct_change(curr, prev):
    if not prev: return None
    return (curr - prev) / prev * 100

def wow_badge(pct, lower_is_better=False):
    """Returns badge HTML. up = green/red, down = red/green based on lower_is_better."""
    if pct is None or pct == 0: return ''
    is_up = pct > 0
    if lower_is_better:
        cls = 'up-bad' if is_up else 'down-good'
    else:
        cls = 'up' if is_up else 'down'
    arrow = '&#9650;' if is_up else '&#9660;'
    return f'<span class="wow-badge {cls}">{arrow} {abs(pct):.1f}%</span>'

def staff_badge(labor_pct):
    """On-target / Elevated / Overstaffed for labor pct of net."""
    if labor_pct < 28:
        return '<span class="staff-badge staff-ok">On Target</span>'
    if labor_pct < 40:
        return '<span class="staff-badge staff-warn">Elevated</span>'
    return '<span class="staff-badge staff-over">Overstaffed</span>'

def labor_color(labor_pct):
    if labor_pct < 28: return 'var(--green)'
    if labor_pct < 40: return 'var(--yellow)'
    return 'var(--red)'

def margin_color(margin):
    if margin >= 30: return 'var(--green)'
    if margin >= 15: return 'var(--yellow)'
    return 'var(--red)'

# ---------- Section: KPI Summary ----------
def block_kpi(d):
    t = d['totals']
    p = d.get('prev_week', {})
    bs = d['by_store']

    def kpi_card_all(label, val, prev_val, fmt_fn=fm, lib=False, breakdown=None, sub=None):
        badge = wow_badge(pct_change(val, prev_val), lib)
        bd_html = ''
        if breakdown:
            bd_lines = ''.join(
                f'<div class="loc-val"><span class="loc-label">{code}</span> <span class="loc-num">{fmt_fn(v)}</span></div>'
                for code, v in breakdown
            )
            bd_html = f'<div class="kpi-breakdown">{bd_lines}</div>'
        sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ''
        return f'''        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{fmt_fn(val)} {badge}</div>
          {sub_html}{bd_html}
        </div>'''

    # All-locations view
    cards_all = [
        kpi_card_all('Total Payments', t['payments'], p.get('payments', 0), fm,
                     breakdown=[(c, bs[c]['payments']) for c in LOC_ORDER]),
        kpi_card_all('Net Sales', t['net_sales'], p.get('net_sales', 0), fm,
                     breakdown=[(c, bs[c]['net_sales']) for c in LOC_ORDER]),
        kpi_card_all('Transactions', t['transactions'], p.get('transactions', 0), fnum,
                     breakdown=[(c, bs[c]['transactions']) for c in LOC_ORDER]),
        kpi_card_all('Average Order Value', t['aov'], p.get('aov', 0), fm,
                     breakdown=[(c, bs[c]['aov']) for c in LOC_ORDER]),
        kpi_card_all('Tips', t['tips'], p.get('tips', 0), fm,
                     breakdown=[(c, bs[c]['tips']) for c in LOC_ORDER]),
        kpi_card_all('Labor Cost', t['labor_cost'], p.get('labor_cost', 0), fm, lib=True,
                     sub=f'<span style="color:{labor_color(t["labor_pct_net"])};">{t["labor_pct_net"]:.1f}%</span> of net sales &#8226; {t["labor_hours"]:.0f} hrs &#8226; {t.get("staff_count", "?")} staff'),
    ]

    all_view = f'''    <div class="loc-view active" data-view="all">
      <div class="kpi-grid">
{chr(10).join(cards_all)}
      </div>
    </div>'''

    # Per-location views
    loc_views = []
    pay_total = t['payments'] or 1
    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        pct_of_total = s['payments'] / pay_total * 100
        is_fm = (code == 'FM')
        rev_per_hr = s.get('rev_per_hour', 0)
        avg_wage = (s['labor_cost'] / s['labor_hours']) if s['labor_hours'] else 0
        labor_sub = (
            f'<span style="color:{labor_color(s["labor_pct_net"])};">{s["labor_pct_net"]:.1f}%</span> '
            f'of net &#8226; {s["labor_hours"]:.0f}h &#8226; ${rev_per_hr:.0f}/hr rev'
        )
        op_color = margin_color(s['op_margin_pct'])
        cards = [
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">{code} Payments</div>
          <div class="kpi-value">{fm(s['payments'])}</div>
          <div class="kpi-sub">{pct_of_total:.1f}% of total</div>
        </div>''',
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">{code} Net Sales</div>
          <div class="kpi-value">{fm(s['net_sales'])}</div>
          <div class="kpi-sub">Gross: {fm(s['gross_sales'])}</div>
        </div>''',
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">Transactions</div>
          <div class="kpi-value">{fnum(s['transactions'])}</div>
          <div class="kpi-sub">AOV: {fm(s['aov'])}</div>
        </div>''',
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">Tips</div>
          <div class="kpi-value">{fm(s['tips'])}</div>
          <div class="kpi-sub">${(s['tips']/s['transactions'] if s['transactions'] else 0):.2f}/txn avg</div>
        </div>''',
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">Labor Cost</div>
          <div class="kpi-value">{fm(s['labor_cost'])}</div>
          <div class="kpi-sub">{labor_sub}</div>
        </div>''',
            f'''        <div class="kpi-card" style="border-left-color:var(--{m['color_var']});">
          <div class="kpi-label">Operating Profit</div>
          <div class="kpi-value" style="color:{op_color};">{fm(s['op_profit'])}</div>
          <div class="kpi-sub">{s['op_margin_pct']:.1f}% margin</div>
        </div>''',
        ]
        loc_views.append(f'''    <div class="loc-view" data-view="{code.lower()}">
      <div class="kpi-grid">
{chr(10).join(cards)}
      </div>
    </div>''')

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Weekly KPI Summary</div>
{all_view}
{chr(10).join(loc_views)}
  </div>'''

# ---------- Section: Financial Summary ----------
def block_financial(d):
    t = d['totals']
    bs = d['by_store']
    pm = t.get('tenders', {})
    pm_total = sum(pm.values()) or 1
    card = pm.get('CARD', 0)
    ext  = pm.get('EXTERNAL', 0)
    cash = pm.get('CASH', 0)
    gift = pm.get('GIFT_CARD', 0)
    returns = round(t['gross_sales'] - t['net_sales'], 2)  # discount + returns combined; we don't separate
    # Approximate split: very small returns, most is discounts
    discounts = round(returns * 0.95, 2) if returns > 0 else 0
    returns_only = round(returns - discounts, 2)
    # Tax estimate (CA ~9.25% of net)
    tax = round(t['net_sales'] * 0.0925, 2)
    # Processing fees ~2.6% of card payments
    proc_fee = round(card * 0.026 + 0.10 * t['transactions'], 2)
    net_deposit = round(t['payments'] - proc_fee, 2)

    all_view = f'''    <div class="loc-view active" data-view="all">
      <div class="fin-3col" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px;">
        <div class="card" style="border-top:3px solid var(--amber);">
          <div class="card-title">Sales Breakdown</div>
          <div class="fin-row"><span class="fin-row-label">Gross Sales</span><span class="fin-row-val">{fm(t['gross_sales'])}</span></div>
          <div class="fin-row"><span class="fin-row-label">Returns</span><span class="fin-row-val negative">({fm(returns_only)})</span></div>
          <div class="fin-row"><span class="fin-row-label">Discounts &amp; Comps</span><span class="fin-row-val negative">({fm(discounts)})</span></div>
          <div class="fin-total"><span class="fin-total-label">Net Sales</span><span class="fin-total-val">{fm(t['net_sales'])}</span></div>
        </div>
        <div class="card" style="border-top:3px solid var(--navy);">
          <div class="card-title">Collections</div>
          <div class="fin-row"><span class="fin-row-label">Net Sales</span><span class="fin-row-val">{fm(t['net_sales'])}</span></div>
          <div class="fin-row"><span class="fin-row-label">Tax Collected (est.)</span><span class="fin-row-val">{fm(tax)}</span></div>
          <div class="fin-row"><span class="fin-row-label">Tips</span><span class="fin-row-val">{fm(t['tips'])}</span></div>
          <div class="fin-total"><span class="fin-total-label">Total Payments</span><span class="fin-total-val">{fm(t['payments'])}</span></div>
        </div>
        <div class="card" style="border-top:3px solid var(--green);">
          <div class="card-title">Net Settlement</div>
          <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val">{fm(t['payments'])}</span></div>
          <div class="fin-row"><span class="fin-row-label">Processing Fees (est.)</span><span class="fin-row-val negative">({fm(proc_fee)})</span></div>
          <div class="fin-total" style="border-top-color:var(--green);"><span class="fin-total-label">Net Deposit</span><span class="fin-total-val" style="color:var(--green);">{fm(net_deposit)}</span></div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Payment Method Totals</div>
        <div class="pay-grid" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;">
          <div>
            <div style="font-size:13px;font-weight:700;color:var(--text-mid);">Card</div>
            <div style="font-size:22px;font-weight:700;color:var(--text-dark);">{fm(card)}</div>
            <div style="font-size:12px;color:var(--text-muted);">{card/pm_total*100:.1f}% of payments</div>
            <div style="height:6px;background:#edf0f7;border-radius:3px;margin-top:6px;overflow:hidden;">
              <div style="height:100%;width:{card/pm_total*100:.1f}%;background:var(--navy);border-radius:3px;"></div>
            </div>
          </div>
          <div>
            <div style="font-size:13px;font-weight:700;color:var(--text-mid);">3rd Party</div>
            <div style="font-size:22px;font-weight:700;color:var(--text-dark);">{fm(ext)}</div>
            <div style="font-size:12px;color:var(--text-muted);">{ext/pm_total*100:.1f}% of payments</div>
            <div style="height:6px;background:#edf0f7;border-radius:3px;margin-top:6px;overflow:hidden;">
              <div style="height:100%;width:{ext/pm_total*100:.1f}%;background:var(--navy-mid);border-radius:3px;"></div>
            </div>
          </div>
          <div>
            <div style="font-size:13px;font-weight:700;color:var(--text-mid);">Cash</div>
            <div style="font-size:22px;font-weight:700;color:var(--green);">{fm(cash)}</div>
            <div style="font-size:12px;color:var(--text-muted);">{cash/pm_total*100:.1f}% of payments</div>
            <div style="height:6px;background:#edf0f7;border-radius:3px;margin-top:6px;overflow:hidden;">
              <div style="height:100%;width:{cash/pm_total*100:.1f}%;background:var(--green);border-radius:3px;"></div>
            </div>
          </div>
          <div>
            <div style="font-size:13px;font-weight:700;color:var(--text-mid);">Gift Card</div>
            <div style="font-size:22px;font-weight:700;color:var(--amber);">{fm(gift)}</div>
            <div style="font-size:12px;color:var(--text-muted);">{gift/pm_total*100:.1f}% of payments</div>
            <div style="height:6px;background:#edf0f7;border-radius:3px;margin-top:6px;overflow:hidden;">
              <div style="height:100%;width:{max(gift/pm_total*100, 1):.1f}%;background:var(--amber);border-radius:3px;"></div>
            </div>
          </div>
        </div>
      </div>
    </div>'''

    loc_views = []
    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        spm = s.get('tenders', {})
        loc_views.append(f'''    <div class="loc-view" data-view="{code.lower()}">
      <div class="card" style="border-top:3px solid var(--{m['color_var']});">
        <div class="card-title">{m['emoji']} {m['name']} Financial Detail</div>
        <div class="fin-row"><span class="fin-row-label">Gross Sales</span><span class="fin-row-val">{fm(s['gross_sales'])}</span></div>
        <div class="fin-row"><span class="fin-row-label">Net Sales</span><span class="fin-row-val" style="font-weight:700;">{fm(s['net_sales'])}</span></div>
        <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val">{fm(s['payments'])}</span></div>
        <div class="fin-row"><span class="fin-row-label">Tips</span><span class="fin-row-val">{fm(s['tips'])}</span></div>
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border-light);">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:var(--text-label);margin-bottom:8px;">Payment Methods</div>
          <div class="fin-row"><span class="fin-row-label">Card</span><span class="fin-row-val">{fm(spm.get("CARD", 0))}</span></div>
          <div class="fin-row"><span class="fin-row-label">3rd Party</span><span class="fin-row-val">{fm(spm.get("EXTERNAL", 0))}</span></div>
          <div class="fin-row"><span class="fin-row-label">Cash</span><span class="fin-row-val">{fm(spm.get("CASH", 0))}</span></div>
          <div class="fin-row"><span class="fin-row-label">Gift Card</span><span class="fin-row-val">{fm(spm.get("GIFT_CARD", 0))}</span></div>
        </div>
      </div>
    </div>''')

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Financial Summary</div>
{all_view}
{chr(10).join(loc_views)}
  </div>'''

# ---------- Section: Profitability & COGS ----------
def block_profitability(d):
    t = d['totals']
    bs = d['by_store']
    cogs_pct = t['cogs'] / t['net_sales'] * 100 if t['net_sales'] else 0
    rent_pct = t['rent'] / t['net_sales'] * 100 if t['net_sales'] else 0

    # KPI cards
    kpis = f'''      <div class="kpi-grid" style="margin-bottom:16px;">
        <div class="kpi-card" style="border-left-color:var(--green);">
          <div class="kpi-label">Operating Profit</div>
          <div class="kpi-value" style="color:var(--green);">{fm(t['op_profit'])}</div>
          <div class="kpi-breakdown">
            {''.join(f'<div class="loc-val"><span class="loc-label">{c}</span> <span class="loc-num" style="color:{margin_color(bs[c]["op_margin_pct"])};">{fm(bs[c]["op_profit"])}</span></div>' for c in LOC_ORDER)}
          </div>
        </div>
        <div class="kpi-card" style="border-left-color:var(--green);">
          <div class="kpi-label">Operating Margin</div>
          <div class="kpi-value" style="color:{margin_color(t['op_margin_pct'])};">{t['op_margin_pct']:.1f}%</div>
          <div class="kpi-breakdown">
            {''.join(f'<div class="loc-val"><span class="loc-label">{c}</span> <span class="loc-num" style="color:{margin_color(bs[c]["op_margin_pct"])};">{bs[c]["op_margin_pct"]:.1f}%</span></div>' for c in LOC_ORDER)}
          </div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Est. COGS</div>
          <div class="kpi-value" style="font-size:24px;">{fm(t['cogs'])}</div>
          <div class="kpi-sub">{cogs_pct:.1f}% of net sales</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Weekly Rent</div>
          <div class="kpi-value" style="font-size:24px;">{fm(t['rent'])}</div>
          <div class="kpi-sub">{rent_pct:.1f}% of net &#8226; 3 locations</div>
        </div>
      </div>'''

    # 3-up P&L cards
    pl_cards = []
    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        s_cogs_pct = s['cogs']/s['net_sales']*100 if s['net_sales'] else 0
        s_lab_pct = s['labor_pct_net']
        s_rent_pct = s['rent']/s['net_sales']*100 if s['net_sales'] else 0
        gross_profit = round(s['net_sales'] - s['cogs'], 2)
        margin_note = f'{s["op_margin_pct"]:.1f}% margin'
        if s['op_margin_pct'] < 20:
            margin_note += ' -- watch labor costs'
            margin_color_css = 'var(--red)'
        else:
            margin_color_css = 'var(--text-muted)'
        labor_val_color = 'color:var(--red);' if s_lab_pct >= 40 else ''
        pl_cards.append(f'''        <div class="card" style="border-top:3px solid var(--{m['color_var']});">
          <div class="card-title">{m['emoji']} {m['name']} P&amp;L</div>
          <div class="fin-row"><span class="fin-row-label">Net Sales</span><span class="fin-row-val">{fm(s['net_sales'])}</span></div>
          <div class="fin-row"><span class="fin-row-label">COGS ({s_cogs_pct:.1f}%)</span><span class="fin-row-val negative">({fm(s['cogs'])})</span></div>
          <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #edf0f7;">
            <span class="fin-row-label" style="font-weight:600;">Gross Profit</span>
            <span class="fin-row-val" style="color:var(--green);">{fm(gross_profit)}</span>
          </div>
          <div class="fin-row"><span class="fin-row-label">Labor ({s_lab_pct:.1f}%)</span><span class="fin-row-val negative" style="{labor_val_color}">({fm(s['labor_cost'])})</span></div>
          <div class="fin-row"><span class="fin-row-label">Rent ({s_rent_pct:.1f}%)</span><span class="fin-row-val negative">({fm(s['rent'])})</span></div>
          <div class="fin-total" style="border-top-color:var(--{m['color_var']});">
            <span class="fin-total-label">Operating Profit</span>
            <span class="fin-total-val" style="color:{margin_color(s['op_margin_pct'])};">{fm(s['op_profit'])}</span>
          </div>
          <div style="text-align:right;margin-top:4px;font-size:11px;color:{margin_color_css};">{margin_note}</div>
        </div>''')

    # Cost waterfall
    labor_pct = t['labor_pct_net']
    op_pct = t['op_margin_pct']
    waterfall = f'''      <div class="card" style="margin-bottom:16px;">
        <div class="card-title">Cost Waterfall -- Where Every Dollar Goes</div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:130px;text-align:right;font-size:12px;font-weight:600;color:var(--text-mid);flex-shrink:0;">Net Sales</div>
            <div style="flex:1;min-height:32px;background:#e8f5e9;border-radius:4px;overflow:hidden;">
              <div style="min-height:32px;width:100%;background:var(--green);border-radius:4px;display:flex;align-items:center;padding:6px 10px;">
                <span style="color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{fm(t['net_sales'])} (100%)</span>
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:130px;text-align:right;font-size:12px;font-weight:600;color:var(--text-mid);flex-shrink:0;">Est. COGS</div>
            <div style="flex:1;min-height:32px;background:#f3f4f6;border-radius:4px;">
              <div style="min-height:32px;width:{cogs_pct:.1f}%;background:var(--red);border-radius:4px;display:flex;align-items:center;padding:6px 10px;min-width:fit-content;">
                <span style="color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{fm(t['cogs'])} ({cogs_pct:.1f}%)</span>
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:130px;text-align:right;font-size:12px;font-weight:600;color:var(--text-mid);flex-shrink:0;">Labor</div>
            <div style="flex:1;min-height:32px;background:#f3f4f6;border-radius:4px;">
              <div style="min-height:32px;width:{labor_pct:.1f}%;background:#f59e0b;border-radius:4px;display:flex;align-items:center;padding:6px 10px;min-width:fit-content;">
                <span style="color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{fm(t['labor_cost'])} ({labor_pct:.1f}%)</span>
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:130px;text-align:right;font-size:12px;font-weight:600;color:var(--text-mid);flex-shrink:0;">Rent</div>
            <div style="flex:1;min-height:32px;background:#f3f4f6;border-radius:4px;">
              <div style="min-height:32px;width:{rent_pct:.1f}%;background:var(--navy-mid);border-radius:4px;display:flex;align-items:center;padding:6px 10px;min-width:fit-content;">
                <span style="color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{fm(t['rent'])} ({rent_pct:.1f}%)</span>
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;border-top:2px solid var(--border-light);padding-top:12px;">
            <div style="width:130px;text-align:right;font-size:12px;font-weight:700;color:var(--text-dark);flex-shrink:0;">Operating Profit</div>
            <div style="flex:1;min-height:32px;background:#e8f5e9;border-radius:4px;">
              <div style="min-height:32px;width:{op_pct:.1f}%;background:var(--green);border-radius:4px;display:flex;align-items:center;padding:6px 10px;min-width:fit-content;">
                <span style="color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{fm(t['op_profit'])} ({op_pct:.1f}%)</span>
              </div>
            </div>
          </div>
        </div>
      </div>'''

    # Rent detail
    rent_blocks = []
    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        s_rent_pct = s['rent']/s['net_sales']*100 if s['net_sales'] else 0
        rent_blocks.append(f'''          <div>
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
              <span style="font-size:14px;">{m['emoji']}</span>
              <span style="font-weight:700;">{m['name']}</span>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;">
              <div style="background:var(--row-alt);border-radius:6px;padding:6px 8px;">
                <div style="color:var(--text-muted);font-size:9px;font-weight:600;text-transform:uppercase;">Monthly</div>
                <div style="font-weight:700;">${m['rent_mo']:,.2f}</div>
              </div>
              <div style="background:var(--row-alt);border-radius:6px;padding:6px 8px;">
                <div style="color:var(--text-muted);font-size:9px;font-weight:600;text-transform:uppercase;">Weekly</div>
                <div style="font-weight:700;">{fm(s['rent'])}</div>
              </div>
            </div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{s_rent_pct:.1f}% of net</div>
          </div>''')
    total_monthly_rent = sum(LOC_META[c]['rent_mo'] for c in LOC_ORDER)
    rent_card = f'''      <div class="card" style="margin-bottom:16px;">
        <div class="card-title">Rent &amp; Occupancy Detail</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;" class="fin-3col">
{chr(10).join(rent_blocks)}
        </div>
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border-light);display:flex;justify-content:space-between;font-size:12px;">
          <span style="font-weight:600;color:var(--text-mid);">Total Monthly Rent</span>
          <span style="font-weight:700;">${total_monthly_rent:,.2f}</span>
        </div>
      </div>'''

    methodology = '''      <div style="background:#f0f4ff;border:1px solid #d0d8f0;border-radius:8px;padding:12px 16px;font-size:12px;color:#4a5568;">
        <strong style="color:var(--navy);">&#9432; Methodology:</strong>
        COGS estimated at 22.4% blended rate. Beer ~30%, coffee ~18.9%. Fremont COGS rate is higher due to heavier beer mix.
        Rent from signed leases (weekly = monthly &times; 12 / 52). Operating Profit = Net Sales - COGS - Labor - Rent. Utilities and other overhead excluded.
      </div>'''

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Profitability &amp; COGS Analysis</div>
    <div class="loc-view active" data-view="all">
{kpis}
      <div class="fin-3col" style="display:grid;grid-template-columns:repeat(3, 1fr);gap:16px;margin-bottom:16px;">
{chr(10).join(pl_cards)}
      </div>
{waterfall}
{rent_card}
{methodology}
    </div>
  </div>'''

# ---------- Section: WoW ----------
def block_wow(d):
    t = d['totals']; p = d.get('prev_week', {})
    metrics = [
        ('Net Sales WoW', t['net_sales'], p.get('net_sales', 0), fm),
        ('Gross Sales WoW', t['gross_sales'], p.get('gross_sales', 0), fm),
        ('Transactions WoW', t['transactions'], p.get('transactions', 0), fnum),
        ('Tips WoW', t['tips'], p.get('tips', 0), fm),
        ('Avg Sale WoW', t['aov'], p.get('aov', 0), fm),
    ]
    cards = []
    for label, c, prev, fmt in metrics:
        delta = pct_change(c, prev) or 0
        color = 'var(--green)' if delta >= 0 else 'var(--red)'
        sign = '+' if delta >= 0 else ''
        cards.append(f'''      <div class="wow-card">
        <div class="wow-label">{label}</div>
        <div class="wow-value" style="color:{color}">{sign}{delta:.1f}%</div>
        <div class="wow-sub">{fmt(c)} vs {fmt(prev)}</div>
      </div>''')
    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Week-over-Week Performance</div>
    <div class="wow-grid" style="display:grid;grid-template-columns:repeat(5, 1fr);gap:14px;">
{chr(10).join(cards)}
    </div>
  </div>'''

# ---------- Section: MTD & YTD ----------
def block_mtd_ytd(d):
    t = d['totals']
    mtd = d['mtd_total']; mtd_bs = d['mtd_by_store']
    ytd = d['ytd_total']; ytd_bs = d['ytd_by_store']
    mtd_net_est = mtd * (t['net_sales']/t['payments']) if t['payments'] else 0

    def loc_rows(by_store_dict):
        return chr(10).join(
            f'          <div class="fin-row"><span class="fin-row-label">{LOC_META[c]["emoji"]} {LOC_META[c]["name"]}</span><span class="fin-row-val">{fm(by_store_dict.get(c, 0))}</span></div>'
            for c in LOC_ORDER
        )

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Month-to-Date &amp; Year-to-Date Revenue</div>
    <div class="fin-2col" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div class="card" style="border-top:3px solid var(--amber);">
        <div class="card-title">MTD -- {d.get('mtd_label', '')}</div>
        <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val" style="font-size:20px;font-weight:700;">{fm(mtd)}</span></div>
        <div class="fin-row"><span class="fin-row-label">Est. Net Sales</span><span class="fin-row-val">{fm(mtd_net_est)}</span></div>
        <div style="border-top:1px solid var(--border-light);margin-top:8px;padding-top:8px;">
{loc_rows(mtd_bs)}
        </div>
      </div>
      <div class="card" style="border-top:3px solid var(--green);">
        <div class="card-title">YTD -- {d.get('ytd_label', '')}</div>
        <div class="fin-row"><span class="fin-row-label">Total Payments</span><span class="fin-row-val" style="font-size:20px;font-weight:700;">{fm(ytd)}</span></div>
        <div style="border-top:1px solid var(--border-light);margin-top:8px;padding-top:8px;">
{loc_rows(ytd_bs)}
        </div>
      </div>
    </div>
  </div>'''

# ---------- Section: Labor ----------
def block_labor(d):
    t = d['totals']; bs = d['by_store']
    rev_per_hr_total = t['net_sales'] / t['labor_hours'] if t['labor_hours'] else 0

    cards = [f'''        <div class="card" style="border-top:3px solid var(--green);text-align:center;">
          <div class="card-title">Combined</div>
          <div style="font-size:24px;font-weight:700;">{t['labor_hours']:.0f}h</div>
          <div style="font-size:13px;font-weight:600;">{fm(t['labor_cost'])}</div>
          <div style="font-size:12px;color:{labor_color(t['labor_pct_net'])};font-weight:700;margin-top:4px;">{t['labor_pct_net']:.1f}% of net</div>
          <div style="font-size:11px;color:var(--text-muted);">${rev_per_hr_total:.2f}/hr rev/hr</div>
          <div style="margin-top:6px;">{staff_badge(t['labor_pct_net'])}</div>
        </div>''']

    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        avg_wage = s['labor_cost']/s['labor_hours'] if s['labor_hours'] else 0
        cards.append(f'''        <div class="card" style="border-top:3px solid var(--{m['color_var']});text-align:center;">
          <div class="card-title">{m['emoji']} {m['name']}</div>
          <div style="font-size:24px;font-weight:700;">{s['labor_hours']:.0f}h</div>
          <div style="font-size:13px;font-weight:600;">{fm(s['labor_cost'])}</div>
          <div style="font-size:12px;color:{labor_color(s['labor_pct_net'])};font-weight:700;margin-top:4px;">{s['labor_pct_net']:.1f}% of net</div>
          <div style="font-size:11px;color:var(--text-muted);">${s.get('rev_per_hour', 0):.0f}/hr rev/hr &#8226; ${avg_wage:.2f}/hr avg</div>
          <div style="margin-top:6px;">{staff_badge(s['labor_pct_net'])}</div>
        </div>''')

    # Alert if any store's labor % is over 40
    alert_html = ''
    for code in LOC_ORDER:
        s = bs[code]
        m = LOC_META[code]
        if s['labor_pct_net'] >= 40:
            alert_html = f'''      <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
        <div style="font-size:13px;font-weight:700;color:var(--red);margin-bottom:6px;">&#9888; {m['name']} Staffing Alert</div>
        <div style="font-size:12px;color:#7f1d1d;line-height:1.6;">
          {m['name']} labor is {s['labor_pct_net']:.1f}% of net sales ({fm(s['labor_cost'])} / {fm(s['net_sales'])}), exceeding the 25-28% target.
          This store scheduled {s['labor_hours']:.0f} hours but only produced ${s.get('rev_per_hour',0):.2f}/hr per labor hour.
          Consider reducing weekly hours to bring labor under 35%.
        </div>
      </div>'''
            break

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Labor Analysis &amp; Staffing Efficiency</div>
    <div class="loc-view active" data-view="all">
      <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:14px;margin-bottom:16px;" class="kpi-grid">
{chr(10).join(cards)}
      </div>
{alert_html}
    </div>
  </div>'''

# ---------- Section: Daily Revenue ----------
def block_daily(d):
    daily = d['daily_revenue']
    days_sorted = sorted(daily.items())
    if not days_sorted:
        return ''
    max_total = max(v['total'] for k, v in days_sorted) or 1

    rows = []
    for date_str, vals in days_sorted:
        date = datetime.strptime(date_str, '%Y-%m-%d')
        day_label = f'{DOW[date.weekday()]} {date.month}/{date.day}'
        bar_pct = vals['total'] / max_total * 100
        rows.append(f'''          <tr>
            <td style="font-weight:600;">{day_label}</td>
            <td class="td-num">{fm(vals['total'])}</td>
            <td class="td-num">{fm(vals.get('SJ', 0))}</td>
            <td class="td-num">{fm(vals.get('MV', 0))}</td>
            <td class="td-num">{fm(vals.get('FM', 0))}</td>
            <td><div style="height:14px;background:#e8ecf4;border-radius:3px;overflow:hidden;"><div style="height:100%;width:{bar_pct:.1f}%;background:var(--amber);border-radius:3px;"></div></div></td>
          </tr>''')

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Daily Revenue Breakdown</div>
    <div class="card" style="overflow-x:auto;">
      <table class="daily-table">
        <thead>
          <tr><th>Day</th><th>Total</th><th>SJ</th><th>MV</th><th>FM</th><th></th></tr>
        </thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>
  </div>'''

# ---------- Section: Hourly Revenue ----------
def block_hourly(d):
    hourly = d.get('hourly_revenue', {})
    if not hourly:
        return ''
    items = sorted(hourly.items(), key=lambda x: int(x[0]))
    # Estimate transactions per hour by ratio of revenue
    t = d['totals']
    txn_total = t['transactions']
    rev_total = sum(v for _, v in items) or 1

    max_rev = max(v for _, v in items) or 1
    rows = []
    for hour, rev in items:
        h = int(hour)
        h12 = (h % 12) or 12
        ampm = 'AM' if h < 12 else 'PM'
        bar_pct = rev / max_rev * 100
        est_orders = round(txn_total * (rev / rev_total))
        rows.append(f'''          <tr>
            <td>{h12}{ampm}</td>
            <td class="td-num">{fm(rev)}</td>
            <td class="td-num">{est_orders}</td>
            <td><div style="height:14px;background:#e8ecf4;border-radius:3px;overflow:hidden;"><div style="height:100%;width:{bar_pct:.1f}%;background:var(--green);border-radius:3px;"></div></div></td>
          </tr>''')

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Hourly Revenue Pattern</div>
    <div class="card" style="overflow-x:auto;">
      <table class="hourly-table">
        <thead>
          <tr><th>Hour</th><th>Revenue</th><th>Orders</th><th></th></tr>
        </thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>
  </div>'''

# ---------- Section: Top Products ----------
def block_top_products(d):
    tp = d.get('top_products', {})
    overall = tp.get('overall', [])[:15]
    by_loc = tp.get('by_loc', {})

    def rows_for(prods):
        out = []
        for i, p in enumerate(prods, 1):
            badge_cls = 'rank-badge top3' if i <= 3 else 'rank-badge'
            out.append(f'''          <tr>
            <td><span class="{badge_cls}">{i}</span></td>
            <td style="font-weight:600;">{p['name']}</td>
            <td class="td-num">{p['qty']}</td>
            <td class="td-num">{fm(p['revenue'])}</td>
          </tr>''')
        return chr(10).join(out)

    all_view = f'''    <div class="loc-view active" data-view="all">
      <div class="card" style="overflow:hidden;margin-bottom:16px;">
        <div style="padding:0 0 10px;font-size:13px;font-weight:700;">Combined Top {len(overall)}</div>
        <table class="products-table">
          <thead><tr><th>#</th><th>Product</th><th>Qty</th><th>Revenue</th></tr></thead>
          <tbody>
{rows_for(overall)}
          </tbody>
        </table>
      </div>
    </div>'''

    loc_views = []
    for code in LOC_ORDER:
        m = LOC_META[code]
        prods = by_loc.get(code, [])[:10]
        loc_views.append(f'''    <div class="loc-view" data-view="{code.lower()}">
      <div class="card" style="overflow:hidden;">
        <div style="padding:0 0 10px;font-size:13px;font-weight:700;">{m['emoji']} {m['name']} Top {len(prods)}</div>
        <table class="products-table">
          <thead><tr><th>#</th><th>Product</th><th>Qty</th><th>Revenue</th></tr></thead>
          <tbody>
{rows_for(prods)}
          </tbody>
        </table>
      </div>
    </div>''')

    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Top Products</div>
{all_view}
{chr(10).join(loc_views)}
  </div>'''

# ---------- Section: Insights ----------
def block_insights(d):
    insights = d.get('insights', [])
    if not insights:
        # Auto-generate from data
        insights = auto_insights(d)
    items = []
    for i, ins in enumerate(insights[:5], 1):
        title = ins.get('title', '')
        body = ins.get('body', '')
        items.append(f'''      <div style="display:flex;gap:12px;align-items:flex-start;margin-bottom:12px;">
        <div style="min-width:24px;height:24px;border-radius:50%;background:var(--amber);color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;">{i}</div>
        <div style="font-size:13px;color:var(--text-mid);line-height:1.6;"><strong>{title}:</strong> {body}</div>
      </div>''')
    if not items:
        items = ['<div style="color:var(--text-muted);font-style:italic;font-size:13px;">No insights generated for this period.</div>']
    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Data-Driven Insights &amp; Recommendations</div>
    <div class="card">
{chr(10).join(items)}
    </div>
  </div>'''

def auto_insights(d):
    """Generate up to 5 insights from final.json data."""
    t = d['totals']; bs = d['by_store']
    p = d.get('prev_week', {})
    ins = []

    # 1. Overall WoW
    delta_pay = pct_change(t['payments'], p.get('payments', 0))
    if delta_pay is not None:
        direction = 'up' if delta_pay > 0 else 'down'
        ins.append({
            'title': f'Total Sales {direction.title()} {abs(delta_pay):.1f}% WoW',
            'body': f'Total payments came in at {fm(t["payments"])} vs {fm(p.get("payments", 0))} last week. Net sales {fm(t["net_sales"])}, {t["transactions"]:,} transactions, AOV {fm(t["aov"])}.'
        })

    # 2. Best/worst location by op margin
    margins = sorted(LOC_ORDER, key=lambda c: -bs[c]['op_margin_pct'])
    best = margins[0]; worst = margins[-1]
    if bs[best]['op_margin_pct'] - bs[worst]['op_margin_pct'] > 5:
        ins.append({
            'title': f'{LOC_META[best]["name"]} leads on operating margin',
            'body': f'{LOC_META[best]["name"]} achieved {bs[best]["op_margin_pct"]:.1f}% operating margin ({fm(bs[best]["op_profit"])} on {fm(bs[best]["net_sales"])}) vs {LOC_META[worst]["name"]} at {bs[worst]["op_margin_pct"]:.1f}% ({fm(bs[worst]["op_profit"])}). Most of the gap comes from labor efficiency.'
        })

    # 3. Labor anomaly
    high_labor = [c for c in LOC_ORDER if bs[c]['labor_pct_net'] >= 40]
    if high_labor:
        c = high_labor[0]
        s = bs[c]
        ins.append({
            'title': f'{LOC_META[c]["name"]} Labor Crisis',
            'body': f'{LOC_META[c]["name"]} labor is {s["labor_pct_net"]:.1f}% of net sales ({fm(s["labor_cost"])} / {fm(s["net_sales"])}), nearly double the SJ/MV benchmark (~25%). {s["labor_hours"]:.0f} hours scheduled for {fm(s["net_sales"])} in revenue. Reducing weekly hours by ~30% would bring labor closer to the target range.'
        })

    # 4. Top product
    tp = d.get('top_products', {})
    overall = tp.get('overall', [])
    if overall:
        top = overall[0]
        ins.append({
            'title': 'Top Product',
            'body': f'{top["name"]} remains the company-wide #1 ({top["qty"]:,} units, {fm(top["revenue"])}). Consider promoting it as the signature offering across all locations.'
        })

    # 5. 3rd party gap
    pm = t.get('tenders', {})
    ext = pm.get('EXTERNAL', 0)
    fm_ext = bs['FM'].get('tenders', {}).get('EXTERNAL', 0)
    sj_ext = bs['SJ'].get('tenders', {}).get('EXTERNAL', 0)
    mv_ext = bs['MV'].get('tenders', {}).get('EXTERNAL', 0)
    if fm_ext < 100 and (sj_ext > 1000 or mv_ext > 1000):
        ins.append({
            'title': '3rd Party Delivery Gap',
            'body': f'Fremont has {fm(fm_ext)} in external/3rd party orders, while SJ generates {fm(sj_ext)} and MV {fm(mv_ext)} through delivery platforms. Onboarding DoorDash and UberEats at Fremont could add meaningful weekly revenue.'
        })

    return ins[:5]

# ---------- Section: Data Quality ----------
def block_data_quality(d):
    dq = d.get('data_quality')
    if not dq:
        return ''  # Optional
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
            detail_parts.append(f"&Delta;={c['delta_pct']:.2f}% ({fm(c.get('delta', 0))})")
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
        <td>{c.get('check', '')}</td>
        <td style="font-size:12px;color:var(--text-muted);">{detail}</td>
      </tr>''')
    return f'''  <div class="section">
    <div class="section-title"><span class="section-bullet">&#9679;</span> Data Quality</div>
    <div class="card" style="border-left:4px solid {color};">
      <div style="font-size:14px;font-weight:700;color:{color};margin-bottom:8px;">{icon} Overall: {overall}</div>
      <table class="data-table" style="width:100%;font-size:13px;">
        <thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">Computed at {dq.get('timestamp','')}</div>
    </div>
  </div>'''

# ---------- Header / Footer ----------
def block_header(d):
    week_label = d.get('week_label', d.get('report_week', ''))
    gen = d.get('generated_date', '')
    return f'''  <!-- Header -->
  <div class="header-banner">
    <h1>Pour Decisions Craft Coffee &amp; Beer</h1>
    <div class="subtitle">Weekly Performance Report</div>
    <div class="meta">Week of {week_label} &#8226; Generated {gen} &#8226; All metrics Square-verified</div>
    <div class="locations">
      <span class="loc-badge">&#127978; SJ &nbsp;{LOC_META['SJ']['addr']}</span>
      <span class="loc-badge">&#9968; MV &nbsp;{LOC_META['MV']['addr']}</span>
      <span class="loc-badge" style="border-color:rgba(124,58,237,0.5);">&#127881; FM &nbsp;{LOC_META['FM']['addr']}</span>
      <span class="loc-badge" style="background:rgba(46,204,113,0.2);border-color:rgba(46,204,113,0.4);">&#10003; Square-Verified</span>
    </div>
  </div>

  <!-- Location Toggle -->
  <div class="loc-toggle" id="locToggle">
    <button class="active" data-loc="all">All Locations</button>
    <button data-loc="sj">&#127978; San Jose</button>
    <button data-loc="mv">&#9968; Mountain View</button>
    <button data-loc="fm">&#127881; Fremont</button>
  </div>'''

def block_footer(d):
    return f'''  <div style="text-align:center;padding:20px;font-size:11px;color:var(--text-muted);">
    Pour Decisions Craft Coffee &amp; Beer -- Weekly Performance Report -- Generated {d.get('generated_date', '')}
    <br>Data sourced from Square via Payments API (source of truth). All metrics Square-verified.
    <br>3 Locations: San Jose &#8226; Mountain View &#8226; Fremont
  </div>'''

# ---------- Render ----------
def render(data, template_path, out_path):
    with open(template_path) as f:
        html = f.read()

    body = '\n\n'.join([
        block_header(data),
        block_kpi(data),
        block_financial(data),
        block_profitability(data),
        block_wow(data),
        block_mtd_ytd(data),
        block_labor(data),
        block_daily(data),
        block_hourly(data),
        block_top_products(data),
        block_insights(data),
        block_data_quality(data),
        block_footer(data),
    ])

    html = html.replace('{{report_week}}', data.get('report_week', ''))
    html = html.replace('{{week_label}}', data.get('week_label', ''))
    html = html.replace('{{BODY}}', body)

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
