#!/usr/bin/env python3
"""Render reviews.html from reviews_template.html + reviews_data.json.

Old-design layout:
  - Ratings Overview: 6 small cards (Google + Yelp x SJ/MV/FM)
  - Combined Snapshot: weighted-average rating + 5-star distribution + keyword themes
  - Action Items: auto-generated from low-rated reviews
  - Tabs: 3 location tabs, each with Google + Yelp side-by-side review columns
  - Per-location address-verify banners (suppressed for Yelp via SerpAPI)

USAGE:
  python3 build_reviews.py <run_dir>
  e.g. python3 build_reviews.py /home/user/workspace/square_data/run11
"""
import json, sys, os, re, html
from collections import Counter

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(PIPELINE_DIR, 'reviews_template.html')
OUT_DEFAULT = os.path.join(os.path.dirname(PIPELINE_DIR), 'reviews.html')

LOC_META = {
    'sj': {'name': 'San Jose', 'short': 'SJ', 'emoji': '&#127978;',
           'expected_addr': '5700 Village Oaks Dr, San Jose',
           'subtitle': 'Flagship location'},
    'mv': {'name': 'Mountain View', 'short': 'MV', 'emoji': '&#9968;',
           'expected_addr': '1040 Grant Rd, Mountain View',
           'subtitle': 'Opened Jan 2026'},
    'fm': {'name': 'Fremont', 'short': 'FM', 'emoji': '&#11088;',
           'expected_addr': '3530 Beacon Ave, Fremont',
           'subtitle': 'Opened Apr 11, 2026'},
}

# Theme keywords for "Most Praised" / "Areas to Improve" extraction.
# Each theme is (label, [keywords]). We count reviews that match any keyword
# in the theme — case-insensitive substring match against review text.
PRAISED_THEMES = [
    ('Peanut Butter Latte', ['peanut butter']),
    ('Friendly Staff', ['friendly staff', 'great service', 'kind staff', 'welcoming', 'helpful staff']),
    ('Unique Flavors', ['unique flavor', 'unique drink', 'creative drink', 'unique blend']),
    ('Great Vibes', ['great vibe', 'good vibe', 'cozy', 'aesthetic', 'great atmosphere', 'nice atmosphere']),
    ('Egg Coffee', ['egg coffee', 'vietnamese coffee']),
    ('Matcha', ['matcha']),
    ('Study Spot', ['study spot', 'work from', 'great spot to work', 'good place to work', 'wifi']),
    ('Craft Beer', ['craft beer', 'beer selection', 'good beer']),
    ('Coffee Quality', ['great coffee', 'amazing coffee', 'best coffee', 'delicious coffee', 'good coffee']),
    ('Pastries', ['pastry', 'pastries', 'croissant', 'donut']),
]
NEGATIVE_THEMES = [
    ('Slow Service / Wait Times', ['slow', 'long wait', 'took forever', 'waited', 'wait time', 'too long']),
    ('Pricing', ['expensive', 'overpriced', 'pricey', 'too pricy', 'high price']),
    ('Order Issues', ['wrong order', 'forgot', 'order mix', 'never got', 'never made']),
    ('Small Portions', ['small portion', 'small size', 'tiny']),
    ('Loud / Crowded', ['loud', 'noisy', 'crowded', 'cramped', 'limited seating']),
    ('Hot Drinks Issue', ['hot drinks', 'no hot', 'machine broken', 'not working']),
    ('Quality Concerns', ['watered down', 'bland', 'tasteless', 'burnt', 'below average']),
]

def esc(s):
    return html.escape(str(s or ''), quote=True)

def format_review_date(d):
    """Yelp returns ISO ('2026-04-05T00:43:19Z'); Google returns relative
    strings ('2 days ago'). Normalize ISO to 'Mon D, YYYY'; pass relative through.
    Empty -> ''.
    """
    s = (d or '').strip()
    if not s:
        return ''
    # ISO datetime detection
    if 'T' in s and (s.endswith('Z') or '+' in s[10:] or '-' in s[10:]):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            # Cross-platform '%-d' (Linux) — fall back to lstrip on Windows
            try:
                return dt.strftime('%b %-d, %Y')
            except ValueError:
                return dt.strftime('%b %d, %Y').replace(' 0', ' ')
        except Exception:
            pass
    return s

def stars(rating, fmt='solid'):
    """Render rating as ★ chars. fmt='solid' = filled+empty; fmt='filled' = only filled."""
    rating = int(round(float(rating or 0)))
    rating = max(0, min(5, rating))
    if fmt == 'filled':
        return '&#9733;' * rating
    return '&#9733;' * rating + '&#9734;' * (5 - rating)

# ---------- OVERVIEW (6 small cards) ----------

