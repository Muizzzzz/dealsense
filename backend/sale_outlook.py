"""Sale outlook: how likely is this product's price to drop during the next big sale event,
and by how much?

Method: a small logistic regression trained on every past sale event in our price data
(product x event: did the price drop 5%+ from its usual level?). Features:
  - which event (Black Friday-Cyber Monday is the strongest for laptops and headphones)
  - volatility: how often the price dipped 5% in an ordinary week over the past year
  - age: how long the product has been sold (old products rarely get sale cuts)
  - position: where today's price sits in its 180-day range (already low = less room)
A product's own record at past events turned out NOT to predict its next one (tested), so it
is shown for context only.

Tested by leaving each product out, training on the others, and predicting its events:
Brier score 0.157 vs 0.165 for always saying the average rate, and the probabilities were
roughly right (said <15%: dropped 8%; 15-25%: 24%; 25-40%: 31%). A modest, honest edge.

    py -m backend.sale_outlook "Swift"       one product's outlook
    py -m backend.sale_outlook backtest      the leave-one-product-out test
    py -m backend.sale_outlook train         refit the weights (saved in data/sale_model.json)
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from backend.db import connect
from backend.price_agent import daily_prices, money

DROP = 0.05          # a 5%+ drop counts as a sale
RECENT = 4           # past occurrences shown for context
HORIZON = 120        # only show events starting within this many days
MODEL_FILE = Path(__file__).resolve().parent.parent / "data" / "sale_model.json"
FEATURES = ["bias", "black_friday", "prime_day", "volatility", "age", "position"]
# Trained on our data in Nov 2026 (211 product-events, 23 products); refit with "train".
DEFAULT_MODEL = {"weights": [-1.04, 0.55, 0.06, 1.3, -1.01, -0.68],
                 "typical_drop": {"black_friday": 0.079, "prime_day": 0.087,
                                  "big_deal_days": 0.118},
                 "trained_on": 211}

# Announced dates (start, end). Black Friday is computed for every year.
KNOWN = {
    "prime_day": [("2023-07-11", "2023-07-12"), ("2024-07-16", "2024-07-17"),
                  ("2025-07-08", "2025-07-11"), ("2026-07-07", "2026-07-10")],
    "big_deal_days": [("2023-10-10", "2023-10-11"), ("2024-10-08", "2024-10-09"),
                      ("2025-10-07", "2025-10-08")],
}
NAMES = {"black_friday": "Black Friday to Cyber Monday", "prime_day": "Prime Day",
         "big_deal_days": "Prime Big Deal Days"}


def black_friday(year):
    nov1 = date(year, 11, 1)
    thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    return thanksgiving + timedelta(days=1)


def nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def occurrences(event, through_year):
    """[(start, end, announced?)] for an event, oldest first, including expected future dates."""
    if event == "black_friday":
        out = [(black_friday(y), black_friday(y) + timedelta(days=3), True)
               for y in range(2015, through_year + 1)]
    else:
        out = [(date.fromisoformat(a), date.fromisoformat(b), True) for a, b in KNOWN[event]]
        known_years = {s.year for s, _, _ in out}
        month, n = (7, 2) if event == "prime_day" else (10, 2)
        for y in range(min(known_years), through_year + 1):
            if y not in known_years and y > max(known_years):
                s = nth_weekday(y, month, 1, n)            # usually the 2nd Tuesday
                out.append((s, s + timedelta(days=1), False))
    return sorted(out)


def next_event(today=None):
    """The soonest event that hasn't ended: (key, start, end, announced?) or None."""
    today = today or date.today()
    best = None
    for key in NAMES:
        for s, e, known in occurrences(key, today.year + 1):
            if e >= today and (s - today).days <= HORIZON:
                if best is None or s < best[1]:
                    best = (key, s, e, known)
                break
    return best


