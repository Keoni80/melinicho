# MeLi Nicho

Market analysis tool for finding profitable niches on MercadoLibre Argentina.

## Stack
- **Backend:** Flask (Python), Gunicorn (timeout 180s)
- **Frontend:** Vanilla JS/CSS
- **Database:** SQLite (melichnicho.db)
- **AI:** Claude API (`claude-sonnet-4-6`) for niche analysis
- **Search:** Apify scraper as primary search (MeLi API search is blocked)
- **Deploy:** Railway via `~/.railway/bin/railway up --detach --service melichnicho` (autodeploy not available on free plan)
- **Dependencies:** `flask`, `requests`, `anthropic`, `gunicorn`, `openpyxl`

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
- `ADMIN_USER` / `ADMIN_PASSWORD` — login credentials (set via CLI only, not dashboard)
- `FLASK_SECRET_KEY`

## Scoring algorithm (analyzer.py)
- Visits demand: 0–35 pts (visits / max_visits) — primary demand signal
- Sales bonus: 0–5 pts (sold_quantity / max_sold) — always 0 (blocked endpoint), reserved for future
- Competition: 0–40 pts (fewer sellers = higher score; falls back to total items if seller_id unavailable)
- Price positioning: 0–10 pts (closeness to median price)
- Free shipping bonus: 0–5 pts

## Key files
- `app.py` — Flask routes: search, discover, analyze (AI), export CSV, history, rt-upload, rt-analyze, nubi-analyze, nubi-export, nubi-results
- `meli_api.py` — MeLi API + Apify integration, token refresh, visits enrichment
- `analyzer.py` — Opportunity scoring, niche stats, seller ranking
- `static/app.js` — Frontend logic (search, AI modal, RT modal, Nubimetrics modal)
- `static/style.css` — Dark theme UI styles
- `templates/index.html` — Main UI with all modals
- `templates/nubi_results.html` — Full-page Nubimetrics results (opens in new tab)
- `Procfile` — Gunicorn config (timeout 180s)

## Deploy
```bash
railway up --detach --service melichnicho
```
Railway binary is at `/usr/local/bin/railway`. Autodeploy via GitHub push IS working (triggers automatically on push to master).
Needs `NODE_EXTRA_CA_CERTS` env var set if machine has AVG antivirus (SSL interception).

## Known issues
- `sold_quantity` always 0 because `/items/{id}` is blocked from Railway IPs since April 2025
- Apify search takes 20-30 seconds (scraper startup time)
- Free Railway plan has resource limits

---

## Features added 2026-06-29

### 📊 Real Trends integration (XLSX upload)
**Button:** "📊 Real Trends" (teal, in the search bar)

**Flow:**
1. In Real Trends: Mercado → select category → Detalle → Exportar (downloads `.xlsx`)
2. In MeLi Nicho: click "📊 Real Trends" → upload XLSX → see ranking table
3. Click "🤖 Analizar oportunidades con IA" → Claude analyzes and returns market insights

**Backend endpoints:**
- `POST /api/rt-upload` — receives XLSX, parses with `openpyxl`, returns ranked products JSON
- `POST /api/rt-analyze` — receives products array, calls Claude, returns markdown analysis

**Column detection:** auto-detects headers by keyword (`unidades`, `título`, `vendedor`, `precio`, `facturación`)

---

### 📈 Nubimetrics integration (CSV upload — client-side processing + full-page results)
**Button:** "📈 Nubimetrics" (orange, in the search bar)

**Flow:**
1. In Nubimetrics: Mercado Avanzado → Items → Exportar CSV (downloads large CSV, ~85MB)
2. In MeLi Nicho: click "📈 Nubimetrics" → upload CSV → click "Procesar"
3. CSV is parsed **entirely in the browser** (no server upload — avoids Railway size/timeout limits)
4. Aggregated JSON (~35KB) is saved to `localStorage` key `nubiResultsData`
5. A **new tab** opens at `/nubi-results` — full-page results view
6. From that page: "🤖 Analizar con IA" and "↓ Exportar Excel" buttons call the backend

**Why client-side:** The CSV is 85MB. Railway's proxy and Gunicorn would timeout or reject the upload. FileReader API reads it locally; only the small aggregated result goes to the server.

**Client-side logic (app.js):**
- `parseCSV(text)` — handles quoted fields
- `aggregateNubi(rows)` — groups by `Categoria_Nivel_4`, computes units, revenue, unique sellers, avg/median price, % Full, % free shipping, top3 concentration, top products/sellers per subcategory
- `uploadNubiFile()` — reads CSV, aggregates, saves to localStorage, opens `/nubi-results` in new tab

**Full-page results (`/nubi-results` → `templates/nubi_results.html`):**
- Sticky header: category name, period, listings / unidades / facturación (format: $5.9B) / vendedores / subcategorías
- Sortable subcategory table (click column headers): listings, vendedores, unidades, revenue, precio mediano, concentración top3 (color-coded), Full%, envío gratis%
- Expandable rows (▶ ver): top 5 productos + top vendedores + métricas extra por subcategoría
- Top 15 productos globales table: título, subcategoría, unidades, precio máx, **facturación estimada** (unidades × precio_máx), vendedor
- AI analysis renders in cards per section (##), subsections (###) with left border, verdict pills 🟢/🟡/🔴 with colored background
- Export Excel button downloads multi-sheet XLSX

**Backend endpoints:**
- `GET /nubi-results` — serves `nubi_results.html` (reads data from localStorage client-side)
- `POST /api/nubi-analyze` — receives aggregated JSON, calls Claude, returns markdown analysis
- `POST /api/nubi-export` — receives aggregated JSON + analysis text, generates multi-sheet XLSX:
  - Sheet 1 "Resumen": meta stats + price segments
  - Sheet 2 "Subcategorías": full table with color-coded concentration
  - Sheet 3 "Top Productos": top 15 by units sold
  - Sheet 4 "Análisis IA": plain text of the Claude analysis

**Claude analysis prompt (`/api/nubi-analyze`):**
- Instructs Claude to use abbreviated formats: $5.9B, $520M, 14.4k (never full numbers)
- Requests ### per opportunity for structured rendering
- Covers: market summary, top 5 niches with verdicts, price segments, warnings, final recommendation

**Nubimetrics CSV columns used:**
- `Categoria_Nivel_4` (fallback: `Nivel_3`) — subcategory grouping
- `Titulo_Publicacion` — product title
- `Nickname_Vendedor` — seller
- `Unidades_Vendidas` — units sold (KEY metric)
- `Monto_Vendido_Moneda_Local` — revenue ARS
- `PrecioMonedaLocal` — price ARS
- `OfreceFull` — MeLi Full (Si/No)
- `Ofrece_Envio_Gratis` — free shipping (true/false)
- `Mes` — period

---

## Pending ideas
- Compare prices with Alibaba (Apify has Alibaba scraper too)
- On-demand Alibaba price lookup per product with margin calculation
- Nubimetrics: add price segment aggregation in client-side JS (currently simplified)
- Consider integrating multiple Nubimetrics CSV exports (different categories) for cross-category analysis
