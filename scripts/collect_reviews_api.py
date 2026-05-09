#!/usr/bin/env python3
"""Collect Google + Yelp reviews via official APIs.

Replaces the previous browser_task-based collection. Deterministic, never
CAPTCHA-blocked, suitable for GitHub Actions.

ENV VARS REQUIRED:
  GOOGLE_PLACES_API_KEY   (Places API (New) — places.googleapis.com)
  YELP_API_KEY            (Yelp Fusion — api.yelp.com/v3)

USAGE:
  python3 collect_reviews_api.py <run_dir>
  e.g. python3 collect_reviews_api.py /home/user/workspace/square_data/run11

Writes:
  <run_dir>/reviews_data.json    (consumed by build_reviews.py)
  <run_dir>/reviews_raw.json     (full raw responses for archive)

Coverage notes:
  - Google Places API (New) returns up to 5 most-relevant reviews per place,
    plus rating + total user_ratings count.
  - Yelp Fusion returns up to 3 review excerpts (~160 chars each), plus
    rating + review count. Yelp ToS forbids storing full reviews — excerpts
    are the official limit.
  - For ratings & counts (the main numbers users care about), both APIs are
    exhaustive. For full reviewer text, the API limits are real ceilings.
"""
import os, sys, json, urllib.request, urllib.parse, time
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
YELP_KEY = os.environ.get('YELP_API_KEY')

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

# ---------- Yelp Fusion ----------

def yelp_fetch_business(slug):
    """Return business details by alias/slug."""
    if not YELP_KEY: return None
    req = urllib.request.Request(
        f'https://api.yelp.com/v3/businesses/{slug}',
        headers={'Authorization': f'Bearer {YELP_KEY}', 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def yelp_fetch_reviews(business_id):
    """Up to 3 review excerpts."""
    if not YELP_KEY: return []
    req = urllib.request.Request(
        f'https://api.yelp.com/v3/businesses/{business_id}/reviews?limit=20&sort_by=newest',
        headers={'Authorization': f'Bearer {YELP_KEY}', 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get('reviews', [])

def yelp_collect(loc_code):
    if not YELP_KEY:
        return {'rating': None, 'count': 0, 'reviews': [], 'error': 'YELP_API_KEY not set'}
    
    slug = YELP_SLUGS[loc_code]
    try:
        biz = yelp_fetch_business(slug)
    except Exception as e:
        return {'rating': None, 'count': 0, 'reviews': [], 'error': str(e)[:120]}
    
    if not biz:
        return {'rating': None, 'count': 0, 'reviews': [], 'error': 'biz not found'}
    
    expected = EXPECTED_ADDRESS[loc_code]
    addr = ' '.join(biz.get('location', {}).get('display_address', []))
    addr_match = expected in addr
    
    reviews_raw = []
    try:
        reviews_raw = yelp_fetch_reviews(biz['id'])
    except Exception as e:
        print(f'  Yelp reviews error for {slug}: {e}', file=sys.stderr)
    
    reviews = []
    for r in reviews_raw:
        reviews.append({
            'name': r.get('user', {}).get('name', 'Yelp Reviewer'),
            'rating': r.get('rating', 5),
            'date': r.get('time_created', '')[:10],
            'text': r.get('text', ''),
            'url': r.get('url', ''),
        })
    
    return {
        'rating': biz.get('rating'),
        'count': biz.get('review_count', 0),
        'reviews': reviews,
        'business_id': biz.get('id'),
        'yelp_url': biz.get('url', f'https://www.yelp.com/biz/{slug}'),
        'address': addr,
        'address_verified': addr_match,
    }

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
