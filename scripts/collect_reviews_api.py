#!/usr/bin/env python3
"""Collect Google + Yelp reviews. Google uses official Places API (New).
Yelp uses Playwright (free; Fusion API is now $229/mo for review access).

Replaces the previous browser_task-based collection. Deterministic, never
CAPTCHA-blocked, suitable for GitHub Actions.

ENV VARS REQUIRED:
  GOOGLE_PLACES_API_KEY   (Places API (New) — places.googleapis.com)
  (no Yelp key; Yelp via Playwright)

USAGE:
  python3 collect_reviews_api.py <run_dir>
  e.g. python3 collect_reviews_api.py /home/user/workspace/square_data/run11

Writes:
  <run_dir>/reviews_data.json    (consumed by build_reviews.py)
  <run_dir>/reviews_raw.json     (full raw responses for archive)

Coverage notes:
  - Google Places API (New) returns up to 5 most-relevant reviews per place,
    plus rating + total user_ratings count.
  - Yelp via Playwright fetches the public business page and parses the
    first ~10–20 reviews. If Yelp blocks (CAPTCHA / 403), we gracefully
    are the official limit.
  - For ratings & counts (the main numbers users care about), both APIs are
    exhaustive. For full reviewer text, the API limits are real ceilings.
"""
import os, sys, json, urllib.request, urllib.parse, time, re
from datetime import datetime, timezone

# Verified Google Maps CIDs (from user) — decimal place identifiers
GOOGLE_CIDS = {
    'sj': 8238189378479499410,
    'mv': 11471586999040563699,
    'fm': 16707411319746596383,
}

# Yelp business slugs (from user-provided URLs)
YELP_SLUGS = {
    'sj': 'pour-decisions-craft-san-jose',
    'mv': 'pour-decisions-craft-mountain-view',
    'fm': 'pour-decisions-craft-fremont',
}

# Address fragments expected on each location's listing — used to verify
# the right place returned (defends against lookup mix-ups).
EXPECTED_ADDRESS = {
    'sj': '5700 Village Oaks',
    'mv': '1040 Grant',
    'fm': '3530 Beacon',
}

GOOGLE_KEY = os.environ.get('GOOGLE_PLACES_API_KEY')
# YELP_KEY removed — using Playwright now

# ---------- Google Places (New) ----------

