"""
scrapers.py
-----------
All extraction logic lives here, isolated from dlt/orchestration concerns.

Design principle (this is the bit interviewers actually probe on):
Scraping code breaks constantly because CSS classes change on every
redesign. So we prefer, in order of stability:

    1. A platform's own JSON API (e.g. Shopify's <handle>.json)
    2. Structured metadata the site puts in <head> for SEO
       (Open Graph meta tags, schema.org JSON-LD)
    3. A hidden internal API the frontend calls (needs manual discovery)
    4. Raw CSS selectors (last resort, most fragile)

Every scrape_* function returns a plain dict or None. None means
"couldn't get a price at all" -- that's different from a $0 or
suspicious price, which is handled downstream as a Data Quality failure.
"""

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {
    # Identify the bot honestly -- don't spoof a browser UA to sneak past
    # blocks. If a site wants to block bots via robots.txt, respect that
    # instead of fighting it.
    "User-Agent": (
        "Mozilla/5.0 (compatible; FakeDiscountDeceptionEngine/1.0; "
        "portfolio project; +https://github.com/YOUR_USERNAME/fake-discount-deception-engine)"
    )
}
REQUEST_TIMEOUT = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_record(product: dict, **fields) -> dict:
    return {
        "product_name": product["product_name"],
        "category": product["category"],
        "site": product["site"],
        "url": product["url"],
        "scraped_at": _now_iso(),
        "current_price": None,
        "original_price": None,
        "currency": "INR",
        "in_stock": None,
        "extraction_strategy": product["extraction_strategy"],
        "fetch_error": None,
        **fields,
    }


