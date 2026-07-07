"""
config.py
---------
Single source of truth for WHICH products we track.

Staff-eng note: keeping this as plain data (not buried inside pipeline.py)
means a non-engineer (or future-you at 11pm) can add a new product by
editing a list, not by reading scraping code. It also means each product
can declare its OWN extraction strategy, because -- as you'll see -- not
every e-commerce site exposes price data the same way.

extraction_strategy values:
  "shopify_json"  -> Shopify stores expose a free, stable {handle}.json
                      endpoint on every product page. No HTML parsing at all.
  "meta_tag"      -> Site embeds Open Graph product:price:amount / currency
                      meta tags in the raw HTML <head>. Very stable, SEO-driven.
  "jsonld"        -> Site embeds schema.org Product/Offer JSON-LD.
  "css_fallback"  -> No structured data found; fall back to a CSS selector
                      (fragile -- most likely to break on redesign).
  "hidden_api"    -> Site is a JS single-page app; price is fetched by the
                      browser from an internal JSON API after page load.
                      Requires you to find the endpoint via DevTools -> Network.
"""

PRODUCTS = [
    {
        "product_name": "Yogabar 26g High Protein Oats - Fruit & Nut (1kg)",
        "category": "grocery",
        "site": "yogabars.in",
        "url": "https://www.yogabars.in/products/26g-high-protein-oats-fruit-nut",
        "extraction_strategy": "shopify_json",
        # Shopify stores serve full product data at <product_url>.json for free.
        "json_url": "https://www.yogabars.in/products/26g-high-protein-oats-fruit-nut.json",
    },
    {
        "product_name": "boAt Airdopes 131 Wireless Earbuds",
        "category": "electronics",
        "site": "boat-lifestyle.com",
        "url": "https://www.boat-lifestyle.com/products/airdopes-131",
        "extraction_strategy": "shopify_json",
        "json_url": "https://www.boat-lifestyle.com/products/airdopes-131.json",
    },
]
