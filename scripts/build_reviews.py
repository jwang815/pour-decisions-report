#!/usr/bin/env python3
"""Render reviews.html from reviews_template.html + reviews_data.json.

Renders all individual reviews returned by the API, with:
  - source badge (Google/Yelp)
  - rating-color border
  - filter buttons (all / 5★ / 4★ / 3★ / 2★ / 1★ / Google / Yelp)
  - verify-address banner per location (PASS/FAIL based on collector)

USAGE:
  python3 build_reviews.py <run_dir>
  e.g. python3 build_reviews.py /home/user/workspace/square_data/run11
"""
import json, sys, os, re, html

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(PIPELINE_DIR, 'reviews_template.html')
OUT_DEFAULT = os.path.join(os.path.dirname(PIPELINE_DIR), 'reviews.html')

LOC_META = {
    'sj': {'name': 'San Jose', 'emoji': '&#129378;', 'expected_addr': '5700 Village Oaks',
           'subtitle': 'Flagship location'},
    'mv': {'name': 'Mountain View', 'emoji': '&#9968;', 'expected_addr': '1040 Grant',
           'subtitle': 'Opened Jan 2026'},
    'fm': {'name': 'Fremont', 'emoji': '&#11088;', 'expected_addr': '3530 Beacon',
           'subtitle': 'Opened Apr 11, 2026'},
}

def esc(s):
    return html.escape(str(s or ''), quote=True)

def stars(rating):
    rating = int(rating or 0)
    full = '&#9733;' * rating
    empty = '&#9734;' * (5 - rating)
    return full + empty

def review_card(r, source, section_id):
    rating = int(r.get('rating') or 0) or 5
    name = esc(r.get('name', f'{source} Reviewer'))
    date_str = esc(r.get('date', ''))
    text = esc((r.get('text') or '').strip())
    if not text:
        return ''
    url = r.get('url', '')
    link = (f'<a href="{esc(url)}" target="_blank" rel="noopener" '
            f'style="font-size:11px;color:var(--text-muted);text-decoration:none;'
            f'margin-left:6px;">[link]</a>') if url else ''
    return f'''        <div class="review-card r{rating}" data-section="{section_id}" data-rating="{rating}" data-source="{source.lower()}">
          <div class="review-header">
            <div>
              <span class="review-name">{name}</span>
              <span class="source-badge {source.lower()}">{source}</span>
            </div>
            <div class="review-stars" title="{rating}/5">{stars(rating)}</div>
          </div>
          <div class="review-meta">{date_str}{link}</div>
          <div class="review-text">{text}</div>
        </div>'''

def block_overview(d):
    g = d['google']; y = d['yelp']
    g_total = sum((g.get(k, {}).get('count') or 0) for k in ['sj','mv','fm'])
    y_total = sum((y.get(k, {}).get('count') or 0) for k in ['sj','mv','fm'])
    total = g_total + y_total
    fm_total = (g.get('fm', {}).get('count') or 0) + (y.get('fm', {}).get('count') or 0)
    fm_g_rating = g.get('fm', {}).get('rating') or '—'
    return f'''    <div class="overview-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;">
      <div class="card">
        <div class="card-title">Total Reviews</div>
        <div style="font-size:32px;font-weight:700;">{total}</div>
        <div style="font-size:11px;color:var(--text-muted);">Across all sources &amp; locations</div>
      </div>
      <div class="card">
        <div class="card-title">Google Reviews</div>
        <div style="font-size:28px;font-weight:700;">{g_total}</div>
        <div style="font-size:11px;color:var(--text-muted);">SJ {g.get('sj',{}).get('count',0)} &#8226; MV {g.get('mv',{}).get('count',0)} &#8226; FM {g.get('fm',{}).get('count',0)}</div>
      </div>
      <div class="card">
        <div class="card-title">Yelp Reviews</div>
        <div style="font-size:28px;font-weight:700;">{y_total}</div>
        <div style="font-size:11px;color:var(--text-muted);">SJ {y.get('sj',{}).get('count',0)} &#8226; MV {y.get('mv',{}).get('count',0)} &#8226; FM {y.get('fm',{}).get('count',0)}</div>
      </div>
      <div class="card">
        <div class="card-title">Fremont Reviews</div>
        <div style="font-size:28px;font-weight:700;color:var(--fm-color);">{fm_total}</div>
        <div style="font-size:11px;color:var(--text-muted);">{fm_g_rating}&#9733; on Google &#8226; opened Apr 11, 2026</div>
      </div>
    </div>
'''

