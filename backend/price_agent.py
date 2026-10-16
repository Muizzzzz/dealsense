"""Price agent: is today's price a genuine low, typical, inflated, or a fake discount?

Uses Keepa's "new" series (lowest new price from any seller, Amazon included).

Usage, from the Dealsense folder:
    py -m backend.price_agent                      # verdict for every product
    py -m backend.price_agent "Zephyrus"           # details for one product
    py -m backend.price_agent "Swift" --as-of 2025-11-28   # what it would have said that day
"""
import sys
from datetime import date, datetime, timedelta, timezone

from backend.db import connect
from backend.search import find_product

WINDOW = 180          # days of history that define "normal"
MIN_DAYS = 30         # need at least this many priced days in the window, else widen it
RECENT = 30           # window for "recent high" in fake-discount detection
LONG = 730            # long-term baseline: catches old models resold at frozen high prices
LONG_GAP = 0.25
LOW_PCTL, HIGH_PCTL = 20, 80
LOW_GAP, HIGH_GAP, FAKE_DROP = 0.05, 0.10, 0.10


def daily_prices(conn, asin, source="new"):
    """Keepa logs changes only, so carry each price forward day by day.
    Returns {date: price or None (no offer)}."""
    rows = conn.execute("SELECT ts, price FROM price_history WHERE asin = ? AND source = ? "
                        "ORDER BY ts", (asin, source)).fetchall()
    days = {}
    for i, r in enumerate(rows):
        start = datetime.fromtimestamp(r["ts"] / 1000, timezone.utc).date()
        end = (datetime.fromtimestamp(rows[i + 1]["ts"] / 1000, timezone.utc).date()
               if i + 1 < len(rows) else date.today() + timedelta(days=1))
        d = start
        while d < end:
            days[d] = r["price"]       # a later change on the same day overwrites
            d += timedelta(days=1)
    return days


def pctl(values, p):
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def money(x):
    return f"${x:,.2f}"


