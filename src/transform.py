"""
transform.py
------------
Transform + Storage stage: merge today's clean rows (sitting in the local
DuckDB file dlt just wrote) into the single historical_prices.parquet file
that lives in the repo. DuckDB is the SQL engine end-to-end here -- we never
hand-roll pandas merge logic, which keeps the transformation testable and
auditable as SQL.

Idempotency note: if the daily GitHub Action ever re-runs for the same day
(manual retrigger, retry after failure), this will NOT create duplicate
rows -- we keep only the latest scrape per (product_name, price_date).
"""

import os

import duckdb

PIPELINE_DB_PATH = "fake_discount_engine.duckdb"
HISTORICAL_PARQUET = "data/historical_prices.parquet"


def merge_to_parquet(pipeline_db_path: str = PIPELINE_DB_PATH, historical_parquet: str = HISTORICAL_PARQUET) -> int:
    con = duckdb.connect()
    con.execute(f"ATTACH '{pipeline_db_path}' AS src (READ_ONLY)")

    new_rows = con.execute(
        """
        SELECT
            product_name,
            category,
            site,
            url,
            current_price,
            original_price,
            currency,
            in_stock,
            scraped_at,
            CAST(scraped_at AS DATE) AS price_date
        FROM src.raw_prices.price_snapshots
        """
    ).df()

    if os.path.exists(historical_parquet):
        history_df = con.execute(f"SELECT * FROM read_parquet('{historical_parquet}')").df()
        combined = duckdb_concat(con, history_df, new_rows)
    else:
        combined = new_rows

    # Keep the LATEST scrape per product per calendar day (protects against
    # duplicate rows if the workflow is re-run manually the same day).
    combined = (
        combined.sort_values("scraped_at")
        .drop_duplicates(subset=["product_name", "price_date"], keep="last")
        .reset_index(drop=True)
    )

    os.makedirs(os.path.dirname(historical_parquet), exist_ok=True)
    con.register("combined_view", combined)
    con.execute(f"COPY combined_view TO '{historical_parquet}' (FORMAT PARQUET)")
    con.close()

    print(f"historical_prices.parquet now has {len(combined)} total rows across "
          f"{combined['product_name'].nunique()} products")
    return len(combined)


def duckdb_concat(con, df_a, df_b):
    """Union two frames of the same schema via DuckDB rather than pandas.concat,
    so the whole transform step is backed by one SQL engine end to end."""
    con.register("_a", df_a)
    con.register("_b", df_b)
    result = con.execute("SELECT * FROM _a UNION ALL BY NAME SELECT * FROM _b").df()
    con.unregister("_a")
    con.unregister("_b")
    return result


if __name__ == "__main__":
    merge_to_parquet()
