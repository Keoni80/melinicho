# MeLi Nicho

Market analysis tool for finding profitable niches on MercadoLibre Argentina.

## Stack
- **Backend:** Flask (Python), Gunicorn (timeout 180s)
- **Frontend:** Vanilla JS/CSS
- **Database:** SQLite (melichnicho.db)
- **AI:** Claude API (`claude-sonnet-4-6`) for niche analysis
- **Search:** Apify scraper as primary search (MeLi API search is blocked)
- **Deploy:** Railway — autodeploy on push to `master`; manual fallback: `~/.railway/bin/railway up --detach --service melinicho`
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
- `app.py` — Flask routes: search, discover, analyze (AI), export CSV, history, rt-upload, rt-analyze, nubi-analyze, nubi-export, nubi-results, store-sales, sourcing-analyze, sourcing-report
- `meli_api.py` — MeLi API + Apify integration, token refresh, visits enrichment
- `analyzer.py` — Opportunity scoring, niche stats, seller ranking
- `static/app.js` — Frontend logic (search, AI modal, RT modal, Nubimetrics modal, Mi Tienda modal, Sourcing modal)
- `static/style.css` — Dark theme UI styles
- `templates/index.html` — Main UI with all modals
- `templates/nubi_results.html` — Full-page Nubimetrics results (opens in new tab)
- `templates/sourcing_report.html` — Full-page Sourcing analysis report (opens in new tab)
- `Procfile` — Gunicorn config (timeout 180s)

## Deploy
Autodeploy via GitHub push IS working (triggers automatically on push to `master`). Manual deploy if needed:
```bash
~/.railway/bin/railway up --detach --service melinicho
```
Railway binary is at `~/.railway/bin/railway`.
Needs `NODE_EXTRA_CA_CERTS` env var set if machine has AVG antivirus (SSL interception).

## Known issues
- `sold_quantity` always 0 because `/items/{id}` is blocked from Railway IPs since April 2025
- Apify search takes 20-30 seconds (scraper startup time)
- Free Railway plan has resource limits

---

## Features added 2026-06-30

### 🏪 Mi Tienda (sales dashboard)
**Button:** "🏪 Mi Tienda" (blue, in the search bar)

**Flow:**
1. Click "Mi Tienda" → modal opens with recent sales summary
2. Click "🤖 Analizar con IA" → Claude analyzes sales performance, top products, trends, recommendations

**Backend endpoints:**
- `GET /api/sales-summary` — returns last 30 days of sales from SQLite (orders/items tables)
- `POST /api/store-analyze` — receives sales data JSON, calls Claude, returns markdown analysis

**Frontend (app.js):**
- `openStoreModal()` — fetches `/api/sales-summary`, renders table
- `analyzeStoreWithAI()` — POSTs to `/api/store-analyze`, renders result with `mdToHtml`

---

### 🎯 Sourcing module (multi-CSV AI product recommender)
**Button:** "🎯 Sourcing" (purple gradient `#4A148C` → `#7B1FA2`, in the search bar)

**Purpose:** Upload multiple Nubimetrics CSVs + set criteria → AI recommends best products to import and sell to hit a monthly revenue target.

**Flow:**
1. Click "🎯 Sourcing" → modal opens
2. Upload one or more Nubimetrics CSV files (drag & drop or click)
3. Fill criteria: objetivo de facturación (ARS), mín/máx productos, tipo de importación (courier/marítimo), tipo de cambio
4. Click "Analizar" → new tab opens at `/sourcing-report` (full-screen dark report)
5. AI analysis appears in the new tab via localStorage cross-tab communication

**Why localStorage cross-tab:** `window.open()` must be called synchronously before any `await` to avoid popup blocker. The new tab is opened first at `/sourcing-report`, then after the async API call completes, data is written to `localStorage` key `sourcing_report_data`. The report page listens for the `storage` event and renders on arrival.

