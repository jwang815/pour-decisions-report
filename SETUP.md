# Setup — Pour Decisions Weekly Report (GitHub Actions)

This repo runs a weekly automated report for Pour Decisions Craft Coffee & Beer.
It pulls Square data, builds an HTML dashboard + reviews page, deploys to Vercel,
and emails a summary. Cadence: **Mondays 03:30 PT** (cron: `30 10 * * 1` UTC).

## 1. Required GitHub Actions Secrets

Add all five secrets at:
**Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret name              | What it is                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------- |
| `SQUARE_API_TOKEN`       | Square access token (starts with `EAAA…`).                                                          |
| `VERCEL_TOKEN`           | Vercel personal token. Create at [vercel.com/account/tokens](https://vercel.com/account/tokens).    |
| `GOOGLE_PLACES_API_KEY`  | Google Cloud API key with **Places API (New)** enabled.                                             |
| `SERPAPI_KEY`            | SerpAPI key for Yelp review collection. Free tier (100 searches/mo) is plenty for weekly runs.      |
| `GMAIL_USERNAME`         | The Gmail address you want emails sent from (e.g. `jwang815@gmail.com`).                            |
| `GMAIL_APP_PASSWORD`     | Gmail App Password (16-character, generated with 2FA on).                                           |

**Note on Yelp:** Yelp Fusion charges $229/mo for review access and Yelp's public pages 403-block all cloud IPs (incl. GitHub Actions runners). We use SerpAPI's `yelp` + `yelp_reviews` engines, which sit on Yelp's public data behind residential IPs. The free tier (100 searches/mo) covers ~16 weekly runs at 6 calls each.

### How to add via the GitHub web UI

1. Go to your repo → **Settings**.
2. Sidebar → **Secrets and variables → Actions**.
3. Click **New repository secret**, paste name + value, save. Repeat for all six.

### How to add via `gh` CLI

```bash
gh secret set SQUARE_API_TOKEN       --body "EAAA..."
gh secret set VERCEL_TOKEN           --body "vcp_..."
gh secret set GOOGLE_PLACES_API_KEY  --body "AIza..."
gh secret set SERPAPI_KEY            --body "..."
gh secret set GMAIL_USERNAME         --body "jwang815@gmail.com"
gh secret set GMAIL_APP_PASSWORD     --body "abcd efgh ijkl mnop"
```

---

## 2. How to obtain each key

### Google Places API key

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Select / create a project (e.g. `pour-decisions-reports`).
3. **APIs & Services → Library** → search **"Places API (New)"** → Enable.
4. **APIs & Services → Credentials → Create credentials → API key**.
5. Copy the generated key. (Optionally) restrict it:
   - **API restrictions** → "Restrict key" → check **Places API (New)** only.
   - **Application restrictions** → leave **None** (GitHub Actions runners use rotating IPs, so IP restriction won't work).
6. Make sure billing is enabled on the project. The Places API has a generous free tier; one weekly run uses ~6 requests.

### Yelp data (SerpAPI)

1. Sign up at [serpapi.com](https://serpapi.com/users/sign_up) (free, no card required).
2. Copy your private API key from the [dashboard](https://serpapi.com/manage-api-key).
3. Set it as the `SERPAPI_KEY` repo secret.

The weekly run makes 6 SerpAPI calls (3 location lookups × 2 endpoints: `yelp` search + `yelp_reviews`). The free plan (100 searches/mo) covers ~16 runs. If `SERPAPI_KEY` is missing or quota is exhausted, the dashboard gracefully renders Google reviews only and flags the failure in the address-verify banner.

### Gmail App Password

1. Open [account.google.com](https://myaccount.google.com).
2. **Security** → enable **2-Step Verification** if not on (required).
3. **Security → App passwords** ([direct link](https://myaccount.google.com/apppasswords)).
4. Name it "Pour Decisions Report" → **Create**.
5. Copy the 16-character password (looks like `abcd efgh ijkl mnop`). Spaces are OK; you can keep or strip them.

### Vercel token

1. Go to [vercel.com/account/tokens](https://vercel.com/account/tokens).
2. **Create Token** → name "GH Actions Pour Decisions" → no expiration (or your preference) → scope: full account.
3. Copy the value (starts with `vcp_`).
4. Confirm a Vercel project named `pour-decisions-report` exists. If not, the first deploy from the script will create it.

### Square API token

1. Go to [developer.squareup.com](https://developer.squareup.com).
2. **Applications → your app → Production → Access token**.
3. Copy the production token (starts with `EAAA`). Required scopes: `MERCHANT_PROFILE_READ`, `PAYMENTS_READ`, `ORDERS_READ`, `ITEMS_READ`, `EMPLOYEES_READ`, `LABOR_READ`.

---

## 3. Pipeline overview (what each step does)

The workflow `.github/workflows/weekly-report.yml` runs these in order:

1. **`scripts/next_run.py`** → reads `state/run_log.json`, prints `last_run_number + 1`.
2. **`scripts/fetch_and_compute.py <run>`** → pulls Square Payments / Orders / Refunds / Shifts / Catalog for the week, computes all metrics, writes `square_data/run<n>/final.json` + raw dumps.
3. **`scripts/reconcile.py <run_dir>`** → 3 data-quality checks: Payments↔Orders cross-check, pagination re-query, sanity bounds vs prior 4-week average. Annotates `final.json.data_quality`.
4. **`scripts/generate_insights.py <final.json>`** → computes 5 data-driven insights, writes back into `final.json`.
5. **`scripts/build_report.py <run_dir>`** → renders `template.html` + `final.json` → `index.html`.
6. **`scripts/collect_reviews_api.py <run_dir>`** → calls Google Places + Yelp Fusion, writes `reviews_data.json`.
7. **`scripts/build_reviews.py <run_dir>`** → renders `reviews_template.html` + `reviews_data.json` → `reviews.html`.
8. **`scripts/update_log.py <run>`** → appends snapshot to `state/run_log.json` (committed back to repo for trailing comparisons).
9. **`scripts/wrap_and_deploy.js`** → applies the SHA-256 client-side password gate to `index.html` and `reviews.html`, copies `pnl.html` unchanged into `dist/`.
10. **`scripts/deploy-vercel-live.js $VERCEL_TOKEN dist`** → uploads the 3 files via Vercel REST API to project `pour-decisions-report`.
11. **`scripts/send_email.py {success|failure}`** → sends summary or failure email via Gmail SMTP.

## 4. Manual trigger

Run on demand from **Actions → Weekly Report → Run workflow**.

Optional input: `skip_email=true` — runs the pipeline but doesn't email (useful while iterating).

## 5. State file (`state/run_log.json`)

The workflow commits an updated `state/run_log.json` after every successful run. That file:

- Tracks all prior run snapshots (history),
- Feeds `next_run.py` so run numbers monotonically increase,
- Feeds `reconcile.py`'s sanity-bounds check (compares this week to prior-4-week avg).

If you ever need to reset, delete `state/run_log.json` and the next run will start at #1.

## 6. Troubleshooting

- **Empty review section** — check the workflow log for `Address mismatch` warnings. The CIDs in `collect_reviews_api.py` are hard-coded for Pour Decisions; if Google relocates the place_id, update them.
- **Vercel 401** — token expired/invalid. Regenerate at vercel.com/account/tokens and update the secret.
- **Gmail SMTP auth failed** — App password got revoked. Generate a new one and update `GMAIL_APP_PASSWORD`.
- **Run number stuck** — confirm `state/run_log.json` was committed back. Workflow needs `permissions: contents: write` (already set).
- **Square 401/429** — token rotated or rate limit. Refresh from developer.squareup.com.

## 7. Delivery Tracking page (optional shared storage)

`deliveries.html` is a client-side delivery tracker (no server). It supports two
persistence modes, selected at runtime by the user:

- **Local-only (default)** — deliveries are stored in `localStorage` in the
  current browser only. No setup; nothing to share. A yellow banner warns the
  user that data is not shared.
- **Shared via GitHub Gist** — deliveries are stored in a single JSON file
  inside a private Gist. Any browser that knows the Gist ID + a token with
  `gist` scope reads/writes the same data. A green banner indicates connected.

To enable shared mode for staff:

1. Create a private Gist at [gist.github.com](https://gist.github.com) with one
   file named `deliveries.json` and the initial content `{"deliveries":[]}`.
2. Note the Gist ID from the URL (the hex string after `/<user>/`).
3. Create a fine-grained Personal Access Token at
   [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)
   with **Account permissions → Gists: Read and write**.
4. On `deliveries.html`, click **Settings** in the storage banner, paste the
   Gist ID and token, and click **Save & Sync**.

Limitations / notes:

- The token is stored in `localStorage` in the user's browser. Anyone with
  access to that browser can read it. Treat it like any other shared
  staff-only credential.
- All staff browsers need the same Gist ID + a token with access to that Gist.
  Any token with read/write on the Gist works (one shared token or one per
  user).
- No real-time tracking-API integration today — carrier is auto-detected from
  the tracking-number pattern and status/ETA are updated manually. The
  `loadState()` / `saveState()` adapter in `deliveries.html` is structured so a
  future tracking provider (Shippo, EasyPost, etc.) can replace manual updates
  with API fetches without changing the UI layer.

The page is wrapped by `wrap_and_deploy.js` with the same client-side password
gate as `index.html` / `reviews.html`. It is committed to the repo in
already-wrapped form (the wrap script is idempotent and skips it on weekly
runs). The weekly workflow does not regenerate or commit `deliveries.html`.

---

## 8. Migrating from the old Perplexity cron

Once you've run the GitHub Actions workflow successfully at least once and verified the deployed site looks correct, **disable the old Perplexity cron `dc4e59f2`** so you stop paying for it.