def analyze(conn, product, as_of=None, experimental=False):
    """experimental=True also returns 'fake_discount'. It's off for the product because the
    backtest showed those days were good days to buy (+12% vs waiting 30 days), the opposite
    of what the label claims. The backtest turns it on to keep measuring it."""
    as_of = as_of or date.today()
    days = daily_prices(conn, product["asin"])
    result = {"asin": product["asin"], "parent_asin": product["parent_asin"],
              "model": f"{product['brand']} {product['model']}", "as_of": str(as_of)}
    past = {d: p for d, p in days.items() if d <= as_of}
    if not past:
        return {**result, "verdict": "no_data", "reasons": ["No price history for this product."]}

    current = past[max(past)]
    if current is None:
        last = [(d, p) for d, p in past.items() if p is not None]
        reason = ("Nobody is selling it new right now"
                  + (f"; last new price was {money(last[-1][1])} on {last[-1][0]}." if last
                     else "."))
        return {**result, "verdict": "unavailable", "current_price": None, "reasons": [reason]}

    # baseline window: last 180 days, widened if the product was rarely offered
    window = WINDOW
    while True:
        start = as_of - timedelta(days=window)
        hist = [p for d, p in past.items() if start <= d < as_of and p is not None]
        if len(hist) >= MIN_DAYS or window >= 3650:
            break
        window *= 2
    if len(hist) < 7:
        return {**result, "verdict": "insufficient_history", "current_price": current,
                "reasons": [f"Only {len(hist)} days of price history; not enough to judge."]}

    median = pctl(hist, 50)
    stats = {f"p{q}": round(pctl(hist, q), 2) for q in (10, 25, 75, 90)}
    rank = round(100 * sum(p < current for p in hist) / len(hist))   # % of days cheaper
    recent = [p for d, p in past.items()
              if as_of - timedelta(days=RECENT) <= d < as_of and p is not None]
    recent_high = max(recent) if recent else None
    recent_median = pctl(recent, 50) if recent else None
    vs_median = (current - median) / median
    low = min(hist)
    all_time_low = min(p for p in past.values() if p is not None)
    long_hist = [p for d, p in past.items()
                 if as_of - timedelta(days=LONG) <= d < as_of and p is not None]
    long_median = pctl(long_hist, 50) if len(long_hist) >= MIN_DAYS else None
    vs_long = (current - long_median) / long_median if long_median else 0

    # a genuine low is a dip below where the price has been lately, not just a slow
    # downward drift (laptops lose value, so an aging laptop is "below median" for months)
    dip = recent_median is not None and current <= recent_median * (1 - LOW_GAP)
    if rank <= LOW_PCTL and vs_median <= -LOW_GAP and dip:
        verdict = "genuine_low"
    elif rank >= HIGH_PCTL and vs_median >= HIGH_GAP:
        verdict = "inflated"
    elif vs_long >= LONG_GAP:
        verdict = "inflated"           # steady lately, but far above what it used to cost
    elif recent_high and current <= recent_high * (1 - FAKE_DROP) and vs_median >= 0:
        verdict = "fake_discount"
    else:
        verdict = "typical"

    pct = f"{abs(vs_median) * 100:.0f}%"
    reasons = [
        f"Current price {money(current)} is {pct} {'below' if vs_median < 0 else 'above'} "
        f"its {window}-day median of {money(median)}.",
        f"It was cheaper than today on {rank}% of the last {window} days "
        f"(range {money(low)} to {money(max(hist))}).",
        f"All-time lowest new price: {money(all_time_low)}.",
    ]
    if recent_median and verdict == "genuine_low":
        reasons.insert(0, f"It dropped {money(recent_median - current)} below its {RECENT}-day "
                          f"median of {money(recent_median)}.")
    if verdict == "inflated" and recent_high and current <= recent_high * (1 - FAKE_DROP):
        reasons.insert(0, f"It's down {money(recent_high - current)} from its {RECENT}-day high "
                          f"of {money(recent_high)}, but that high was itself above normal.")
    if long_median:
        reasons.append(f"Its 2-year median is {money(long_median)}; today is "
                       f"{abs(vs_long) * 100:.0f}% {'below' if vs_long < 0 else 'above'} that.")
    if verdict == "fake_discount":
        if experimental:
            reasons.insert(0, f"It's {money(recent_high - current)} below its {RECENT}-day high "
                              f"of {money(recent_high)}, but still not below its normal price.")
        else:
            verdict = "typical"
            reasons.insert(0, f"It's down {money(recent_high - current)} from its {RECENT}-day "
                              f"high of {money(recent_high)}.")
    return {**result, "verdict": verdict, "current_price": current,
            "window_days": window, "median": round(median, 2), **stats,
            "window_low": low, "window_high": max(hist), "all_time_low": all_time_low,
            "pct_vs_median": round(vs_median * 100, 1), "cheaper_days_pct": rank,
            "recent_high": recent_high, "long_median": long_median,
            "pct_vs_long_median": round(vs_long * 100, 1), "reasons": reasons}


LABEL = {"genuine_low": "GENUINE LOW", "typical": "typical", "inflated": "INFLATED",
         "fake_discount": "FAKE DISCOUNT", "unavailable": "unavailable",
         "insufficient_history": "not enough data", "no_data": "no data"}

if __name__ == "__main__":
    args = sys.argv[1:]
    as_of = None
    if "--as-of" in args:
        i = args.index("--as-of")
        as_of = date.fromisoformat(args[i + 1])
        del args[i:i + 2]
    conn = connect()
    if args:
        product = find_product(conn, args[0])
        if not product:
            sys.exit(f"No product matches {args[0]!r}")
        r = analyze(conn, product, as_of)
        print(f"{r['model']} ({r['asin']}), as of {r['as_of']}: {LABEL[r['verdict']]}")
        for line in r["reasons"]:
            print(f"  - {line}")
    else:
        for product in conn.execute("SELECT * FROM products ORDER BY price_tier, model"):
            r = analyze(conn, product, as_of)
            price = money(r["current_price"]) if r.get("current_price") else "-"
            med = money(r["median"]) if r.get("median") else "-"
            print(f"{product['price_tier']:9} {r['model'][:40]:40} {price:>10}  "
                  f"median {med:>10}  {LABEL[r['verdict']]}")
