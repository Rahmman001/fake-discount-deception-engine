"""
pipeline.py
-----------
Extract + Load stage, built on dlt.

dlt's job here is small on purpose: take whatever scrapers.py hands back,
run it through a Data Quality gate, and load only the clean rows into a
local DuckDB destination. dlt handles schema creation/evolution for us --
if you add a new field to a record next month, dlt adds the column,
you don't hand-write ALTER TABLE statements.

THE DQ GATE (this is the important part for the portfolio narrative):
A price of $0, None, or negative doesn't mean "the item is free" -- it
means the scraper broke (site redesign, bot block, network hiccup). If we
let that row into historical_prices.parquet, it silently drops the
rolling median for that product to near-zero and EVERY future day looks
like a "massive genuine discount" versus that corrupted baseline. So we
drop the row and log why, rather than let bad data poison history.
"""

import dlt

from config import PRODUCTS
from scrapers import scrape_product

MIN_PLAUSIBLE_PRICE = 1.0  # anything <= this is treated as a scrape failure, not a real price
MAX_PLAUSIBLE_PRICE = 500_000.0  # sanity ceiling; catches unit errors (paise vs rupees, etc.)


def _passes_dq_checks(record: dict) -> bool:
    price = record.get("current_price")

    if record.get("fetch_error"):
        print(f"[DQ-DROP] {record['product_name']}: fetch/parse error -> {record['fetch_error']}")
        return False

    if price is None:
        print(f"[DQ-DROP] {record['product_name']}: current_price is NULL")
        return False

    if price <= MIN_PLAUSIBLE_PRICE:
        print(f"[DQ-DROP] {record['product_name']}: implausible price {price} (<= {MIN_PLAUSIBLE_PRICE})")
        return False

    if price > MAX_PLAUSIBLE_PRICE:
        print(f"[DQ-DROP] {record['product_name']}: implausible price {price} (> {MAX_PLAUSIBLE_PRICE})")
        return False

    # original_price, if present, should never be LESS than current_price --
    # that would mean the "discount" is actually a price increase, which is
    # a real (interesting!) signal but almost always means we mis-parsed
    # which number is which.
    original = record.get("original_price")
    if original is not None and original < price:
        print(
            f"[DQ-WARN] {record['product_name']}: original_price ({original}) < "
            f"current_price ({price}) -- likely a parsing mix-up, dropping original_price"
        )
        record["original_price"] = None

    return True


@dlt.resource(
    name="price_snapshots",
    write_disposition="append",
    # dlt infers column types by sampling values it actually sees. If every
    # row in a batch happens to have NULL for an optional field (e.g. no
    # product went out of stock today), dlt can't infer a type and will
    # silently skip creating that column -- which then breaks any downstream
    # query that references it. Pinning nullable columns explicitly avoids
    # this "works today, breaks in prod next Tuesday" class of bug.
    columns={
        "original_price": {"data_type": "double", "nullable": True},
        "in_stock": {"data_type": "bool", "nullable": True},
    },
)
def price_snapshots():
    """Generator dlt iterates over. Only yields records that pass DQ checks --
    dlt never sees the rejected ones, so they can't end up in the warehouse."""
    for product in PRODUCTS:
        record = scrape_product(product)
        if _passes_dq_checks(record):
            # fetch_error is guaranteed None for anything that passed the DQ
            # gate -- it's a scrape-debugging field, not historical data, so
            # we don't persist a column that would always be empty.
            record.pop("fetch_error", None)
            yield record
        # else: dropped, already logged inside the check functions above


def run():
    pipeline = dlt.pipeline(
        pipeline_name="fake_discount_engine",
        destination="duckdb",
        dataset_name="raw_prices",
    )
    load_info = pipeline.run(price_snapshots())
    print(load_info)
    return pipeline, load_info


if __name__ == "__main__":
    run()
