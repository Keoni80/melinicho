# MeLi Nicho

Market analysis tool for finding profitable niches on MercadoLibre Argentina.

## Stack
- **Backend:** Flask (Python), Gunicorn (timeout 180s)
- **Frontend:** Vanilla JS/CSS
- **Database:** SQLite (melichnicho.db)
- **AI:** Claude API (anthropic SDK) for niche analysis
- **Search:** Apify scraper as primary search (MeLi API search is blocked)
- **Deploy:** Railway via `railway up` CLI (autodeploy not available on free plan)

## MercadoLibre API (critical constraints)

Since April 2025, MeLi blocked public API access for non-partner apps:

### Blocked endpoints (403 from server IPs):
- `/sites/MLA/search` — general marketplace search
- `/items/{item_id}` — individual item details (sold_quantity)

### Working endpoints (with OAuth token):
- `/categories/{id}` — subcategory tree
- `/products/{id}` — product details
- `/products/{id}/items` — get item_id from product
- `/highlights/MLA/category/{id}` — highlighted products per category
- `/visits/items?ids={item_id}` — visit counts (primary demand metric)
- `/users/{user_id}/items/search` — search own listings only

### Workaround:
Search uses **Apify** scraper (`karamelo/mercadolibre-scraper-espanol-castellano`) when `/search` returns 403. Returns ~48 items per page with title, price, seller, category, image. Cost: ~$1.20/1000 results.

Token refresh in `_get()` handles both 401 and 403.

## Railway environment variables
- `ANTHROPIC_API_KEY` — Claude API
- `MELI_ACCESS_TOKEN` — MeLi OAuth token (auto-refreshes)
- `MELI_CLIENT_ID` — 533536772492362
- `MELI_CLIENT_SECRET`
- `MELI_REFRESH_TOKEN`
- `APIFY_API_TOKEN` — Apify scraper

## Scoring algorithm (analyzer.py)
- Visits demand: 0–30 pts (visits / max_visits)
- Sales demand: 0–10 pts (sold / max_sold)
- Competition: 0–40 pts (fewer sellers = higher score)
- Price positioning: 0–15 pts (closeness to median price)
- Free shipping bonus: 0–5 pts

## Key files
- `app.py` — Flask routes: search, discover, analyze (AI), export CSV, history
- `meli_api.py` — MeLi API + Apify integration, token refresh, visits enrichment
- `analyzer.py` — Opportunity scoring, niche stats, seller ranking
- `static/app.js` — Frontend logic
- `templates/index.html` — UI
- `Procfile` — Gunicorn config (timeout 180s)

## Deploy
```bash
railway up
```
Needs `NODE_EXTRA_CA_CERTS` env var set if machine has AVG antivirus (SSL interception).

## Known issues
- `sold_quantity` always 0 because `/items/{id}` is blocked
- Apify search takes 20-30 seconds (scraper startup time)
- Free Railway plan has resource limits

## Pending ideas
- Compare prices with Alibaba (Apify has Alibaba scraper too)
- On-demand Alibaba price lookup per product with margin calculation
