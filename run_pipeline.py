"""
run_pipeline.py
----------------
Single entrypoint for both local runs and the GitHub Actions workflow.

Usage:
    python run_pipeline.py
"""

import sys
from pathlib import Path

# Allow `python run_pipeline.py` from the repo root while pipeline.py /
# transform.py live under src/ and import each other with bare names.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pipeline import run as run_extract_load  # noqa: E402
from transform import merge_to_parquet  # noqa: E402


def main():
    print("=== Phase 1/2: Extract + Load (dlt -> DuckDB) ===")
    pipeline, load_info = run_extract_load()

    print("\n=== Phase 3: Transform + Store (DuckDB -> Parquet) ===")
    pipeline_db_path = f"{pipeline.pipeline_name}.duckdb"
    row_count = merge_to_parquet(pipeline_db_path=pipeline_db_path)

    print(f"\nDone. historical_prices.parquet has {row_count} rows.")


if __name__ == "__main__":
    main()