**FOB pricing logic (in AI prompt):**
- Courier import multiplier: CIF × 1.975 (50% arancel + 31.5% IVA + 10% IVA adicional + 6% Ganancias)
- Marítimo import multiplier: CIF × 2.35 (higher duties, bulk shipments)
- Target margin: 30% recommended, 25% minimum

**Objetivo field formatting:** Uses `toLocaleString('es-AR')` for Argentine period-separated thousands (e.g., `30.000.000`). Stripped with `.replace(/\D/g, '')` before sending to API.

**CSV processing (entirely client-side):**
- Multiple files supported — stored per file in `sourcingFiles = [{id, name, rows}]` (app.js), flattened with `flatMap` at analyze time
- Each file in the list has a ✕ button (`removeSourcingFile()`) to drop it from the pool; removing all files hides the criteria panel
- `parseCSV(text)` handles quoted fields
- `aggregateSourcingProducts(rows, headerRow)` groups by `Titulo_Publicacion`, sums units/revenue across months, returns top 50 products sorted by units sold
- Only the aggregated ~50 products are sent to the server (avoids Railway timeout on large files)

**Key Nubimetrics columns used for aggregation:**
- `Titulo_Publicacion` — product grouping key
- `Unidades_Vendidas` — units sold (primary sort metric)
- `Monto_Vendido_Moneda_Local` — total revenue ARS
- `PrecioMonedaLocal` — price ARS
- `Categoria_Nivel_1` to `Nivel_4` — category hierarchy
- `Nickname_Vendedor` — seller count
- `OfreceFull`, `Ofrece_Envio_Gratis` — logistics flags
- `Mes` — period (used to deduplicate months)

**Backend endpoints:**
- `GET /sourcing-report` — serves `sourcing_report.html` (protected by `@login_required`)
- `POST /api/sourcing-analyze` — receives products (top 50), target_revenue, min_products, max_products, shipping, tc; calls `claude-sonnet-4-6` (max_tokens=4000); returns `{"analysis": text, "simulation": [...]}`
  - Claude embeds a ```json block at the end of the response with the simulation array
  - Backend extracts it with regex, strips it from the analysis text, returns both separately

**Simulation JSON format (returned by Claude, extracted server-side):**
```json
[{"producto": "Nombre corto", "precio_ars": 120000, "unidades_mes": 25, "revenue_mes": 3000000}, ...]
```

**Report page (`templates/sourcing_report.html`):**
- Full-screen dark theme (matches nubi_results.html style)
- Header with meta chips (criteria summary) + print button
- Loading spinner until data arrives via localStorage
- Renders `mdToHtml(analysisText)` result
- **Recuadro de simulación al final** (renderizado por `openSourcingReport()` en app.js, no por Claude):
  - Borde y encabezado en color accent (#FFE600)
  - Tabla: producto / precio de venta / unidades por mes / revenue mensual
  - Fila TOTAL con suma y diferencia vs. objetivo (verde si supera, rojo si falta)
- Auto-removes localStorage key after render

---

## Features added 2026-07-20

### 🔥 Nichos populares + cascada de subcategorías (búsqueda principal)
- **Chips de sugerencias** debajo del search-row (`#suggestions-row`): `GET /api/suggestions` rankea búsquedas previas de la tabla `searches` (frecuencia ×10 + bonus por poca competencia según `unique_sellers` del `niche_stats`); completa hasta 8 con `FALLBACK_SUGGESTIONS` curadas (app.py). Click en chip → keyword search. Chip con borde accent (`chip-hot`) = competencia "Baja".
- **Cascada de subcategorías**: al elegir categoría con hijas aparece otro select al lado (`#subcat-slots`, hasta 4-5 niveles MeLi). `GET /api/categories/<id>/children` envuelve `get_subcategories()`. La búsqueda usa `effectiveCategoryId()` (el select más profundo con valor). Cada opción muestra `total_items_in_this_category` abreviado. Cambiar un nivel superior destruye los selects más profundos.
- **Historial**: `performSearch(overrideCategoryId)` — el click en historial pasa el `category_id` guardado (puede ser subcategoría que no existe en los selects visibles).

