"""Paste-a-link mode: check any Amazon laptop by link or ASIN.

Steps: link -> ASIN -> Keepa product (live price history + listing details) -> laptop check ->
match to our 2023 reviews (by ASIN, parent listing, or any variant) -> stored as a product
so the price agent and write-up work on it like any test-set laptop.

Usage, from the Dealsense folder:
    py -m backend.lookup "https://www.amazon.com/dp/B086KKKT15"
    py -m backend.lookup B086KKKT15
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from backend import keepa
from backend.db import connect
from backend.prices import store

LIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "keepa_live"
FRESH_HOURS = 6
ASIN_IN_URL = re.compile(r"/(?:dp|gp/product|gp/aw/d|product|d)/([A-Z0-9]{10})(?=[/?&#]|$)", re.I)
BARE_ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)
LAPTOP_WORDS = re.compile(r"\b(laptop|notebook|macbook|chromebook|ultrabook)\b", re.I)


class LookupError_(Exception):
    """A problem the user should see (bad link, not a laptop, unknown product)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_asin(text):
    text = text.strip()
    if BARE_ASIN.match(text):
        return text.upper()
    url = text if text.startswith("http") else "https://" + text
    for _ in range(5):                      # short links (amzn.to, a.co) redirect to /dp/ASIN
        m = ASIN_IN_URL.search(url)
        if m:
            return m.group(1).upper()
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            opener.open(urllib.request.Request(url, method="HEAD",
                                               headers={"User-Agent": "Mozilla/5.0"}), timeout=15)
            break
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code in (301, 302, 303, 307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                continue
            break
        except urllib.error.URLError:
            break
    raise LookupError_("Couldn't find an Amazon product ID in that link. Paste the full product "
                       "page link (it contains /dp/ followed by a 10-character code) or the code itself.")


def fetch_product(asin):
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    cache = LIVE_DIR / f"{asin}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < FRESH_HOURS * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    data = keepa.call("product", domain=1, asin=asin, history=1)
    products = data.get("products") or []
    if not products or not products[0].get("title"):
        raise LookupError_(f"Amazon has no product {asin} that we can look up.")
    p = products[0]
    cache.write_text(json.dumps(p), encoding="utf-8")
    return p


def listing_for(asin):
    """Saved Keepa listing for an ASIN (live lookup or test-set fetch), or None."""
    for folder in (LIVE_DIR, LIVE_DIR.parent / "keepa"):
        f = folder / f"{asin}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    return None


def is_laptop(p):
    tree = [c.get("name", "") for c in (p.get("categoryTree") or [])]
    if tree:
        return any("Laptop" in n for n in tree) and not any("Accessor" in n and n !=
                                                             "Computers & Accessories" for n in tree)
    return bool(LAPTOP_WORDS.search(p.get("title") or ""))


def match_reviews(conn, p):
    """Return (parent_asin, review_count) of our best-matching review set, or (None, 0)."""
    cands = {p["asin"]} | {v["asin"] for v in (p.get("variations") or []) if v.get("asin")}
    if p.get("parentAsin"):
        cands.add(p["parentAsin"])
    marks = ",".join("?" * len(cands))
    row = conn.execute(f"SELECT parent_asin, COUNT(*) n FROM reviews WHERE asin IN ({marks}) "
                       f"OR parent_asin IN ({marks}) GROUP BY parent_asin ORDER BY n DESC LIMIT 1",
                       [*cands, *cands]).fetchone()
    return (row["parent_asin"], row["n"]) if row else (None, 0)


def tier_for(price):
    if price is None:
        return ""
    return "budget" if price < 500 else "mid-range" if price < 1000 else "premium"


def current_price(p):
    for idx in (1, 0):                       # lowest new, then Amazon
        s = (p.get("csv") or [None, None])[idx] or []
        if s and s[-1] >= 0:
            return s[-1] / 100
    return None


def lookup(conn, text):
    asin = resolve_asin(text)
    p = fetch_product(asin)
    if not is_laptop(p):
        raise LookupError_(f"That looks like \"{(p.get('title') or '')[:80]}\", which isn't a "
                           "laptop. Myrmech Explorer only covers laptops for now.")
    return register(conn, p)


def short_title(title, limit=64):
    """Amazon titles are long; cut at a word boundary and drop trailing punctuation."""
    if len(title) <= limit:
        return title
    return title[:limit].rsplit(" ", 1)[0].rstrip(",;:|-– ")


def register(conn, p):
    """Store a Keepa product's price history and add it to products (unless it's in the
    test set already). Returns its products row."""
    asin = p["asin"]
    store(conn, [p])                          # refresh its price history
    existing = conn.execute("SELECT * FROM products WHERE asin = ?", (asin,)).fetchone()
    if existing and existing["status"] != "lookup":
        return existing
    parent, n_reviews = match_reviews(conn, p)
    title = p.get("title") or ""
    conn.execute("INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?)", (
        parent or asin, asin, "laptop", p.get("brand") or "", short_title(title),
        tier_for(current_price(p)), title, n_reviews, "lookup"))
    conn.commit()
    return conn.execute("SELECT * FROM products WHERE asin = ?", (asin,)).fetchone()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    from backend.price_agent import analyze, LABEL
    conn = connect()
    try:
        product = lookup(conn, sys.argv[1])
    except LookupError_ as e:
        sys.exit(str(e))
    r = analyze(conn, product)
    print(f"{product['brand']} {product['model']} ({product['asin']})")
    print(f"Reviews in our data: {product['review_count']}")
    print(f"Verdict: {LABEL[r['verdict']]}")
    for line in r["reasons"]:
        print(f"  - {line}")