def event_drop(days, start, end):
    """Fractional drop from the usual price before the event to the lowest during it,
    or None if we lack data for that occurrence."""
    before = [p for d, p in days.items() if start - timedelta(days=30) <= d <= start -
              timedelta(days=8) and p is not None]
    during = [p for d, p in days.items() if start - timedelta(days=1) <= d <= end +
              timedelta(days=1) and p is not None]
    if len(before) < 10 or not during:
        return None
    usual = median(before)
    return (usual - min(during)) / usual


def history(days, event, before_date):
    """This product's drops at past occurrences of an event that ended before a date."""
    out = []
    for s, e, known in occurrences(event, before_date.year):
        if known and e < before_date:
            d = event_drop(days, s, e)
            if d is not None:
                out.append((s.year, d))
    return out[-RECENT:]


def features(days, event, start, as_of):
    """Model inputs for one product and one event, using only prices up to as_of."""
    known = [d for d, p in sorted(days.items()) if p]
    if not known:
        return None
    long = [p for d, p in days.items() if as_of - timedelta(days=180) <= d <= as_of and p]
    if len(long) < 60:
        return None
    short = [p for d, p in days.items() if as_of - timedelta(days=30) <= d <= as_of and p]
    year = [p for d, p in sorted(days.items()) if as_of - timedelta(days=365) <= d <= as_of and p]
    windows = max(1, len(year) - 36)
    vol = sum(1 for i in range(30, len(year) - 6)
              if min(year[i:i + 6]) <= median(year[i - 30:i]) * (1 - DROP)) / windows
    pos = (median(short) - min(long)) / (max(long) - min(long) + 1e-9)
    age = (start - known[0]).days / 365
    return [1.0, float(event == "black_friday"), float(event == "prime_day"),
            vol, min(age, 6) / 6, pos]


def predict(weights, x):
    return 1 / (1 + math.exp(-sum(w * v for w, v in zip(weights, x))))


def load_model():
    try:
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_MODEL


def outlook(conn, product, today=None):
    """Outlook for the next sale event, or None if there's nothing to say."""
    today = today or date.today()
    ev = next_event(today)
    if not ev:
        return None
    key, start, end, announced = ev
    days = daily_prices(conn, product["asin"])
    recent = [p for d, p in days.items() if today - timedelta(days=30) <= d <= today and p]
    current = days.get(today) or (recent[-1] if recent else None)
    x = features(days, key, start, min(today, start - timedelta(days=8)))
    if not current or not x:
        return None                     # not sold new, or too little history to say anything
    model = load_model()
    p = predict(model["weights"], x)
    typical = model["typical_drop"][key]
    usual = median(recent)
    sale_price = usual * (1 - typical)
    past = history(days, key, today)
    vol, age = x[3], x[4] * 6
    reasons = []
    reasons.append("its price often dips in ordinary weeks" if vol >= 0.2 else
                   "its price rarely moves" if vol < 0.05 else "its price moves now and then")
    if age >= 4:
        reasons.append("it has been on sale for years, and older models rarely get sale cuts")
    if x[5] < 0.25:
        reasons.append("today's price is already near the bottom of its 6-month range")
    elif x[5] > 0.75:
        reasons.append("today's price is near the top of its 6-month range")
    return {
        "event": key, "name": NAMES[key], "start": str(start), "end": str(end),
        "announced": announced, "days_away": max(0, (start - today).days),
        "happening": start <= today <= end,
        "probability": round(p, 2), "typical_drop": typical,
        "usual_price": round(usual, 2), "expected_sale_price": round(sale_price, 2),
        "current_price": round(current, 2),
        "already_low": current <= sale_price * 1.02,
        "past": [{"year": y, "drop": round(d, 3)} for y, d in past],
        "reasons": reasons, "trained_on": model.get("trained_on"),
    }