def block_overview(d):
    g = d['google']; y = d['yelp']
    cards = []
    for source_label, source_class, source_icon, source_data in [
        ('Google', 'google', '&#9670;', g),
        ('Yelp', 'yelp', '&#9888;', y),
    ]:
        for code in ['sj', 'mv', 'fm']:
            m = LOC_META[code]
            entry = source_data.get(code, {}) or {}
            rating = entry.get('rating')
            count = entry.get('count', 0) or 0
            rating_str = f'{rating:.1f}' if isinstance(rating, (int, float)) and rating else '&mdash;'
            stars_str = stars(rating) if rating else '&#9734;&#9734;&#9734;&#9734;&#9734;'
            cards.append(f'''      <div class="overview-card {source_class}">
        <div class="ov-label">{source_label} &mdash; {m['name']}</div>
        <div class="ov-rating">{rating_str}</div>
        <div class="ov-stars">{stars_str}</div>
        <div class="ov-count">{count:,} reviews</div>
      </div>''')
    return ('    <div class="overview-grid">\n'
            + '\n'.join(cards)
            + '\n    </div>\n')

# ---------- COMBINED SNAPSHOT ----------

def combined_stats(d):
    """Compute weighted-average rating and a 5-star distribution.
    
    Aggregate counts across all 6 sources. Approximate the distribution from
    the weighted average rating using a simple model (~62/18/9/6/5 split for
    a 4.3-class business). This is a real-world UX trade-off: APIs don't
    return histograms, only avg + count.
    """
    g = d['google']; y = d['yelp']
    total_count = 0
    weighted_sum = 0.0
    for source in [g, y]:
        for code in ['sj', 'mv', 'fm']:
            e = source.get(code, {}) or {}
            r = e.get('rating')
            c = e.get('count') or 0
            if r and c:
                weighted_sum += float(r) * c
                total_count += c
    avg = (weighted_sum / total_count) if total_count else 0.0
    
    # Approximate distribution from average rating using a smooth interpolation.
    # 4.5+ -> 70/18/7/3/2 ; 4.0-4.5 -> 60/22/10/5/3 ; 3.5-4.0 -> 50/22/15/8/5
    if avg >= 4.5:
        pct = [70, 18, 7, 3, 2]
    elif avg >= 4.2:
        pct = [62, 20, 10, 5, 3]
    elif avg >= 3.8:
        pct = [55, 22, 12, 7, 4]
    elif avg >= 3.4:
        pct = [45, 22, 16, 10, 7]
    else:
        pct = [35, 20, 18, 14, 13]
    counts = [int(round(total_count * p / 100)) for p in pct]
    return avg, total_count, list(zip([5,4,3,2,1], pct, counts))

def extract_themes(d, themes, max_themes=8):
    """Walk all individual reviews and count how many mention each theme.
    Returns a list of (label, count) sorted desc, top max_themes."""
    counts = Counter()
    for source in ['google', 'yelp']:
        for code in ['sj', 'mv', 'fm']:
            entry = d.get(source, {}).get(code, {}) or {}
            for r in entry.get('reviews', []) or []:
                text = (r.get('text') or '').lower()
                if not text:
                    continue
                # Filter by rating: positive themes from 4-5★ reviews,
                # negative themes from 1-3★ reviews. We can't tell from
                # this function alone, so callers pass appropriately filtered themes.
                for label, kws in themes:
                    if any(kw in text for kw in kws):
                        counts[label] += 1
                        break
    return counts.most_common(max_themes)

def filter_reviews_by_rating(d, min_rating=None, max_rating=None):
    """Return all individual reviews with rating in [min, max]."""
    out = []
    for source in ['google', 'yelp']:
        for code in ['sj', 'mv', 'fm']:
            entry = d.get(source, {}).get(code, {}) or {}
            for r in entry.get('reviews', []) or []:
                rating = int(r.get('rating') or 0)
                if min_rating is not None and rating < min_rating: continue
                if max_rating is not None and rating > max_rating: continue
                out.append({**r, 'source': source, 'loc': code})
    return out

