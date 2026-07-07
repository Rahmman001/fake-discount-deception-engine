# Fake Discount Deception Engine

An automated daily pipeline that tracks real historical prices of specific
products to calculate each item's **True Median Price**, and flags when a
retailer's advertised "discount" is fake -- i.e. the "original price" it's
being cut from is inflated relative to the item's actual price history.

100% free stack, hosted entirely on GitHub:

| Stage | Tool |
|---|---|
| Extract | `requests` + `BeautifulSoup` |
| Load | `dlt` (Data Load Tool) -> local DuckDB |
| Transform & Storage | DuckDB -> `data/historical_prices.parquet` (committed to the repo) |
| Orchestration | GitHub Actions (daily cron + auto-commit) |
| Visualization | Streamlit (Community Cloud) |

## How it works

1. `run_pipeline.py` runs daily via GitHub Actions.
2. `src/scrapers.py` fetches today's price for each product in
   `src/config.py`, using whichever extraction strategy fits that site
   (Shopify JSON endpoint / meta tags / hidden API -- see comments in
   `config.py` for why these differ).
3. `src/pipeline.py` (dlt) loads only records that pass a Data Quality
   gate -- a scrape that returns `$0`, `NULL`, or an implausible price is
   dropped rather than allowed to corrupt the historical median.
4. `src/transform.py` (DuckDB) merges today's clean rows into
   `data/historical_prices.parquet`, deduplicating so re-runs are safe.
5. GitHub Actions commits the updated Parquet file back to `main`.
6. `app.py` (Streamlit) reads that Parquet file directly with DuckDB and
   plots Current Price vs. the True 30-Day Median Price.

## Running locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_pipeline.py           # populates data/historical_prices.parquet
streamlit run app.py
```

## Before you deploy

The Uniqlo product in `src/config.py` has `extraction_strategy:
"hidden_api"` with `api_url: None` -- Uniqlo's India site is a JS SPA, so
its price never appears in the static HTML. You'll need to find the real
endpoint yourself once via your browser's DevTools (Network tab -> XHR/Fetch
while the product page loads) and paste it into `config.py`. Until then,
that product will be cleanly dropped by the DQ gate every day rather than
report a fake price.
