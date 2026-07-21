import logging
import os
import threading
import time
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.mercadolibre.com"
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

_access_token = None
_refresh_lock = threading.Lock()


def _load_env():
    global _access_token
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    _access_token = os.environ.get("MELI_ACCESS_TOKEN")


_load_env()


def _refresh_token():
    global _access_token
    token_before = _access_token
    with _refresh_lock:
        # El refresh token es de un solo uso: si otro thread renovó mientras
        # esperábamos el lock, reusar su token en vez de quemar el refresh nuevo.
        if _access_token != token_before:
            return True
        return _refresh_token_locked()


def _refresh_token_locked():
    global _access_token
    client_id     = os.environ.get("MELI_CLIENT_ID")
    client_secret = os.environ.get("MELI_CLIENT_SECRET")
    refresh       = os.environ.get("MELI_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh]):
        return False

    try:
        resp = requests.post(
            f"{BASE_URL}/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        return False

    data = resp.json()
    new_access  = data.get("access_token")
    new_refresh = data.get("refresh_token")

    if not new_access:
        return False

    _access_token = new_access
    os.environ["MELI_ACCESS_TOKEN"] = new_access
    if new_refresh:
        os.environ["MELI_REFRESH_TOKEN"] = new_refresh

    _write_env(new_access, new_refresh)
    return True


def _write_env(new_access, new_refresh):
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH) as f:
        lines = f.readlines()

    replacements = {"MELI_ACCESS_TOKEN": new_access}
    if new_refresh:
        replacements["MELI_REFRESH_TOKEN"] = new_refresh

    out = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in replacements:
            out.append(f"{key}={replacements[key]}\n")
        else:
            out.append(line)

    with open(_ENV_PATH, "w") as f:
        f.writelines(out)


def _headers():
    h = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    if _access_token:
        h["Authorization"] = f"Bearer {_access_token}"
    return h


def _get(url, **kwargs):
    resp = requests.get(url, headers=_headers(), **kwargs)
    if resp.status_code in (401, 403) and _refresh_token():
        log.info("Token refreshed, retrying %s", url)
        resp = requests.get(url, headers=_headers(), **kwargs)
    return resp



def get_categories():
    try:
        resp = _get(f"{BASE_URL}/sites/MLA/categories", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _search_apify(query="", category_id="", max_results=100):
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        log.error("APIFY_API_TOKEN not set")
        return None

    if not query and category_id:
        # El actor de Apify solo busca por keyword: usar el nombre de la
        # categoría como keyword (/categories/{id} sigue accesible desde servidores)
        try:
            resp = _get(f"{BASE_URL}/categories/{category_id}", timeout=10)
            data = resp.json()
            # Nombres de hoja tipo "De Mano" u "Otros" son malos keywords:
            # combinar los últimos dos niveles del path (ej. "taladros de mano").
            # En minúsculas: el actor devuelve 0 items con keywords capitalizadas
            path = [p.get("name", "") for p in data.get("path_from_root", [])]
            query = (" ".join(path[-2:]) if len(path) >= 2 else data.get("name", "")).lower()
            log.info("Apify: categoría %s resuelta a keyword '%s'", category_id, query)
        except Exception as e:
            log.error("Apify: no se pudo resolver categoría %s: %s", category_id, e)

    if not query:
        log.error("Apify: búsqueda sin keyword ni categoría resoluble")
        return None

    log.info("Apify scraper: keyword='%s'", query)
    data = []
    for attempt in (1, 2):  # el actor a veces devuelve 0 items; un reintento suele alcanzar
        try:
            resp = requests.post(
                "https://api.apify.com/v2/acts/karamelo~mercadolibre-scraper-espanol-castellano/run-sync-get-dataset-items",
                params={"token": token},
                json={
                    "keyword": query,
                    "country": "https://listado.mercadolibre.com.ar/",
                    "maxPages": 1,
                },
                timeout=160,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Apify search failed (intento %d): %s", attempt, e)
            return None
        if data:
            break
        log.warning("Apify devolvió 0 items para '%s' (intento %d)", query, attempt)

    log.info("Apify returned %d items", len(data))
    import re
    items = []
    for r in data:
        price_str = r.get("nuevoPrecio", "0") or "0"
        try:
            price = float(str(price_str).replace(".", "").replace(",", "."))
        except (ValueError, TypeError):
            price = 0
        envio = str(r.get("Envio", "")).lower()
        # El actor de Apify renombró este campo de "zdireccion" a "zProductoLink"
        # en algún momento; se mantiene el nombre viejo como fallback por si vuelve a cambiar.
        url = r.get("zProductoLink") or r.get("zdireccion", "")

        # Apify returns two URL types:
        # 1. articulo.mercadolibre.com.ar/MLA-1234567890-... → individual listing ID (works with /visits/items)
        # 2. mercadolibre.com.ar/.../p/MLA12345678 → catalog product ID (doesn't work with /visits/items)
        # Store both so enrich_with_visits can resolve catalog IDs to real listing IDs.
        raw_id = r.get("SKU", "")
        is_catalog = bool(re.search(r'/p/MLA', url))

        items.append({
            "id": raw_id,
            "title": r.get("articuloTitulo", ""),
            "price": price,
            "currency": r.get("Moneda", "ARS"),
            "sold_quantity": 0,
            "available_quantity": 0,
            "condition": "",
            "seller_id": r.get("sellerID", ""),
            "seller_name": r.get("Vendedor", "") or r.get("productoMarca", ""),
            "seller_level": "",
            "free_shipping": "gratis" in envio or "free" in envio or "full" in envio,
            "permalink": url,
            "thumbnail": r.get("imgDireccion", ""),
            "listing_type": "catalog" if is_catalog else "",
            "visits": 0,
            "_is_catalog": is_catalog,
        })
    return items[:max_results] if items else None


def _resolve_catalog_to_listing(product_id):
    """Resolve a catalog product ID to its top listing ID and unique seller count.

    Returns (listing_id, seller_count) or (None, 0).
    """
    try:
        resp = _get(
            f"{BASE_URL}/products/{product_id}/items",
            params={"limit": 10, "status": "active"},
            timeout=8,
        )
        if resp.ok:
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None, 0
            listing_id = results[0].get("item_id")
            unique_sellers = len({r.get("seller_id") for r in results if r.get("seller_id")})
            # paging.total tells us how many total listings exist for this product
            total_listings = data.get("paging", {}).get("total", unique_sellers)
            return listing_id, max(unique_sellers, 1)
    except Exception as e:
        log.warning("catalog resolve failed for %s: %s", product_id, e)
    return None, 0


def enrich_with_visits(items):
    """Add visit counts to items using MeLi's /visits/items endpoint (1 ID per request).

    Catalog product IDs (from /p/ URLs) are resolved to individual listing IDs first
    so the visits endpoint can return real data.
    """
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue

        # Catalog items need resolution to individual listing IDs
        if item.get("_is_catalog"):
            listing_id, seller_count = _resolve_catalog_to_listing(item_id)
            if listing_id:
                item["_resolved_id"] = listing_id
                item["catalog_seller_count"] = seller_count
                log.info("Resolved catalog %s → listing %s (%d sellers)", item_id, listing_id, seller_count)
            time.sleep(0.1)

    visits_map = {}
    for item in items:
        query_id = item.get("_resolved_id") or item.get("id")
        if not query_id:
            continue
        try:
            resp = _get(
                f"{BASE_URL}/visits/items",
                params={"ids": query_id},
                timeout=8,
            )
            if resp.ok:
                v = resp.json().get(query_id, 0)
                visits_map[item["id"]] = v
        except Exception as e:
            log.warning("visits enrichment failed for %s: %s", query_id, e)
        time.sleep(0.1)

    hits = sum(1 for v in visits_map.values() if v > 0)
    log.info("Visits enrichment: %d items, %d with actual visits", len(visits_map), hits)

    for item in items:
        item["visits"] = visits_map.get(item["id"], 0)
        item.pop("_is_catalog", None)
        item.pop("_resolved_id", None)

    return items


def search_meli(query="", category_id="", max_results=100):
    params = {"limit": 50}
    if query:
        params["q"] = query
    if category_id:
        params["category"] = category_id

    all_items = []
    offset = 0

    while len(all_items) < max_results:
        params["offset"] = offset
        try:
            resp = _get(f"{BASE_URL}/sites/MLA/search", params=params, timeout=15)
            if resp.status_code == 403:
                log.info("MeLi search returned 403, trying Apify fallback")
                apify_items = _search_apify(query, category_id, max_results)
                if apify_items:
                    enriched = enrich_with_visits(apify_items)
                    return {"items": enriched, "total": len(enriched)}
                return {"error": "La búsqueda de MercadoLibre no está disponible y el scraper Apify falló."}
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            return {"error": "La API de MercadoLibre tardó demasiado. Intentá de nuevo."}
        except requests.exceptions.HTTPError:
            log.info("MeLi search HTTP error, trying Apify fallback")
            apify_items = _search_apify(query, category_id, max_results)
            if apify_items:
                enriched = enrich_with_visits(apify_items)
                return {"items": enriched, "total": len(enriched)}
            return {"error": "Error al consultar MercadoLibre y el scraper Apify falló."}
        except Exception as e:
            return {"error": f"Error al consultar MercadoLibre: {e}"}

        results = data.get("results", [])
        if not results:
            break

        all_items.extend(_parse_item(r) for r in results)
        offset += len(results)

        total = data.get("paging", {}).get("total", 0)
        if offset >= total or offset >= max_results:
            break

        time.sleep(0.25)

    return {"items": all_items[:max_results], "total": len(all_items)}


def get_subcategories(category_id):
    try:
        resp = _get(f"{BASE_URL}/categories/{category_id}", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        children = data.get("children_categories", [])
        log.info("get_subcategories %s → %d children", category_id, len(children))
        return [{"id": c["id"], "name": c["name"], "total_items_in_this_category": c.get("total_items_in_this_category", 0)} for c in children]
    except Exception as e:
        log.error("get_subcategories %s failed: %s", category_id, e)
        return []


def sample_subcategory(category_id, limit=5):
    path = f"/highlights/MLA/category/{category_id}"
    try:
        resp = _get(f"{BASE_URL}{path}", timeout=10)
        resp.raise_for_status()
        content = resp.json().get("content", [])
    except Exception as e:
        log.error("sample_subcategory highlights %s failed: %s", category_id, e)
        return None

    product_ids = [item["id"] for item in content[:limit]]
    if not product_ids:
        return None

    items = []
    for pid in product_ids:
        try:
            r_prod = _get(f"{BASE_URL}/products/{pid}", timeout=8)
            if not r_prod.ok:
                continue
            prod = r_prod.json()

            r_listing = _get(f"{BASE_URL}/products/{pid}/items", params={"limit": 1}, timeout=8)
            if not r_listing.ok:
                continue
            results = r_listing.json().get("results", [])
            if not results:
                continue
            listing = results[0]

            items.append({
                "title": prod.get("name", ""),
                "price": listing.get("price", 0) or 0,
                "sold_quantity": 0,
                "seller_id": str(listing.get("seller_id", "")),
                "seller_name": "",
            })
        except Exception:
            continue

    if not items:
        return None

    sellers = {i["seller_id"] for i in items if i.get("seller_id")}
    prices = [i["price"] for i in items if i.get("price", 0) > 0]

    return {
        "sampled": len(items),
        "unique_sellers": len(sellers),
        "avg_price": round(sum(prices) / len(prices), 2) if prices else 0,
        "median_price": round(sorted(prices)[len(prices) // 2], 2) if prices else 0,
        "top_items": [{"title": i["title"], "price": i["price"]} for i in items[:5]],
    }


def get_my_user_id():
    resp = _get(f"{BASE_URL}/users/me", timeout=10)
    if not resp.ok:
        return None
    return resp.json().get("id")


def fetch_orders_total(user_id, date_from, date_to):
    """Returns (total_amount, order_count) for paid orders in the given date range."""
    total_amount = 0.0
    order_count = 0
    offset = 0
    retries_429 = 0
    while True:
        r = _get(f"{BASE_URL}/orders/search", params={
            "seller": user_id,
            "order.date_created.from": date_from,
            "order.date_created.to": date_to,
            "limit": 50,
            "offset": offset,
        }, timeout=15)
        if r.status_code == 429 and retries_429 < 6:
            retries_429 += 1
            time.sleep(1.5 * retries_429)
            continue
        if not r.ok:
            log.warning("orders/search failed: %s %s", r.status_code, r.text[:200])
            break
        retries_429 = 0
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for order in results:
            status = order.get("status", "")
            if status == "paid":
                total_amount += float(order.get("total_amount", 0) or 0)
                order_count += 1
            elif status == "partially_refunded":
                # paid_amount ya descuenta lo reembolsado
                total_amount += float(order.get("paid_amount", 0) or 0)
                order_count += 1
        total = data.get("paging", {}).get("total", 0)
        offset += len(results)
        if offset >= total:
            break
        time.sleep(0.05)
    return round(total_amount), order_count


def get_my_store_items():
    """Fetch all seller's own items with sold_quantity via authenticated API."""
    resp = _get(f"{BASE_URL}/users/me", timeout=10)
    if not resp.ok:
        return None, "Token inválido. Actualizá el MELI_ACCESS_TOKEN."
    user_id = resp.json().get("id")
    if not user_id:
        return None, "No se pudo obtener el user_id."

    all_ids = []
    offset = 0
    while True:
        r = _get(
            f"{BASE_URL}/users/{user_id}/items/search",
            params={"limit": 100, "offset": offset},
            timeout=15,
        )
        if not r.ok:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        all_ids.extend(results)
        total = data.get("paging", {}).get("total", 0)
        offset += len(results)
        if offset >= total:
            break
        time.sleep(0.1)

    if not all_ids:
        return [], None

    attrs = ("id,title,price,sold_quantity,available_quantity,status,category_id,"
             "thumbnail,permalink,catalog_product_id,inventory_id,shipping,attributes,user_product_id")
    items = []
    for i in range(0, len(all_ids), 20):
        batch = all_ids[i:i+20]
        r = _get(
            f"{BASE_URL}/items",
            params={"ids": ",".join(batch), "attributes": attrs},
            timeout=15,
        )
        if r.ok:
            for entry in r.json():
                if entry.get("code") == 200:
                    b = entry.get("body", {})
                    price = b.get("price", 0) or 0
                    sold  = b.get("sold_quantity", 0) or 0
                    items.append({
                        "id":                 b.get("id", ""),
                        "title":              b.get("title", ""),
                        "price":              price,
                        "sold_quantity":      sold,
                        "available_quantity": b.get("available_quantity", 0) or 0,
                        "status":             b.get("status", ""),
                        "category_id":        b.get("category_id", ""),
                        "thumbnail":          (b.get("thumbnail") or "").replace("http://", "https://"),
                        "permalink":          b.get("permalink", ""),
                        "revenue":            round(price * sold),
                        "catalog_product_id": b.get("catalog_product_id") or "",
                        "inventory_id":       b.get("inventory_id") or "",
                        "logistic_type":      (b.get("shipping") or {}).get("logistic_type", ""),
                        "iva_pct":            _parse_iva_pct(b.get("attributes") or []),
                        "user_product_id":    b.get("user_product_id") or "",
                    })
        time.sleep(0.15)

    items.sort(key=lambda x: -x["revenue"])
    return items, None


def _parse_iva_pct(attributes):
    """Extrae el % de IVA cargado en la publicación (atributo VALUE_ADDED_TAX, ej. '10.5 %')."""
    import re
    for attr in attributes:
        if attr.get("id") == "VALUE_ADDED_TAX":
            m = re.search(r"[\d.,]+", attr.get("value_name") or "")
            if m:
                return float(m.group().replace(",", "."))
    return None


def fetch_sales_by_item(user_id, date_from, date_to):
    """Returns {item_id: {"units": int, "revenue": float}} for paid orders in range."""
    sales = {}
    offset = 0
    retries_429 = 0
    while True:
        r = _get(f"{BASE_URL}/orders/search", params={
            "seller": user_id,
            "order.date_created.from": date_from,
            "order.date_created.to": date_to,
            "limit": 50,
            "offset": offset,
        }, timeout=15)
        if r.status_code == 429 and retries_429 < 6:
            retries_429 += 1
            time.sleep(1.5 * retries_429)
            continue
        if not r.ok:
            log.warning("orders/search failed: %s %s", r.status_code, r.text[:200])
            break
        retries_429 = 0
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for order in results:
            if order.get("status", "") not in ("paid", "partially_refunded"):
                continue
            for oi in order.get("order_items", []):
                item_id = (oi.get("item") or {}).get("id")
                if not item_id:
                    continue
                qty = int(oi.get("quantity", 0) or 0)
                unit_price = float(oi.get("unit_price", 0) or 0)
                s = sales.setdefault(item_id, {"units": 0, "revenue": 0.0})
                s["units"] += qty
                s["revenue"] += qty * unit_price
        total = data.get("paging", {}).get("total", 0)
        offset += len(results)
        if offset >= total:
            break
        time.sleep(0.05)
    for s in sales.values():
        s["revenue"] = round(s["revenue"])
    return sales


def get_items_prices(item_ids, max_workers=8):
    """Fetch current prices (with active promotions) via /items/{id}/prices.

    El multiget de /items no trae promociones (sale_price viene null aun con
    promo activa), por eso se consulta por item, en paralelo.
    Returns {item_id: {"standard": float, "deal": float|None, "deal_end": str|None}}.
    """
    from concurrent.futures import ThreadPoolExecutor

    def fetch(item_id):
        try:
            r = _get(f"{BASE_URL}/items/{item_id}/prices", timeout=10)
            if not r.ok:
                return item_id, None
            out = {"standard": None, "deal": None, "deal_end": None}
            for p in r.json().get("prices", []):
                if p.get("type") == "standard":
                    out["standard"] = p.get("amount")
                elif p.get("type") == "promotion":
                    # puede haber más de una promo: quedarse con la más barata
                    if out["deal"] is None or (p.get("amount") or 0) < out["deal"]:
                        out["deal"] = p.get("amount")
                        out["deal_end"] = (p.get("conditions") or {}).get("end_time")
            return item_id, out
        except Exception as e:
            log.warning("prices failed for %s: %s", item_id, e)
            return item_id, None

    prices = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for item_id, data in ex.map(fetch, item_ids):
            if data:
                prices[item_id] = data
    return prices


def get_items_catalog_ids(item_ids):
    """Returns the set of catalog_product_ids of the given own items."""
    out = set()
    for i in range(0, len(item_ids), 20):
        r = _get(
            f"{BASE_URL}/items",
            params={"ids": ",".join(item_ids[i:i+20]), "attributes": "id,catalog_product_id"},
            timeout=15,
        )
        if r.ok:
            for entry in r.json():
                cid = (entry.get("body") or {}).get("catalog_product_id")
                if cid:
                    out.add(cid)
    return out


def get_fulfillment_stock(inventory_id):
    """Available stock at MeLi Full warehouse, or None if unavailable."""
    if not inventory_id:
        return None
    try:
        r = _get(f"{BASE_URL}/inventories/{inventory_id}/stock/fulfillment", timeout=10)
        if r.ok:
            return r.json().get("available_quantity")
        log.warning("fulfillment stock failed for %s: %s", inventory_id, r.status_code)
    except Exception as e:
        log.warning("fulfillment stock failed for %s: %s", inventory_id, e)
    return None


def get_user_product_stock(user_product_id):
    """Stock por ubicación de un User Product (Multi-Origin Stock).

    Una publicación Full puede tener, a la vez, unidades en el depósito propio
    del vendedor (no enviadas a Full) y en el centro Full — /items/{id} solo
    expone el total visible para venta, no el desglose. Devuelve
    {"deposito": int, "full": int} o None si el vendedor no usa este modelo.
    """
    if not user_product_id:
        return None
    try:
        r = _get(f"{BASE_URL}/user-products/{user_product_id}/stock", timeout=10)
        if not r.ok:
            log.warning("user-product stock failed for %s: %s", user_product_id, r.status_code)
            return None
        locations = r.json().get("locations", [])
        full = sum(l.get("quantity", 0) or 0 for l in locations if l.get("type") == "meli_facility")
        deposito = sum(l.get("quantity", 0) or 0 for l in locations if l.get("type") != "meli_facility")
        return {"deposito": deposito, "full": full}
    except Exception as e:
        log.warning("user-product stock failed for %s: %s", user_product_id, e)
        return None


def derive_keyword(title):
    """Keyword de búsqueda por defecto: primeros 3 tokens significativos del título."""
    import re
    words = []
    for w in re.split(r"[\s/,()\-]+", title):
        w = w.strip(".").lower()
        if not w or w in _ES_STOP or len(w) < 3:
            continue
        words.append(w)
        if len(words) == 3:
            break
    return " ".join(words)


def get_item_position(item_ids, seller_id, keyword, catalog_ids=None):
    """Busca la keyword en el listado real de MeLi (vía Apify) y ubica la publicación propia.

    Los resultados de catálogo aparecen con el catalog_product_id (URL /p/MLA...) en vez
    del id de la publicación, por eso también se matchea contra catalog_ids.
    Returns (position 1-based | None, total_scanned, competitors): competitors son los
    primeros 15 resultados del listado, con la fila propia marcada is_mine.
    """
    results = _search_apify(keyword.lower(), max_results=60)
    if not results:
        return None, 0, []

    norm_ids = {i.replace("-", "") for i in item_ids if i}
    norm_ids |= {c.replace("-", "") for c in (catalog_ids or []) if c}
    position = None
    competitors = []
    for idx, it in enumerate(results, 1):
        rid = str(it.get("id") or "").replace("-", "")
        permalink = (it.get("permalink") or "").replace("-", "")
        is_mine = bool(
            rid in norm_ids
            or any(nid in permalink for nid in norm_ids)
            or (seller_id and str(it.get("seller_id") or "") == str(seller_id))
        )
        if is_mine and position is None:
            position = idx
        if idx <= 15:
            competitors.append({
                "pos": idx,
                "title": it.get("title", ""),
                "price": it.get("price", 0),
                "seller_name": it.get("seller_name", ""),
                "free_shipping": bool(it.get("free_shipping")),
                "is_mine": is_mine,
            })
    return position, len(results), competitors


def search_alibaba(query, limit=20):
    token = os.environ.get("APIFY_API_TOKEN")
    if not token or not query:
        return []
    log.info("Searching Alibaba via Apify: '%s'", query)
    try:
        resp = requests.post(
            "https://api.apify.com/v2/acts/devcake~alibaba-products-scraper/run-sync-get-dataset-items",
            params={"token": token},
            json={"queries": [query], "max_pages": 1},
            timeout=160,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            log.error("Alibaba Apify unexpected response: %s", data)
            return []
        log.info("Alibaba Apify returned %d items", len(data))
        return data
    except Exception as e:
        log.error("Alibaba Apify search failed: %s", e)
        return []


_ES_STOP = {
    "de", "en", "para", "con", "el", "la", "los", "las", "un", "una", "y", "a",
    "por", "del", "al", "se", "su", "lo", "le", "es", "no", "si", "the", "for",
    "and", "with", "new", "hombre", "mujer", "color", "negro", "blanco", "rojo",
    "azul", "verde", "talla", "talle", "marca", "modelo", "nuevo", "original",
}


def _match_score(meli_title, ali_name):
    import re
    def tokens(t):
        out = set()
        for w in re.split(r"[\s/,()\-]+", t):
            w = w.strip(".").lower()
            if not w or w in _ES_STOP:
                continue
            # Keep: proper nouns, model numbers (contain digit), or long words
            if w[0].isupper() or any(c.isdigit() for c in w) or len(w) >= 5:
                out.add(w)
        return out

    mt = tokens(meli_title)
    at = tokens(ali_name)
    if not mt:
        return 0.0
    return len(mt & at) / len(mt)


def enrich_with_alibaba(items, alibaba_raw, query=""):
    parsed = []
    for a in alibaba_raw:
        pmin = a.get("price_min")
        pmax = a.get("price_max") or pmin
        if pmin is None:
            continue
        parsed.append({
            "name": a.get("name", ""),
            "price_min": float(pmin),
            "price_max": float(pmax),
            "url": a.get("product_url", ""),
        })

    ali_search_url = f"https://www.alibaba.com/trade/search?SearchText={requests.utils.quote(query)}"

    if not parsed:
        for item in items:
            item["alibaba_price_min"] = None
            item["alibaba_price_max"] = None
            item["alibaba_url"] = None
        return items

    # Fallback: P25-P75 range across all results
    mids = sorted((p["price_min"] + p["price_max"]) / 2 for p in parsed)
    n = len(mids)
    fallback_min = round(mids[max(0, n // 4)], 2)
    fallback_max = round(mids[min(n - 1, (3 * n) // 4)], 2)

    for item in items:
        best_score = 0.0
        best = None
        for a in parsed:
            score = _match_score(item.get("title", ""), a["name"])
            if score > best_score:
                best_score = score
                best = a

        if best_score >= 0.3 and best:
            item["alibaba_price_min"] = round(best["price_min"], 2)
            item["alibaba_price_max"] = round(best["price_max"], 2)
            item["alibaba_url"] = best["url"] or ali_search_url
        else:
            item["alibaba_price_min"] = fallback_min
            item["alibaba_price_max"] = fallback_max
            item["alibaba_url"] = ali_search_url

    return items


def _parse_item(raw):
    seller   = raw.get("seller", {})
    shipping = raw.get("shipping", {})
    return {
        "id":                 raw.get("id", ""),
        "title":              raw.get("title", ""),
        "price":              raw.get("price", 0) or 0,
        "currency":           raw.get("currency_id", "ARS"),
        "sold_quantity":      raw.get("sold_quantity", 0) or 0,
        "available_quantity": raw.get("available_quantity", 0) or 0,
        "condition":          raw.get("condition", ""),
        "seller_id":          seller.get("id", ""),
        "seller_name":        seller.get("nickname", ""),
        "seller_level":       seller.get("power_seller_status") or "",
        "free_shipping":      bool(shipping.get("free_shipping")),
        "permalink":          raw.get("permalink", ""),
        "thumbnail":          raw.get("thumbnail", ""),
        "listing_type":       raw.get("listing_type_id", ""),
    }