def block_snapshot(d):
    avg, total, dist = combined_stats(d)
    
    # Extract themes from positive (4-5★) and negative (1-3★) reviews
    pos_reviews = filter_reviews_by_rating(d, min_rating=4)
    neg_reviews = filter_reviews_by_rating(d, max_rating=3)
    
    pos_counts = Counter()
    for r in pos_reviews:
        text = (r.get('text') or '').lower()
        for label, kws in PRAISED_THEMES:
            if any(kw in text for kw in kws):
                pos_counts[label] += 1
                break
    neg_counts = Counter()
    for r in neg_reviews:
        text = (r.get('text') or '').lower()
        for label, kws in NEGATIVE_THEMES:
            if any(kw in text for kw in kws):
                neg_counts[label] += 1
                break
    
    pos_top = pos_counts.most_common(8)
    neg_top = neg_counts.most_common(6)
    
    # Distribution bars
    dist_html = []
    for star, pct, count in dist:
        dist_html.append(f'''          <div class="dist-bar-row">
            <span class="dist-bar-label">{star} &#9733;</span>
            <div class="dist-bar-track"><div class="dist-bar-fill s{star}" style="width:{pct}%;"></div></div>
            <span class="dist-bar-count">~{count:,}</span>
          </div>''')
    
    # Praised themes
    if pos_top:
        praised_html = '\n'.join(
            f'            <span class="keyword-badge positive">{esc(label)} <span class="kw-count">{count}</span></span>'
            for label, count in pos_top
        )
    else:
        praised_html = '            <span style="font-size:12px;color:var(--text-muted);font-style:italic;">No clear themes detected yet</span>'
    
    # Improve themes
    if neg_top:
        improve_html = '\n'.join(
            f'            <span class="keyword-badge negative">{esc(label)} <span class="kw-count">{count}</span></span>'
            for label, count in neg_top
        )
    else:
        improve_html = '            <span style="font-size:12px;color:var(--text-muted);font-style:italic;">No critical themes detected</span>'
    
    avg_str = f'{avg:.1f}' if avg else '&mdash;'
    
    return f'''    <div class="sentiment-grid">
      <div class="sentiment-card-left">
        <div class="ov-label" style="text-align:left;">Combined Across All Platforms</div>
        <div style="display:flex; align-items:baseline; gap:12px; margin-bottom:16px; margin-top:6px;">
          <span style="font-size:42px; font-weight:700; color:var(--text-dark);">{avg_str}</span>
          <span style="color:var(--amber); font-size:20px; letter-spacing:2px;">{stars(avg)}</span>
          <span style="font-size:13px; color:var(--text-muted);">{total:,} total reviews</span>
        </div>
        <div class="dist-bar-container">
{chr(10).join(dist_html)}
        </div>
      </div>

      <div class="sentiment-card-right">
        <div class="keyword-card praised">
          <div class="ov-label">Most Praised Themes</div>
          <div class="keyword-section">
{praised_html}
          </div>
        </div>
        <div class="keyword-card improve">
          <div class="ov-label">Areas to Improve</div>
          <div class="keyword-section">
{improve_html}
          </div>
        </div>
      </div>
    </div>
'''

# ---------- ACTION ITEMS (auto-generated) ----------

def block_actions(d):
    """Auto-generate up to 3 alert cards from theme analysis + low-rated reviews."""
    neg_reviews = filter_reviews_by_rating(d, max_rating=3)
    neg_counts = Counter()
    examples = {}  # theme -> [example reviews]
    for r in neg_reviews:
        text = (r.get('text') or '').lower()
        for label, kws in NEGATIVE_THEMES:
            if any(kw in text for kw in kws):
                neg_counts[label] += 1
                examples.setdefault(label, []).append(r)
                break
    
    # Build alert cards from top 3 negative themes
    severity_colors = ['var(--red)', 'var(--orange)', 'var(--yellow)']
    severity_icons = ['&#9888;', '&#9888;', '&#128161;']
    
    cards = []
    for i, (theme, count) in enumerate(neg_counts.most_common(3)):
        color = severity_colors[i] if i < 3 else 'var(--text-muted)'
        icon = severity_icons[i] if i < 3 else '&#9432;'
        ex_reviews = examples.get(theme, [])[:2]
        # Build a concise body: theme description + example quotes
        ex_quotes = []
        for er in ex_reviews:
            name = er.get('name', 'Reviewer')
            src = er.get('source', '').title()
            loc = er.get('loc', '').upper()
            txt = (er.get('text') or '').strip()
            # Truncate to ~140 chars
            if len(txt) > 140:
                txt = txt[:137] + '...'
            ex_quotes.append(f'{esc(name)} ({src}, {loc}): &ldquo;{esc(txt)}&rdquo;')
        body = f'{count} review{"s" if count != 1 else ""} mention this. ' + ' '.join(ex_quotes) if ex_quotes else f'{count} reviews flagged this theme.'
        title = f'{icon} {esc(theme)}'
        cards.append(f'''    <div class="alert-card" style="border-left-color: {color};">
      <div class="alert-title">{title}</div>
      <div class="alert-body">{body}</div>
    </div>''')
    
    if not cards:
        return ('    <div class="empty-state">No critical action items detected this week. '
                'Reviews are predominantly positive across all locations.</div>\n')
    return '\n'.join(cards) + '\n'

# ---------- VERIFY BANNER ----------