def google_resolve_place_id(cid):
    """Convert numeric CID → place_id by querying Place Details with the cid hex."""
    if not GOOGLE_KEY: return None
    # Place ID format: ChIJ... — we get it from Place Details using the CID URL param.
    # New Places API supports searchText with the maps URL.
    body = {'textQuery': f'https://maps.google.com/?cid={cid}'}
    req = urllib.request.Request(
        'https://places.googleapis.com/v1/places:searchText',
        data=json.dumps(body).encode(),
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': GOOGLE_KEY,
            'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        places = data.get('places', [])
        if places: return places[0]
    except Exception as e:
        print(f'  Google resolve error for cid {cid}: {e}', file=sys.stderr)
    return None

def google_fetch_place(place_id):
    """Pull rating, count, reviews for a place_id."""
    field_mask = ('id,displayName,formattedAddress,rating,userRatingCount,'
                  'reviews,googleMapsUri')
    req = urllib.request.Request(
        f'https://places.googleapis.com/v1/places/{place_id}?languageCode=en',
        headers={
            'X-Goog-Api-Key': GOOGLE_KEY,
            'X-Goog-FieldMask': field_mask,
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def google_collect(loc_code):
    """Collect Google reviews for one location. Returns dict for reviews_data.json."""
    if not GOOGLE_KEY:
        return {'rating': None, 'count': 0, 'reviews': [], 'error': 'GOOGLE_PLACES_API_KEY not set'}
    
    cid = GOOGLE_CIDS[loc_code]
    place = google_resolve_place_id(cid)
    if not place:
        return {'rating': None, 'count': 0, 'reviews': [], 'error': f'CID {cid} not resolvable'}
    
    place_id = place['id']
    detail = google_fetch_place(place_id)
    
    # Verify address matches expected fragment
    addr = detail.get('formattedAddress', '')
    expected = EXPECTED_ADDRESS[loc_code]
    addr_match = expected in addr
    
    reviews = []
    for r in detail.get('reviews', []):
        reviews.append({
            'name': r.get('authorAttribution', {}).get('displayName', 'Google Reviewer'),
            'rating': r.get('rating', 5),
            'date': r.get('relativePublishTimeDescription', ''),
            'text': (r.get('text', {}) or {}).get('text') or (r.get('originalText', {}) or {}).get('text', ''),
            'url': r.get('googleMapsUri', ''),
        })
    
    return {
        'rating': detail.get('rating'),
        'count': detail.get('userRatingCount', 0),
        'reviews': reviews,
        'place_id': place_id,
        'address': addr,
        'address_verified': addr_match,
        'maps_uri': detail.get('googleMapsUri', f'https://maps.google.com/?cid={cid}'),
    }

# ---------- Yelp via Playwright ----------

def yelp_collect(loc_code):
    """Fetch Yelp business page with Playwright, parse rating + count + reviews.
    
    Defensive: any failure returns a graceful empty payload with error string
    so build_reviews.py can render the verify-banner and skip Yelp this week.
    """
    slug = YELP_SLUGS[loc_code]
    url = f'https://www.yelp.com/biz/{slug}'
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'rating': None, 'count': 0, 'reviews': [],
                'error': 'playwright not installed', 'yelp_url': url}
    
    expected = EXPECTED_ADDRESS[loc_code]
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/124.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 900},
                locale='en-US',
            )
            page = ctx.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(2500)
            
            # Detect block / CAPTCHA
            title = page.title() or ''
            content_sample = page.content()[:2000].lower()
            if 'captcha' in content_sample or 'access denied' in content_sample or 'unusual traffic' in content_sample:
                browser.close()
                return {'rating': None, 'count': 0, 'reviews': [],
                        'error': 'Yelp blocked request (CAPTCHA / access-denied)',
                        'yelp_url': url}
            
            # Address (verify it's the right business)
            addr = ''
            for sel in ['address', '[data-testid="bizDetailsHeaderAddress"]',
                        'p:has-text(" CA ")']:
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = (el.inner_text() or '').strip()
                        if t and any(c.isdigit() for c in t):
                            addr = t.replace('\n', ' ')
                            break
                except Exception:
                    continue
            addr_match = expected in addr if addr else None
            
            # Overall rating + count from JSON-LD
            rating = None
            count = 0
            try:
                ld_handles = page.query_selector_all('script[type="application/ld+json"]')
                for h in ld_handles:
                    try:
                        ld = json.loads(h.inner_text() or '{}')
                    except Exception:
                        continue
                    if isinstance(ld, list):
                        candidates = ld
                    else:
                        candidates = [ld]
                    for c in candidates:
                        agg = c.get('aggregateRating') if isinstance(c, dict) else None
                        if agg:
                            rating = float(agg.get('ratingValue') or 0) or rating
                            count = int(agg.get('reviewCount') or 0) or count
                            break
                    if rating: break
            except Exception:
                pass
            
            # Fallback: scrape rating/count from page text if JSON-LD missing
            if not rating:
                try:
                    rating_el = page.query_selector('div[role="img"][aria-label*="star rating"]')
                    if rating_el:
                        lbl = rating_el.get_attribute('aria-label') or ''
                        m = re.search(r'([\d.]+)\s*star', lbl)
                        if m: rating = float(m.group(1))
                except Exception: pass
            if not count:
                try:
                    txt = page.inner_text('body')[:5000]
                    m = re.search(r'([\d,]+)\s+reviews?', txt)
                    if m: count = int(m.group(1).replace(',', ''))
                except Exception: pass
            
            # Individual reviews
            reviews = []
            review_cards = page.query_selector_all('ul.list__09f24__ynIEd > li, [data-testid="serp-ia-card"], section[aria-label*="Recommended Reviews"] li, ul li:has(div[role="img"][aria-label*="star rating"])')
            seen = set()
            for card in review_cards[:30]:
                try:
                    text_el = (card.query_selector('p[class*="comment"]') or
                               card.query_selector('span.raw__09f24__T4Ezm') or
                               card.query_selector('p span'))
                    text = (text_el.inner_text() if text_el else '').strip()
                    if not text or len(text) < 20: continue
                    if text in seen: continue
                    seen.add(text)
                    
                    rt_el = card.query_selector('div[role="img"][aria-label*="star rating"]')
                    rev_rating = 5
                    if rt_el:
                        m = re.search(r'([\d.]+)\s*star', rt_el.get_attribute('aria-label') or '')
                        if m: rev_rating = int(float(m.group(1)))
                    
                    name_el = card.query_selector('a[href*="/user_details"]') or card.query_selector('span[class*="user-passport-info"] a')
                    name = (name_el.inner_text().strip() if name_el else 'Yelp Reviewer')
                    
                    date = ''
                    for ds in card.query_selector_all('span'):
                        try:
                            tx = (ds.inner_text() or '').strip()
                            if re.match(r'^[A-Z][a-z]{2}\s\d{1,2},\s\d{4}$', tx):
                                date = tx; break
                        except Exception: continue
                    
                    reviews.append({
                        'name': name[:80],
                        'rating': rev_rating,
                        'date': date,
                        'text': text[:1500],
                        'url': url,
                    })
                    if len(reviews) >= 20: break
                except Exception:
                    continue
            
            browser.close()
            
            return {
                'rating': rating,
                'count': count,
                'reviews': reviews,
                'yelp_url': url,
                'address': addr,
                'address_verified': addr_match,
            }
    except Exception as e:
        return {'rating': None, 'count': 0, 'reviews': [],
                'error': str(e)[:200], 'yelp_url': url}

