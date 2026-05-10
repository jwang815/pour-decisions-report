#!/usr/bin/env python3
"""Collect Google + Yelp reviews. Google uses official Places API (New).
Yelp uses SerpAPI (free tier 100 searches/mo; Yelp Fusion API is $229/mo).

Replaces the previous browser_task-based collection. Deterministic, never
CAPTCHA-blocked, suitable for GitHub Actions.

ENV VARS REQUIRED:
  GOOGLE_PLACES_API_KEY   (Places API (New) — places.googleapis.com)
  SERPAPI_KEY             (SerpAPI for Yelp; gracefully degrades if missing)

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

# Google Place text-search queries: name + address (matches one place exactly)
# We previously tried CID URL lookup but Places API (New) doesn't accept that format.
GOOGLE_TEXT_QUERIES = {
    'sj': 'Pour Decisions Craft Coffee Beer 5700 Village Oaks San Jose',
    'mv': 'Pour Decisions Craft Coffee Beer 1040 Grant Rd Mountain View',
    'fm': 'Pour Decisions Craft Coffee Beer 3530 Beacon Ave Fremont',
}

# Verified Google Maps CIDs (from user) — kept for reference / fallback link
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

def google_resolve_place_id(loc_code):
    """Resolve to a place_id by text-searching for 'name + address'.
    Verifies the result matches the expected address fragment to avoid wrong place.
    """
    if not GOOGLE_KEY: return None
    text_query = GOOGLE_TEXT_QUERIES[loc_code]
    expected = EXPECTED_ADDRESS[loc_code]
    body = {'textQuery': text_query, 'maxResultCount': 5}
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
        # Pick the first result whose address contains the expected street fragment
        for place in data.get('places', []):
            addr = place.get('formattedAddress', '')
            if expected in addr:
                return place
        # fallback: first result (will be flagged as address_verified=False downstream)
        if data.get('places'):
            return data['places'][0]
    except Exception as e:
        print(f'  Google resolve error for {loc_code}: {e}', file=sys.stderr)
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
    
    place = google_resolve_place_id(loc_code)
    if not place:
        return {'rating': None, 'count': 0, 'reviews': [],
                'error': f'No Place match for {loc_code}'}
    cid = GOOGLE_CIDS.get(loc_code)
    
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

# ---------- Yelp via SerpAPI ----------

# City + business-name search terms to resolve Yelp place_id via SerpAPI's
# yelp engine. We then fetch reviews via yelp_reviews engine.
YELP_SEARCH = {
    'sj': {'find_desc': 'Pour Decisions Craft Coffee Beer', 'find_loc': 'San Jose, CA'},
    'mv': {'find_desc': 'Pour Decisions Craft Coffee Beer', 'find_loc': 'Mountain View, CA'},
    'fm': {'find_desc': 'Pour Decisions Craft Coffee Beer', 'find_loc': 'Fremont, CA'},
}

def _serpapi_get(params, timeout=30):
    """GET https://serpapi.com/search.json with params dict; returns parsed JSON."""
    base = 'https://serpapi.com/search.json'
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{base}?{qs}', headers={'User-Agent': 'pd-reports/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

def yelp_collect(loc_code):
    """Fetch Yelp business rating + count + reviews via SerpAPI.
    
    Two-step: (1) yelp engine to find the matching place_id from name+location,
    (2) yelp_reviews engine to pull review list.
    
    Defensive: any failure returns a graceful empty payload with error string
    so build_reviews.py can render the verify-banner and skip Yelp this week.
    """
    slug = YELP_SLUGS[loc_code]
    url = f'https://www.yelp.com/biz/{slug}'
    expected = EXPECTED_ADDRESS[loc_code]
    
    api_key = os.environ.get('SERPAPI_KEY', '').strip()
    if not api_key:
        return {'rating': None, 'count': 0, 'reviews': [],
                'error': 'SERPAPI_KEY not set', 'yelp_url': url}
    
    search = YELP_SEARCH[loc_code]
    
    try:
        # Step 1: Resolve place_id via Yelp search engine on SerpAPI
        search_resp = _serpapi_get({
            'engine': 'yelp',
            'find_desc': search['find_desc'],
            'find_loc': search['find_loc'],
            'api_key': api_key,
        })
        
        if 'error' in search_resp:
            return {'rating': None, 'count': 0, 'reviews': [],
                    'error': f"SerpAPI search: {search_resp['error']}"[:200],
                    'yelp_url': url}
        
        organic = search_resp.get('organic_results') or []
        # Pick the result whose link matches our slug (most reliable),
        # else the first one with matching address fragment.
        match = None
        for r in organic:
            link = r.get('link', '') or ''
            if slug in link:
                match = r; break
        if not match:
            for r in organic:
                addr_blob = ' '.join([
                    r.get('neighborhoods', '') or '',
                    r.get('address', '') or '',
                    str((r.get('service_area') or {}).get('addresses') or ''),
                ])
                if expected in addr_blob:
                    match = r; break
        if not match and organic:
            match = organic[0]
        
        if not match:
            return {'rating': None, 'count': 0, 'reviews': [],
                    'error': 'Yelp business not found in SerpAPI search',
                    'yelp_url': url}
        
        place_id = match.get('place_ids', [None])[0] if match.get('place_ids') else None
        if not place_id:
            # Fallback: some results expose 'place_id' directly
            place_id = match.get('place_id')
        
        rating = match.get('rating')
        try:
            rating = float(rating) if rating is not None else None
        except Exception:
            rating = None
        count = int(match.get('reviews') or 0)
        addr_full = match.get('address') or match.get('neighborhoods') or ''
        addr_match = (expected in addr_full) if addr_full else None
        
        # Step 2: Fetch reviews
        reviews = []
        if place_id:
            try:
                rev_resp = _serpapi_get({
                    'engine': 'yelp_reviews',
                    'place_id': place_id,
                    'api_key': api_key,
                })
                for r in (rev_resp.get('reviews') or [])[:20]:
                    user = r.get('user') or {}
                    txt = (r.get('comment') or {}).get('text') or r.get('snippet') or ''
                    if not txt: continue
                    reviews.append({
                        'name': (user.get('name') or 'Yelp Reviewer')[:80],
                        'rating': int(float(r.get('rating') or 5)),
                        'date': r.get('date') or '',
                        'text': txt[:1500],
                        'url': r.get('comment_link') or url,
                    })
            except Exception as e:
                # Reviews fetch failed but we still have rating + count
                pass
        
        return {
            'rating': rating,
            'count': count,
            'reviews': reviews,
            'yelp_url': url,
            'address': addr_full,
            'address_verified': addr_match,
            'place_id': place_id,
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