---

## Features added 2026-07-15

### 📦 mis productos (monitoreo de operación propia)
**Button:** "📦 mis productos" (cyan gradient `#006064` → `#00ACC1`, in the search bar) → opens `/mis-productos` in a new tab (standalone page, no localStorage handoff: the page fetches its own JSON).

**Purpose:** table of all own active listings with: stock split (tu depósito vs Full), final price with active promotion, sales last 30 days per product, search-ranking position, user-defined monthly projection, and per-product AI sales strategy when selling < 50% of projection.

**Key mechanics:**
- **Grouping:** multiple listings of the same product (e.g. one Full + one self-shipped) collapse into one row. Heuristic: shared `inventory_id` (MeLi user-product) OR shared `catalog_product_id` OR normalized-title match, then reconciled against `product_config.item_ids` (manual merges survive re-grouping). Manual merge via 🔗 button → `POST /api/mis-productos/merge`.
- **Stock Full:** items with `shipping.logistic_type == "fulfillment"` → `GET /inventories/{inventory_id}/stock/fulfillment` (available_quantity), deduped by inventory_id (several pubs can share stock). "Tu depósito" = `available_quantity` of non-fulfillment pubs (`xd_drop_off`).
- **Final price:** the `/items` multiget does NOT return promotions (`sale_price` comes null even with an active deal) → `GET /items/{id}/prices` per item, parallelized with ThreadPoolExecutor(8) (`get_items_prices`). `promotion` type < standard = precio final. Works from server IPs (own items, OAuth).
- **Sales 30d per product:** `fetch_sales_by_item` clones `fetch_orders_total` but aggregates `order_items[].item.id`. Both now retry on `429 local_rate_limited` (orders API rate-limits after ~20 consecutive pages).
- **Position:** `get_item_position` runs ONE Apify search (`~25s`, keyword stored per product, editable in UI; default = `derive_keyword(title)` first 3 significant tokens). Match own listing by item_id, permalink, seller_id **and catalog_product_id** — catalog listings appear as `/p/MLA...` URLs with the catalog id, not the listing id (this was the original bug: own product at #2 not recognized). Result cached in `product_config` (`position`, `position_total`, `position_ts`, `position_competitors` = top-15 JSON). NEVER runs on page load — only per-row ↻ button or sequential "update all" loop client-side. Changing the keyword invalidates the cache.
- **AI strategy:** button appears when `sales_30d_units < 0.5 * proyeccion_mes` (computed server-side, `needs_strategy`). `POST /api/mis-productos/estrategia` requires cached `position_competitors` (400 otherwise). Margin is precomputed in Python (`precio_final×0,85 − envío($7.000 si ≥$33k) − landed`) and injected into the prompt so Claude never invents arithmetic. Streaming keep-alive pattern, `claude-sonnet-4-6`, max_tokens=3000. Prompt includes the real top-15 listing with own row marked "→ VOS".

**DB:** table `product_config` (item_ids JSON, landed_cost_ars, proyeccion_mes, keyword, position cache). Created in `init_products_table()`. **`DB_PATH` is now env-configurable** — set `DB_PATH=/data/melichnicho.db` + a Railway volume mounted at `/data` for persistence (without it the container DB is wiped per deploy).

**Endpoints:** `GET /mis-productos` (page), `GET /api/mis-productos` (~30s load: items + orders 30d + prices + fulfillment stock), `POST /api/mis-productos/config` (partial update: proyeccion_mes / landed_cost_ars / keyword), `POST /api/mis-productos/position`, `POST /api/mis-productos/merge`, `POST /api/mis-productos/estrategia` (streaming).

**Files:** `templates/mis_productos.html` (standalone, inline helpers — do NOT include app.js), `meli_api.py` (`fetch_sales_by_item`, `get_items_prices`, `get_fulfillment_stock`, `get_item_position`, `get_items_catalog_ids`, `derive_keyword`, `_refresh_lock` for thread-safe token refresh), `app.py` (grouping + endpoints at the end).

---

## Features added 2026-07-04

### 🕵️ Buscador de Competidores module (vendedores con producto estrella)
**Button:** "🕵️ Buscar Competidores" (blue gradient `#1565C0` → `#42A5F5`, in the search bar)

**Purpose:** Upload category CSVs from Nubimetrics (same as Sourcing/Nicho) → find SELLERS whose revenue is concentrated in a few hero products with high rotation (the "modelo" competitors worth studying — opposite of long-tail sellers like TODOMICRO).

**Flow:** mirrors Nicho — client-side CSV parsing, criteria (concentración top3 % default 60, rotación mínima del producto estrella default 100 u/mes, facturación mínima default $10M formatted es-AR, TC autofilled from DolarApi). Report at `/buscomp-report` via localStorage key `buscomp_report_data`.

**Aggregation (`aggregateBuscompSellers` in app.js):** groups by `Nickname_Vendedor` → per seller: revenue, units, publicaciones (unique titles), top1_share/top3_share (% of seller revenue in top 1/3 products by revenue), estrellas = top 3 products with unidades_mes, revenue_mes, precio_real (monto ÷ unidades). Filter: revenue ≥ min, top3_share ≥ min, star units ≥ min. Deterministic table shows up to 30 sellers with their star product.

**Key caveat (in table note + AI prompt):** Nubimetrics nicknames are ANONYMIZED — identify the real seller by searching the star product title on MercadoLibre.

**AI analysis (`POST /api/buscomp-analyze`):** user context (courier ops, factory-direct FOB ~1/3 Alibaba listings) + 4× rule. Output per seller: business profile (hero+variants / marca propia / escalera), star product courier-viability with FOB/margin math, risks, 🟢🟡🔴 verdict; final summary table + "📋 Próximos pasos" pointing 🟢 sellers to the ⚔️ Competidores module (export their catalog from Nubimetrics).

**Pipeline:** 🕵️ finds WHO to study from category CSVs → export that seller's catalog XLSX → ⚔️ analyzes their full catalog for attackable gaps.

---


### ⚔️ Competidores module (análisis de catálogo por vendedor)
**Button:** "⚔️ Competidores" (red gradient `#B71C1C` → `#E53935`, in the search bar)

**Purpose:** Upload per-seller catalog XLSX exports from Nubimetrics (buscar vendedor → Items → Exportar; filename = nickname) → competitor profile + attackable gaps for courier import.

**Flow:** multi-XLSX upload (parsed server-side with openpyxl via `POST /api/comp-upload`, files ~1MB); each file shows inline stats on load ($XXXm · pubs · top10%). TC input autofilled from DolarApi via generic `fetchDolarInto(inputId, hintId)` (shared helper, `dolarManual` map guards manual edits). Report opens at `/comp-report` via localStorage key `comp_report_data`.

**Server-side stats per seller (`/api/comp-upload`):** revenue_mes, unidades, publicaciones, ticket, top10_share (% revenue in top 10 pubs — ≥60% = concentrated seller worth studying), pct_full, pct_catalogo, top brands with share, top 20 products with **precio real = Ventas $ ÷ Ventas en Unid.** (units come rounded in bands).

**AI analysis (`POST /api/comp-analyze`):** prompt includes the user's context (imports via courier: CO sensors, protectores de tensión; factory-direct FOB ~1/3 of Alibaba listings) and the **courier 4× rule** (neto = precio×0,85 − $7.000; landed = FOB × 1,975 × TC). Output: per-competitor profile (concentrado vs cola larga, marca propia vs revendedor), attackable gaps with FOB/margin math and 🟢🟡🔴 verdicts, premium-ladder plays, final prioritized recommendation.

**XLSX columns (Nubimetrics per-seller export):** Título, Marca, Ventas en $, Ventas en Unid., Precio Promedio, Tipo de Publicación, Fulfillment, Catálogo, Con Envío Gratis, SKU.

---


### 💎 Nicho module (alta rotación + pocos vendedores)
**Button:** "💎 Nicho" (pink gradient `#AD1457` → `#EC407A`, in the search bar)

**Purpose:** Upload the same Nubimetrics CSVs as Sourcing → find products with high rotation (units/month) and few sellers.

**Flow:** mirrors Sourcing — multi-CSV client-side parsing, criteria (rotación mínima u/mes default 50, máx vendedores default 3), report opens in new tab at `/nicho-report` via localStorage key `nicho_report_data`.

**Aggregation (`aggregateNichoProducts` in app.js):** groups by `Titulo_Publicacion`; per product: unidades_mes, **precio_real = Monto_Vendido ÷ Unidades** (never list price), revenue_mes, unique sellers. Category levels with literal `"-"` are treated as empty (Nubimetrics quirk). Deterministic table = products meeting criteria sorted by units/sellers ratio.

**Key caveat (handled in the AI prompt):** the same product appears under different titles per seller, so seller counts per exact title UNDERESTIMATE competition. `/api/nicho-analyze` instructs Claude to cluster similar titles before ranking, and to output a "⚠️ Falsos nichos" section for single-seller titles that are actually fragmented competitive markets.

**Backend:** `GET /nicho-report` (template `nicho_report.html`, copy of sourcing report), `POST /api/nicho-analyze` (streaming with keep-alive spaces, same pattern as sourcing).

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

## Nubimetrics data files

Los CSVs de Nubimetrics **no están en este repo** (demasiado grandes: 588 MB total, archivos hasta 97 MB).
Están en **Google Drive** del usuario (jderoberto@gmail.com) y copia local en `~/Downloads/data/`.

Categorías disponibles (2026-06-30):
- Alarmas y sensores (86 MB), Amplificadores (1.2 MB), Barbería (16 MB)
- Destornilladores (8.3 MB), Fitness y musculación (88 MB), Herrajes de seguridad (90 MB)
- Herramientas eléctricas (97 MB), Instrumentos a cuerda (37 MB), Mochilas notebook (3.4 MB)
- OBD (4.1 MB), Pilates y yoga (88 MB), Proyectores y pantallas (7.6 MB)
- Set de destornilladores (8.6 MB), Teclados musicales (2.3 MB), Tester y multímetros (54 MB)

Estructura de los CSVs (verificado 2026-07-02):
- Filas **diarias** (columna `Mes` = fecha YYYY-MM-DD), cubren un mes calendario. Para totales mensuales se suma; para precio real usar `Monto_Vendido / Unidades_Vendidas` — **nunca** `PrecioMonedaLocal` máximo (puede ser 2× el precio real de venta)
- `Nickname_Vendedor` viene **anonimizado** (ej. "gorrion.cobre.ineficaz"); títulos de producto reales; `AI_Product_Name` vacío
- Duplicados exactos conocidos: `timbres.csv` = `alarmas y sensores.csv`; `pilates y yoga.csv` = `fitness y musculacion.csv`
- Nubimetrics también exporta el catálogo de **un vendedor puntual** como XLSX (columnas: Título, Marca, Ventas en $, Ventas en Unid., Precio Promedio, Fulfillment, etc.) — guardados en `~/Downloads/Competidores/` (TODOMICRO, GENEVE). Unidades redondeadas en bandas. Reemplaza el scraping Apify del módulo Competidores a costo $0

---

## Pending ideas
- Compare prices with Alibaba (Apify has Alibaba scraper too)
- On-demand Alibaba price lookup per product with margin calculation — FOB lookup + landed cost calculation at current TC
- Nubimetrics: add price segment aggregation in client-side JS (currently simplified)
- Sourcing: show per-product margin simulation table in the report (FOB input → landed cost → suggested price → MG%)
- Sourcing: add supplier search via Alibaba Apify scraper for top recommended products