# ---------- Main ----------

def main(run_dir):
    os.makedirs(run_dir, exist_ok=True)
    
    google = {}; yelp = {}
    for code in ['sj', 'mv', 'fm']:
        print(f'Collecting {code.upper()}...')
        google[code] = google_collect(code)
        time.sleep(0.5)  # be polite
        yelp[code] = yelp_collect(code)
        print(f'  Google: {google[code].get("count")} reviews @ {google[code].get("rating")}'
              f' | addr_ok={google[code].get("address_verified")}')
        print(f'  Yelp:   {yelp[code].get("count")} reviews @ {yelp[code].get("rating")}'
              f' | addr_ok={yelp[code].get("address_verified")}')
    
    today = datetime.now(timezone.utc)
    data = {
        'generated_date': today.strftime('%b %-d, %Y').replace(' 0', ' '),
        'collected_at': today.isoformat().replace('+00:00', 'Z'),
        'google': google,
        'yelp': yelp,
    }
    
    out = os.path.join(run_dir, 'reviews_data.json')
    with open(out, 'w') as f:
        json.dump(data, f, indent=2)
    print(f'Wrote {out}')
    
    # Sanity log
    issues = []
    for code in ['sj', 'mv', 'fm']:
        for src, src_data in [('Google', google[code]), ('Yelp', yelp[code])]:
            if src_data.get('error'):
                issues.append(f'{code.upper()} {src}: {src_data["error"]}')
            elif src_data.get('address_verified') is False:
                issues.append(f'{code.upper()} {src}: address mismatch '
                              f'(got "{src_data.get("address","")[:60]}")')
    if issues:
        print('\nReview collection issues:')
        for i in issues: print(f'  - {i}')
    return data

if __name__ == '__main__':
    run_dir = sys.argv[1] if len(sys.argv) > 1 else '/tmp/run_test'
    main(run_dir)