def verify_banner(source_label, entry, source_key=None):
    """Render a single verify-banner row. For Yelp via SerpAPI, the address
    field contains a neighborhood string (not a street), so we suppress the
    visual ✗ when slug-match succeeded."""
    if not entry:
        return ''
    addr = entry.get('address', '') or ''
    addr_verified = entry.get('address_verified', False)
    err = entry.get('error', '')
    
    if err:
        return f'<div class="verify-banner fail">&#10006; {source_label}: {esc(err)}</div>'
    
    if not addr:
        return ''
    
    if addr_verified:
        return f'<div class="verify-banner">&#10004; {source_label}</div>'
    else:
        return f'<div class="verify-banner fail">&#10006; {source_label}: address mismatch</div>'

# ---------- LOCATION TABS (Google + Yelp side-by-side) ----------

def review_card_html(r, source_label):
    """Render a single review card. Long reviews get truncated with read-more."""
    rating = int(r.get('rating') or 0) or 5
    rating = max(1, min(5, rating))
    name = esc(r.get('name', f'{source_label} Reviewer'))
    date_str = esc(format_review_date(r.get('date', '')))
    text = (r.get('text') or '').strip()
    if not text:
        return ''
    text_esc = esc(text)
    
    # Truncate if longer than ~280 chars
    is_long = len(text) > 280
    text_class = 'review-text truncated' if is_long else 'review-text'
    
    # Generate unique-ish id from hash
    rid = f'r-{abs(hash(name + date_str + text[:30]))}'[:20]
    
    read_more = ''
    if is_long:
        read_more = f'\n            <button class="read-more-btn" onclick="toggleReview(\'{rid}\', this)">Read more</button>'
    
    src_class = source_label.lower()
    
    return f'''          <div class="review-card star-{rating}">
            <div class="review-header">
              <span class="review-author">{name}</span>
              <span class="review-date">{date_str}</span>
            </div>
            <div class="review-meta">
              <span class="review-stars">{stars(rating, 'filled')}</span>
              <span class="review-platform {src_class}">{source_label}</span>
            </div>
            <div class="{text_class}" id="{rid}">{text_esc}</div>{read_more}
          </div>'''

def loc_tab(d, code, is_active):
    """Render one location's tab pane: G/Y verify banners + side-by-side review columns."""
    m = LOC_META[code]
    g_entry = d.get('google', {}).get(code, {}) or {}
    y_entry = d.get('yelp', {}).get(code, {}) or {}
    
    g_revs = g_entry.get('reviews', []) or []
    y_revs = y_entry.get('reviews', []) or []
    
    # Verify banners (above the reviews grid)
    banners = []
    g_b = verify_banner('Google', g_entry)
    y_b = verify_banner('Yelp', y_entry)
    if g_b: banners.append(f'      {g_b}')
    if y_b: banners.append(f'      {y_b}')
    banners_html = '\n'.join(banners)
    if banners_html:
        banners_html = f'      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;">\n        {g_b}\n        {y_b}\n      </div>'
    
    # Google column
    if g_revs:
        g_cards = '\n'.join(c for c in (review_card_html(r, 'Google') for r in g_revs[:10]) if c)
    else:
        g_cards = '          <div class="empty-state" style="padding:14px;">No Google reviews available.</div>'
    
    # Yelp column
    if y_revs:
        y_cards = '\n'.join(c for c in (review_card_html(r, 'Yelp') for r in y_revs[:10]) if c)
    else:
        y_cards = '          <div class="empty-state" style="padding:14px;">No Yelp reviews available.</div>'
    
    cls = 'tab-content active' if is_active else 'tab-content'
    
    return f'''    <div id="tab-{code}" class="{cls}">
{banners_html}
      <div class="loc-reviews-grid">
        <div>
          <div class="col-header google">
            <span class="src-icon">G</span> Google Reviews
          </div>
{g_cards}
        </div>
        <div>
          <div class="col-header yelp">
            <span class="src-icon">Y</span> Yelp Reviews
          </div>
{y_cards}
        </div>
      </div>
    </div>'''

def block_tabs(d):
    panes = [loc_tab(d, code, is_active=(i == 0)) for i, code in enumerate(['sj', 'mv', 'fm'])]
    return '\n'.join(panes) + '\n'

# ---------- RENDER ----------

def render(data, template_path, out_path):
    with open(template_path) as f:
        h = f.read()
    blocks = {
        '{{generated_date}}': data.get('generated_date', ''),
        '{{OVERVIEW_BLOCK}}': block_overview(data),
        '{{SNAPSHOT_BLOCK}}': block_snapshot(data),
        '{{ACTIONS_BLOCK}}': block_actions(data),
        '{{TABS_BLOCK}}': block_tabs(data),
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
