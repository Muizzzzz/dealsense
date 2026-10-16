"""Price history from Keepa -> price_history table.

Usage, from the Dealsense folder:
    py -m backend.prices fetch      # one Keepa call for all products (~1 token each)
    py -m backend.prices reparse    # rebuild the table from saved raw files (0 tokens)
    py -m backend.prices summary    # what we have per product (0 tokens)

Keepa format notes:
- product["csv"][0] is Amazon's own price, [1] the lowest new price from any seller.
- Each series is flat: [time, price, time, price, ...].
- time is "Keepa minutes" (minutes since 2011-01-01 UTC); price is in cents; -1 = no offer.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import keepa
from backend.db import connect

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "keepa"
SERIES = {0: "amazon", 1: "new"}
KEEPA_EPOCH_MIN = 21_564_000  # 2011-01-01 in minutes since 1970


def keepa_time_to_ms(t):
    return (t + KEEPA_EPOCH_MIN) * 60_000


def parse(product):
    """Yield (asin, ts_ms, price_usd, source) rows; 'no offer' (-1) points become None."""
    csv = product.get("csv") or []
    for idx, name in SERIES.items():
        series = csv[idx] if idx < len(csv) and csv[idx] else []
        for t, cents in zip(series[0::2], series[1::2]):
            if cents is not None:
                yield (product["asin"], keepa_time_to_ms(t),
                       cents / 100 if cents >= 0 else None, name)


def reset_table(conn):
    conn.execute("DROP TABLE IF EXISTS price_history")
    conn.close()
    return connect()          # connect() recreates the table with the current schema


def store(conn, products):
    n = 0
    for p in products:
        conn.execute("DELETE FROM price_history WHERE asin = ?", (p["asin"],))
        rows = list(parse(p))
        conn.executemany("INSERT OR REPLACE INTO price_history (asin, ts, price, source) "
                         "VALUES (?,?,?,?)", rows)
        n += len(rows)
    conn.commit()
    return n


def fetch():
    conn = connect()
    asins = [r["asin"] for r in conn.execute("SELECT asin FROM products WHERE asin != ''")]
    if not asins:
        sys.exit("No products in dealsense.db. Run: py -m backend.load_data")
    data = keepa.call("product", domain=1, asin=",".join(asins), history=1)
    conn = reset_table(conn)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for p in data.get("products", []):
        (RAW_DIR / f"{p['asin']}.json").write_text(json.dumps(p), encoding="utf-8")
    n = store(conn, data.get("products", []))
    print(f"Fetched {len(data.get('products', []))} products, stored {n:,} price points. "
          f"Tokens used: {data.get('tokensConsumed')}, left: {data.get('tokensLeft')}")
    summary(conn)


def reparse():
    conn = reset_table(connect())
    wanted = {r["asin"] for r in conn.execute("SELECT asin FROM products")}
    products = [json.loads(f.read_text(encoding="utf-8")) for f in RAW_DIR.glob("*.json")
                if f.stem in wanted]
    print(f"Re-parsed {len(products)} saved products, stored {store(conn, products):,} points.")
    summary(conn)


def day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def summary(conn=None):
    conn = conn or connect()
    print(f"\n{'model':32} {'series':7} {'points':>6}  {'first':10}  {'last':10}  "
          f"{'now $':>9} {'min $':>8} {'max $':>8}")
    for p in conn.execute("SELECT asin, model, price_tier FROM products ORDER BY price_tier"):
        rows = conn.execute("SELECT source, COUNT(price) n, MIN(ts) a, MAX(ts) b, MIN(price) lo,"
                            " MAX(price) hi FROM price_history WHERE asin = ? GROUP BY source",
                            (p["asin"],)).fetchall()
        if not rows:
            print(f"{p['model'][:32]:32} (no price data)")
        for r in rows:
            last = conn.execute("SELECT price FROM price_history WHERE asin=? AND source=? "
                                "ORDER BY ts DESC LIMIT 1", (p["asin"], r["source"])).fetchone()
            now = f"{last['price']:>8.2f}" if last["price"] is not None else " no offer"
            print(f"{p['model'][:32]:32} {r['source']:7} {r['n']:>6}  {day(r['a'])}  "
                  f"{day(r['b'])} {now} {r['lo'] or 0:>8.2f} {r['hi'] or 0:>8.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"fetch": fetch, "reparse": reparse, "summary": summary}.get(cmd, lambda: print(__doc__))()