def block_ratings(d):
    g = d['google']; y = d['yelp']
    blocks = []
    for code in ['sj','mv','fm']:
        m = LOC_META[code]
        gs = g.get(code, {}); ys = y.get(code, {})
        g_rating = gs.get('rating') if gs.get('rating') is not None else '—'
        y_rating = ys.get('rating') if ys.get('rating') is not None else '—'
        g_count = gs.get('count', 0)
        y_count = ys.get('count', 0)
        total = g_count + y_count
        ratings_for_avg = [r for r in [gs.get('rating'), ys.get('rating')] if r is not None]
        avg = sum(ratings_for_avg) / len(ratings_for_avg) if ratings_for_avg else None
        avg_str = f'{avg:.2f}&#9733;' if avg is not None else '—'
        sub = (f"Opened Apr 11, 2026 &mdash; {total} total reviews, avg {avg_str}"
               if code == 'fm' else f"{total} total reviews &#8226; {m['subtitle']}")
        sub_color = 'var(--fm-color)' if code == 'fm' else 'var(--text-muted)'
        
        # API verification status
        g_verified = gs.get('address_verified', False)
        y_verified = ys.get('address_verified', False)
        g_addr = gs.get('address', '—')
        y_addr = ys.get('address', '—')
        
        verify_lines = []
        if gs:
            ok = '&#10004;' if g_verified else '&#10006;'
            cls = '' if g_verified else ' fail'
            verify_lines.append(f'<div class="verify-banner{cls}">{ok} Google: <code>{esc(g_addr)}</code></div>')
        if ys:
            ok = '&#10004;' if y_verified else '&#10006;'
            cls = '' if y_verified else ' fail'
            verify_lines.append(f'<div class="verify-banner{cls}">{ok} Yelp: <code>{esc(y_addr)}</code></div>')
        
        blocks.append(f'''      <div class="card">
        <div class="card-title">{m['emoji']} {m['name']}</div>
        <div style="display:flex;gap:24px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px;">
          <div>
            <div style="font-size:11px;color:var(--text-muted);">Google</div>
            <div style="font-size:28px;font-weight:700;color:var(--amber);">{g_rating}&#9733;</div>
            <div style="font-size:11px;color:var(--text-muted);">{g_count:,} reviews</div>
          </div>
          <div>
            <div style="font-size:11px;color:var(--text-muted);">Yelp</div>
            <div style="font-size:28px;font-weight:700;color:var(--red);">{y_rating}&#9733;</div>
            <div style="font-size:11px;color:var(--text-muted);">{y_count:,} reviews</div>
          </div>
          <div style="margin-left:auto;text-align:right;">
            <div style="font-size:11px;color:var(--text-muted);">Combined avg</div>
            <div style="font-size:22px;font-weight:700;">{avg_str}</div>
          </div>
        </div>
        {''.join(verify_lines)}
        <div style="margin-top:8px;font-size:12px;color:{sub_color};">{sub}</div>
      </div>''')
    return ('    <div class="ratings-grid" style="display:grid;'
            'grid-template-columns:repeat(3,1fr);gap:14px;">\n'
            + '\n'.join(blocks) + '\n    </div>\n')

def block_recent(d, code):
    section_id = f'reviews-{code}'
    g_revs = d['google'].get(code, {}).get('reviews', [])
    y_revs = d['yelp'].get(code, {}).get('reviews', [])
    
    cards = []
    for r in g_revs:
        c = review_card(r, 'Google', section_id)
        if c: cards.append(c)
    for r in y_revs:
        c = review_card(r, 'Yelp', section_id)
        if c: cards.append(c)
    
    if not cards:
        return '    <div class="card" style="color:var(--text-muted);font-style:italic;">No individual reviews available from APIs (rating &amp; counts above are exhaustive — full reviewer text requires API limits).</div>\n'
    
    # Filter bar
    rating_counts = {}
    for r in g_revs + y_revs:
        if r.get('text'):
            rating_counts[int(r.get('rating') or 0)] = rating_counts.get(int(r.get('rating') or 0), 0) + 1
    
    g_count_with_text = sum(1 for r in g_revs if r.get('text'))
    y_count_with_text = sum(1 for r in y_revs if r.get('text'))
    total = g_count_with_text + y_count_with_text
    
    filter_buttons = [f'<button data-filter="all" class="active">All ({total})</button>']
    if g_count_with_text:
        filter_buttons.append(f'<button data-filter="google">Google ({g_count_with_text})</button>')
    if y_count_with_text:
        filter_buttons.append(f'<button data-filter="yelp">Yelp ({y_count_with_text})</button>')
    for star in [5, 4, 3, 2, 1]:
        if rating_counts.get(star):
            filter_buttons.append(f'<button data-filter="{star}">{star}&#9733; ({rating_counts[star]})</button>')
    
    return (f'    <div class="filter-bar" data-filter-group="{section_id}">'
            + ''.join(filter_buttons) + '</div>\n'
            + '\n'.join(cards) + '\n  ')

def render(data, template_path, out_path):
    with open(template_path) as f:
        h = f.read()
    blocks = {
        '{{generated_date}}': data.get('generated_date', ''),
        '{{OVERVIEW_BLOCK}}': block_overview(data),
        '{{RATINGS_BLOCK}}': block_ratings(data),
        '{{SJ_REVIEWS_BLOCK}}': block_recent(data, 'sj'),
        '{{MV_REVIEWS_BLOCK}}': block_recent(data, 'mv'),
        '{{FM_REVIEWS_BLOCK}}': block_recent(data, 'fm'),
    }
    for k, v in blocks.items():
        h = h.replace(k, v)
    leftover = re.findall(r'\{\{[A-Z_a-z]+\}\}', h)
    if leftover:
        print(f'WARNING: unsubstituted tokens: {leftover}', file=sys.stderr)
    with open(out_path, 'w') as f:
        f.write(h)
    print(f'Wrote {out_path}: {len(h):,} chars')

if __name__ == '__main__':
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        run_dir = sys.argv[1]
        data_path = os.path.join(run_dir, 'reviews_data.json')
    else:
        data_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PIPELINE_DIR, '../reviews_data.json')
    
    if not os.path.exists(data_path):
        print(f'ERROR: {data_path} not found. Run collect_reviews_api.py first.')
        sys.exit(1)
    data = json.load(open(data_path))
    out = os.environ.get('REVIEWS_OUT', OUT_DEFAULT)
    render(data, TEMPLATE, out)