def _get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        print(f"[FETCH-FAIL] {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Strategy 1: Shopify's free product JSON endpoint
# ---------------------------------------------------------------------------
def scrape_shopify_json(product: dict) -> dict:
    """Every Shopify storefront serves full product data at <product_url>.json
    -- no HTML parsing, no headless browser, and it doesn't change with
    theme redesigns. This is the most reliable strategy in this file."""
    record = _base_record(product)
    resp = _get(product["json_url"])
    if resp is None:
        record["fetch_error"] = "request_failed"
        return record

    try:
        data = resp.json()
        variant = data["product"]["variants"][0]
        record["current_price"] = float(variant["price"])
        # Shopify only exposes compare_at_price (the crossed-out "original"
        # price) if the merchant set one -- absence means no advertised
        # discount is being claimed at all, which is a useful signal itself.
        if variant.get("compare_at_price"):
            record["original_price"] = float(variant["compare_at_price"])
        record["in_stock"] = bool(variant.get("available"))
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        record["fetch_error"] = f"parse_error: {exc}"

    return record


# ---------------------------------------------------------------------------
# Strategy 2: Open Graph / meta-tag price data
# ---------------------------------------------------------------------------
def scrape_meta_tag(product: dict) -> dict:
    """Many D2C and marketplace sites embed
    <meta property="product:price:amount" content="999">
    in <head> purely for Facebook/Google SEO. It's stable because it's not
    tied to the visual layout at all -- redesign the page, this survives."""
    record = _base_record(product)
    resp = _get(product["url"])
    if resp is None:
        record["fetch_error"] = "request_failed"
        return record

    soup = BeautifulSoup(resp.text, "lxml")

    amount_tag = soup.find("meta", property="product:price:amount")
    currency_tag = soup.find("meta", property="product:price:currency")

    if amount_tag and amount_tag.get("content"):
        try:
            record["current_price"] = float(amount_tag["content"])
        except ValueError:
            pass
    if currency_tag and currency_tag.get("content"):
        record["currency"] = currency_tag["content"]

    # Fallback / enrichment: pull the "Regular price X. Discounted price Y."
    # sentence that's rendered as visible text, which also gives us the
    # crossed-out original price the meta tag doesn't carry.
    text = soup.get_text(" ", strip=True)
    match = re.search(
        r"Regular price[^\d₹]*₹?\s*([\d,]+\.?\d*).{0,40}?Discounted price[^\d₹]*₹?\s*([\d,]+\.?\d*)",
        text,
    )
    if match:
        try:
            record["original_price"] = float(match.group(1).replace(",", ""))
            if record["current_price"] is None:
                record["current_price"] = float(match.group(2).replace(",", ""))
        except ValueError:
            pass

    if record["current_price"] is None:
        record["fetch_error"] = "price_not_found_in_meta_or_text"

    return record


# ---------------------------------------------------------------------------
# Strategy 3: schema.org JSON-LD (kept generic for reuse on other sites)
# ---------------------------------------------------------------------------
def scrape_jsonld(product: dict) -> dict:
    record = _base_record(product)
    resp = _get(product["url"])
    if resp is None:
        record["fetch_error"] = "request_failed"
        return record

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if item.get("@type") == "Product" and "offers" in item:
                offers = item["offers"]
                offers = offers[0] if isinstance(offers, list) else offers
                price = offers.get("price")
                if price:
                    record["current_price"] = float(price)
                    record["currency"] = offers.get("priceCurrency", record["currency"])
                    return record

    record["fetch_error"] = "no_jsonld_product_offer_found"
    return record


# ---------------------------------------------------------------------------
# Strategy 4: hidden internal API (JS SPA sites like Uniqlo)
# ---------------------------------------------------------------------------
def scrape_hidden_api(product: dict) -> dict:
    """For JS-rendered SPAs, the price never appears in the HTML requests.py
    downloads -- the browser fetches it afterwards from an internal JSON
    endpoint. You have to find that endpoint yourself once (DevTools ->
    Network -> XHR/Fetch while the product page loads), then hit it directly
    here. Until `api_url` is filled in, this returns a clean DQ failure
    instead of silently reporting a fake price."""
    record = _base_record(product)
    if not product.get("api_url"):
        record["fetch_error"] = "api_url_not_configured_see_config_py"
        return record

    resp = _get(product["api_url"])
    if resp is None:
        record["fetch_error"] = "request_failed"
        return record

    try:
        data = resp.json()
        # NOTE: adjust this path once you've inspected the real response --
        # this is a best-guess shape based on how these commerce APIs are
        # typically structured (nested under a price group / SKU).
        record["current_price"] = float(data["prices"]["base"]["value"])
        record["original_price"] = float(data["prices"].get("suggestedRetail", {}).get("value", 0)) or None
    except (ValueError, KeyError, TypeError) as exc:
        record["fetch_error"] = f"parse_error: {exc}"

    return record


# ---------------------------------------------------------------------------
# Strategy 5: last-resort CSS selector fallback
# ---------------------------------------------------------------------------
def scrape_css_fallback(product: dict) -> dict:
    record = _base_record(product)
    resp = _get(product["url"])
    if resp is None:
        record["fetch_error"] = "request_failed"
        return record

    soup = BeautifulSoup(resp.text, "lxml")
    el = soup.select_one(product.get("css_selector", ""))
    if el is None:
        record["fetch_error"] = "css_selector_matched_nothing"
        return record

    match = re.search(r"[\d,]+\.?\d*", el.get_text())
    if not match:
        record["fetch_error"] = "no_number_in_matched_element"
        return record

    try:
        record["current_price"] = float(match.group().replace(",", ""))
    except ValueError:
        record["fetch_error"] = "price_parse_failed"

    return record


STRATEGY_DISPATCH = {
    "shopify_json": scrape_shopify_json,
    "meta_tag": scrape_meta_tag,
    "jsonld": scrape_jsonld,
    "hidden_api": scrape_hidden_api,
    "css_fallback": scrape_css_fallback,
}


def scrape_product(product: dict) -> dict:
    """Single entry point pipeline.py calls. Dispatches to the right
    strategy based on config.py, so pipeline.py never needs to know HOW
    a given site is scraped -- only that it gets a record back."""
    strategy_fn = STRATEGY_DISPATCH.get(product["extraction_strategy"])
    if strategy_fn is None:
        raise ValueError(f"Unknown extraction_strategy: {product['extraction_strategy']}")
    return strategy_fn(product)
