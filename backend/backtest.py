"""Backtest the price agent: replay it on past dates and check whether its "genuine low"
calls land on Amazon's big sale events more often than chance.

Usage, from the Dealsense folder:
    py -m backend.backtest              # last 3 years, every day
    py -m backend.backtest --years 2

Sale windows are the US dates of Prime Day, Prime Big Deal Days (October), and
Black Friday through Cyber Monday. If the agent works, the share of its genuine-low days
that fall in a sale window should be well above the share of all days that do.
"""
import sys
from collections import Counter
from datetime import date, timedelta

from backend.db import connect
from backend.price_agent import analyze, daily_prices

SALES = [  # (start, end) inclusive
    ("2023-07-11", "2023-07-12"), ("2023-10-10", "2023-10-11"), ("2023-11-24", "2023-11-27"),
    ("2024-07-16", "2024-07-17"), ("2024-10-08", "2024-10-09"), ("2024-11-29", "2024-12-02"),
    ("2025-07-08", "2025-07-11"), ("2025-10-07", "2025-10-08"), ("2025-11-28", "2025-12-01"),
    ("2026-07-07", "2026-07-10"),
]
# count a call as "near" a sale if it falls within 3 days either side (prices often drop early)
SLACK = 3
HORIZON = 30     # days: did a lower price show up within this long after the call?
WINDOWS = [(date.fromisoformat(a) - timedelta(days=SLACK),
            date.fromisoformat(b) + timedelta(days=SLACK)) for a, b in SALES]


def in_sale(d):
    return any(a <= d <= b for a, b in WINDOWS)


def main():
    years = int(sys.argv[sys.argv.index("--years") + 1]) if "--years" in sys.argv else 3
    end = date.today()
    start = end - timedelta(days=365 * years)
    conn = connect()
    total, verdicts, low_in_sale, days_in_sale = 0, Counter(), 0, 0
    per_product = {}
    no_regret, scored = Counter(), Counter()   # "would a lower price have come within 30 days?"
    savings = Counter()                        # sum of (typical next-30-day price - today) / today
    for product in conn.execute("SELECT * FROM products"):
        counts = Counter()
        days = daily_prices(conn, product["asin"])
        d = start
        while d <= end:
            r = analyze(conn, product, d, experimental=True)
            v = r["verdict"]
            if v not in ("unavailable", "no_data", "insufficient_history"):
                future = [days.get(d + timedelta(days=k)) for k in range(1, HORIZON + 1)]
                future = [p for p in future if p is not None]
                if d + timedelta(days=HORIZON) <= end and future:
                    scored[v] += 1
                    no_regret[v] += r["current_price"] <= min(future)
                    fut = sorted(future)
                    savings[v] += (fut[len(fut) // 2] - r["current_price"]) / r["current_price"]
                counts[v] += 1
                total += 1
                verdicts[v] += 1
                days_in_sale += in_sale(d)
                if v == "genuine_low" and in_sale(d):
                    low_in_sale += 1
            d += timedelta(days=1)
        per_product[f"{product['brand']} {product['model']}"] = counts

    print(f"Replayed {total:,} product-days from {start} to {end}.\n")
    print(f"{'product':45} {'low':>5} {'typical':>8} {'fake':>5} {'inflated':>9}")
    for name, c in sorted(per_product.items()):
        print(f"{name[:45]:45} {c['genuine_low']:>5} {c['typical']:>8} "
              f"{c['fake_discount']:>5} {c['inflated']:>9}")
    print(f"\nIf you bought on the day of each call, how often was there no lower price "
          f"in the next {HORIZON} days?\n(saved = how much cheaper today was than the "
          f"median price over the next {HORIZON} days; negative means waiting was better)")
    for v in ("genuine_low", "typical", "fake_discount", "inflated"):
        if scored[v]:
            print(f"  {v:14} {no_regret[v] / scored[v]:6.1%}  ({scored[v]:,} days); "
                  f"vs waiting, you saved {savings[v] / scored[v]:+.1%} on average")
    base = days_in_sale / total if total else 0
    lows = verdicts["genuine_low"]
    hit = low_in_sale / lows if lows else 0
    print(f"\nShare of all product-days that fall near a sale event: {base:.1%}")
    print(f"Share of 'genuine low' calls that fall near a sale event: {hit:.1%} "
          f"({low_in_sale} of {lows})")
    if base:
        print(f"Lift: genuine-low calls are {hit / base:.1f}x as likely to land on a sale "
              f"as a random day.")


if __name__ == "__main__":
    main()