def facts(o):
    """Plain-language lines for the write-up prompt."""
    if not o:
        return []
    when = ("happening now" if o["happening"] else
            f"starts {o['start']} ({o['days_away']} days away)")
    lines = [f"Next sale event: {o['name']}, {when}"
             + ("" if o["announced"] else "; date not announced yet, estimated") + ".",
             f"Estimated chance of a 5%+ price drop during it: about "
             f"{round(o['probability'] * 20) * 5}% (a rough estimate from past sale events; "
             f"factors: {'; '.join(o['reasons'])}).",
             f"When products like this do drop at {o['name']}, it is typically about "
             f"{o['typical_drop']:.0%}, which would be about {money(o['expected_sale_price'])}."]
    if o["already_low"]:
        lines.append("Today's price is already about as low as a typical sale price.")
    return lines


# ------------------------------------------------------------------ training and testing

def dataset(conn):
    """(asin, features, dropped?, drop) for every past product x event we have data for."""
    rows = []
    for (asin,) in conn.execute("SELECT DISTINCT asin FROM price_history"):
        days = daily_prices(conn, asin)
        for key in NAMES:
            for s, e, known in occurrences(key, date.today().year):
                if not known or e >= date.today():
                    continue
                actual = event_drop(days, s, e)
                x = features(days, key, s, s - timedelta(days=8)) if actual is not None else None
                if x:
                    rows.append((asin, x, actual >= DROP, actual, key))
    return rows


def fit(rows, l2=1.0, steps=3000, lr=0.3):
    """Logistic regression by gradient descent (a handful of features, no libraries)."""
    w = [0.0] * len(FEATURES)
    for _ in range(steps):
        grad = [0.0] * len(w)
        for _, x, y, *_ in rows:
            err = predict(w, x) - y
            for j, v in enumerate(x):
                grad[j] += err * v
        for j in range(len(w)):
            w[j] -= lr * (grad[j] / len(rows) + (l2 * w[j] / len(rows) if j else 0))
    return w


def train():
    rows = dataset(connect())
    typical = {}
    for key in NAMES:
        hits = [d for *_, y, d, k in rows if k == key and y]
        typical[key] = round(median(hits), 3) if hits else 0.08
    model = {"weights": [round(v, 3) for v in fit(rows)], "typical_drop": typical,
             "trained_on": len(rows), "trained": str(date.today())}
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_FILE.write_text(json.dumps(model, indent=2), encoding="utf-8")
    print(f"Trained on {len(rows)} product-events.")
    print(dict(zip(FEATURES, model["weights"])), typical)


def backtest():
    """Leave each product out, train on the rest, predict its events, compare with reality.
    Brier score = mean squared error of the probabilities (lower is better)."""
    rows = dataset(connect())
    asins = sorted({r[0] for r in rows})
    out = []
    for a in asins:
        train_rows = [r for r in rows if r[0] != a]
        w, base = fit(train_rows), sum(r[2] for r in train_rows) / len(train_rows)
        out += [(predict(w, r[1]), base, r[2]) for r in rows if r[0] == a]
    brier = lambda i: sum((o[i] - o[2]) ** 2 for o in out) / len(out)
    print(f"{len(rows)} product-events from {len(asins)} products; "
          f"{sum(r[2] for r in rows) / len(rows):.0%} dropped 5%+.")
    print(f"Brier score: model {brier(0):.3f} vs always saying the average rate {brier(1):.3f}")
    print("Calibration (when we said X, how often did it drop?):")
    for lo, hi in ((0, .15), (.15, .25), (.25, .4), (.4, 1.01)):
        b = [y for p, _, y in out if lo <= p < hi]
        if b:
            print(f"  said {lo:.0%}-{min(hi, 1):.0%}: dropped {sum(b) / len(b):.0%} of {len(b)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "backtest":
        backtest()
    elif args[0] == "train":
        train()
    else:
        from backend.search import find_product
        conn = connect()
        prod = find_product(conn, args[0])
        if not prod:
            sys.exit(f"No product matches {args[0]!r}")
        o = outlook(conn, prod)
        print(f"{prod['brand']} {prod['model']}")
        print("\n".join(facts(o)) if o else "No upcoming sale event, or not sold new right now.")
        if o:
            print("Past:", ", ".join(f"{p['year']}: {-p['drop']:+.0%}" for p in o["past"]) or "none")
