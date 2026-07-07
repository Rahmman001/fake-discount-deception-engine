"""
app.py
------
Streamlit dashboard for the Fake Discount Deception Engine.

WHAT THIS APP DOES, IN PLAIN ENGLISH (for the non-technical reader):
Every day this project records the REAL price of a few products. Over time
that builds up a history. This dashboard compares TODAY's advertised price
against two different baselines:

  1. The "Claimed Discount" -- what the retailer says you're saving,
     based on their own crossed-out "original price" (MRP).
  2. The "True Discount" -- what you're ACTUALLY saving, based on the
     item's real 30-day median price that this pipeline has been tracking.

When a retailer inflates the "original price" right before a sale, the
Claimed Discount looks big while the True Discount stays small. That gap
IS the deception this project is built to expose.

HOW: DuckDB queries the historical_prices.parquet file that GitHub Actions
commits every day, directly with SQL -- no separate database server needed.
That parquet file is the entire "data warehouse" for this project.
"""

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HISTORICAL_PARQUET = "data/historical_prices.parquet"

# Tune these two numbers to change how strict the "is this a real deal?"
# verdict is. 15% below the item's own history = genuine deal; within a
# few percent of normal = the "discount" isn't really a discount at all.
GENUINE_DEAL_THRESHOLD_PCT = 15.0
PRICE_HIKE_THRESHOLD_PCT = -5.0

st.set_page_config(page_title="Fake Discount Deception Engine", layout="wide")


@st.cache_data(ttl=3600)
def load_priced_history() -> pd.DataFrame:
    """Runs entirely in DuckDB against the Parquet file -- this is the
    'lakehouse' pattern: no database server, the file on disk IS the table."""
    con = duckdb.connect()
    query = f"""
        WITH scored AS (
            SELECT
                product_name,
                category,
                price_date,
                current_price,
                original_price,
                MEDIAN(current_price) OVER (
                    PARTITION BY product_name
                    ORDER BY price_date
                    RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND CURRENT ROW
                ) AS rolling_30d_median,
                COUNT(*) OVER (
                    PARTITION BY product_name
                    ORDER BY price_date
                    RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND CURRENT ROW
                ) AS days_of_history
            FROM read_parquet('{HISTORICAL_PARQUET}')
        )
        SELECT
            *,
            CASE WHEN original_price IS NOT NULL AND original_price > 0
                 THEN ROUND((original_price - current_price) / original_price * 100, 1)
                 ELSE NULL END AS claimed_discount_pct,
            ROUND((rolling_30d_median - current_price) / rolling_30d_median * 100, 1) AS true_discount_pct
        FROM scored
        ORDER BY product_name, price_date
    """
    return con.execute(query).df()


def verdict_for(true_discount_pct: float, days_of_history: int) -> tuple[str, str]:
    """Returns (label, color). Kept as a plain function (not inline logic)
    so the business rule is easy to point to and explain in an interview."""
    if days_of_history < 5:
        return "⚪ Not enough history yet", "gray"
    if true_discount_pct >= GENUINE_DEAL_THRESHOLD_PCT:
        return "🟢 Genuine Deal", "green"
    if true_discount_pct <= PRICE_HIKE_THRESHOLD_PCT:
        return "🔴 Priced Above Its Own History", "red"
    return "🟡 Fake / Inflated Discount", "orange"


# --- Load data -------------------------------------------------------------
try:
    df = load_priced_history()
except (duckdb.IOException, FileNotFoundError):
    st.error(
        f"Couldn't find `{HISTORICAL_PARQUET}`. Run `python run_pipeline.py` "
        "at least once locally (or wait for the first GitHub Actions run) "
        "to generate it."
    )
    st.stop()

if df.empty:
    st.warning("historical_prices.parquet exists but has no rows yet.")
    st.stop()

# --- Header ------------------------------------------------------------------
st.title("🕵️ Fake Discount Deception Engine")
st.caption(
    "Tracking real historical prices to expose inflated 'original price' "
    "discounts on e-commerce sites."
)

# --- Sidebar: product picker -------------------------------------------------
products = sorted(df["product_name"].unique())
selected_product = st.sidebar.selectbox("Choose a product", products)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "**How to read the verdict**\n\n"
    f"- 🟢 **Genuine Deal**: priced ≥{GENUINE_DEAL_THRESHOLD_PCT:.0f}% below its own "
    "30-day median\n"
    "- 🟡 **Fake / Inflated Discount**: site claims a discount, but the price "
    "is basically normal for this item\n"
    f"- 🔴 **Priced Above Its Own History**: current price is ≥{abs(PRICE_HIKE_THRESHOLD_PCT):.0f}% "
    "*higher* than usual\n"
    "- ⚪ **Not enough history**: fewer than 5 days tracked so far"
)

product_df = df[df["product_name"] == selected_product].sort_values("price_date")
latest = product_df.iloc[-1]

# --- Top metrics row ----------------------------------------------------------
verdict_label, verdict_color = verdict_for(latest["true_discount_pct"], latest["days_of_history"])

col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"₹{latest['current_price']:,.0f}")
col2.metric("True 30-Day Median Price", f"₹{latest['rolling_30d_median']:,.0f}")
claimed = latest["claimed_discount_pct"]
col3.metric(
    "Site Claims Discount Of",
    f"{claimed:.0f}%" if pd.notna(claimed) else "No discount advertised",
)
col4.metric("Verdict", verdict_label)

if pd.notna(claimed) and latest["true_discount_pct"] < claimed - 5:
    st.info(
        f"📢 The site advertises a **{claimed:.0f}% discount**, but based on "
        f"{int(latest['days_of_history'])} days of tracked history, the real "
        f"discount versus this item's normal price is only "
        f"**{latest['true_discount_pct']:.0f}%**. The gap suggests the "
        "'original price' may be inflated."
    )

# --- Main chart: current price vs rolling median over time --------------------
fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=product_df["price_date"],
        y=product_df["current_price"],
        mode="lines+markers",
        name="Current (advertised) price",
        line=dict(color="#EF553B", width=2),
    )
)
fig.add_trace(
    go.Scatter(
        x=product_df["price_date"],
        y=product_df["rolling_30d_median"],
        mode="lines",
        name="True 30-day median price",
        line=dict(color="#636EFA", width=2, dash="dash"),
    )
)
fig.update_layout(
    title=f"{selected_product}: Current Price vs. True Median Price",
    xaxis_title="Date",
    yaxis_title="Price (₹)",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    height=450,
)
st.plotly_chart(fig, use_container_width=True)

# --- Transparency: raw data table ---------------------------------------------
with st.expander("See the underlying daily data"):
    display_cols = [
        "price_date", "current_price", "original_price",
        "rolling_30d_median", "claimed_discount_pct", "true_discount_pct",
    ]
    st.dataframe(
        product_df[display_cols].rename(columns={
            "price_date": "Date",
            "current_price": "Current Price (₹)",
            "original_price": "Site's 'Original' Price (₹)",
            "rolling_30d_median": "True 30-Day Median (₹)",
            "claimed_discount_pct": "Claimed Discount (%)",
            "true_discount_pct": "True Discount (%)",
        }),
        use_container_width=True,
        hide_index=True,
    )

st.caption(
    "Data updates once daily via a scheduled GitHub Actions workflow. "
    "Source: dlt + DuckDB pipeline reading each retailer's own product pages."
)
