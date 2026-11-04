"""Live owner reviews from Best Buy, for laptops newer than the 2023 Amazon dataset.

Amazon now hides review text from signed-out visitors (every scraper we tested, including
Bright Data, got "Sign in to see customer reviews"). Best Buy shows all reviews publicly
through its own review feed, which we read directly. Plain Python requests get no answer
from Best Buy's bot protection, so we use curl_cffi, which connects the way Chrome does.

Each review keeps Best Buy's badges, so the write-up can say how many reviews were
incentivized (the reviewer got a promotion for writing it) or verified purchases.

Results are cached for 7 days in data/reviews_live/bestbuy_<sku>.json.

Test, from the Dealsense folder:
    py -m backend.reviews_live 12974438          (the SKU shown on the Best Buy page)
    py -m backend.reviews_live 12974438 --refresh
"""
import json
import sys
import time
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "reviews_live"
CACHE_DAYS = 7
FEED = ("https://www.bestbuy.com/ugc/v2/reviews?page={page}&pageSize=20&sku={sku}"
        "&sort=MOST_RECENT")
PAGES = 5                # 20 reviews per page, newest first: up to 100 reviews


def _get(url):
    try:
        from curl_cffi import requests as creq
    except ImportError:
        sys.exit("Run first: py -m pip install curl_cffi")
    r = creq.get(url, impersonate="chrome", timeout=30,
                 headers={"Accept": "application/json, text/plain, */*"})
    if r.status_code != 200:
        raise RuntimeError(f"Best Buy returned {r.status_code} for {url}")
    return r.json()


def _badges(topic):
    return {b.get("badgeCode") for b in topic.get("badges") or []}


def fetch(sku, pages=PAGES):
    """Download up to `pages` pages of reviews plus Best Buy's summary stats."""
    first = _get(FEED.format(page=1, sku=sku))
    topics = list(first.get("topics") or [])
    for page in range(2, min(pages, first.get("totalPages") or 1) + 1):
        time.sleep(0.5)                                   # be gentle with their server
        topics += _get(FEED.format(page=page, sku=sku)).get("topics") or []
    reviews = []
    for t in topics:
        if t.get("topicType") != "review" or not (t.get("text") or "").strip():
            continue
        b = _badges(t)
        reviews.append({
            "id": t.get("id"), "source": "Best Buy", "rating": t.get("rating"),
            "title": t.get("title") or "", "text": t["text"].strip(),
            "date": (t.get("submissionTime") or "")[:10],
            "verified": "VerifiedPurchaser" in b, "incentivized": "Incentivized" in b,
            "days_owned": t.get("daysOfOwnership"), "recommended": t.get("recommended"),
            "product": (t.get("productDetails") or {}).get("name")})
    stats = first.get("stats") or {}
    return {"sku": str(sku), "fetched": time.strftime("%Y-%m-%d"),
            "total_reviews": first.get("totalResults"),
            "verified_count": stats.get("verifiedPurchaseCount"),
            "distribution": {d["value"]: d["count"] for d in stats.get("distribution") or []},
            "pros_tally": {d["value"]: d["count"] for d in stats.get("prosDistribution") or []},
            "cons_tally": {d["value"]: d["count"] for d in stats.get("consDistribution") or []},
            "reviews": reviews}


def bestbuy_reviews(sku, refresh=False):
    """Cached reviews + stats for one Best Buy SKU (7 or 8 digits)."""
    sku = "".join(c for c in str(sku) if c.isdigit())
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"bestbuy_{sku}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < CACHE_DAYS * 86400:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):                        # skip old scraper-format files
            return data
    data = fetch(sku)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    started = time.time()
    d = bestbuy_reviews(args[0], refresh="--refresh" in sys.argv)
    revs = d["reviews"]
    words = sorted(len(r["text"].split()) for r in revs)
    print(f"Best Buy SKU {d['sku']}: {d['total_reviews']} reviews in total, "
          f"{len(revs)} downloaded ({time.time() - started:.0f}s)")
    if revs:
        print(f"Dates: {min(r['date'] for r in revs)} to {max(r['date'] for r in revs)}")
        print(f"Words per review: median {words[len(words) // 2]}, "
              f"{sum(w >= 15 for w in words)} of {len(words)} have 15+ words")
        print(f"Incentivized: {sum(r['incentivized'] for r in revs)} of {len(revs)}; "
              f"verified purchase: {sum(r['verified'] for r in revs)} of {len(revs)}")
    print(f"Star breakdown (all reviews): {d['distribution']}")
    print(f"Owners' top pros: {dict(list(d['pros_tally'].items())[:4])}")
    print(f"Owners' top cons: {dict(list(d['cons_tally'].items())[:4])}\n")
    for r in revs[:3]:
        print(f"{r['rating']}* {r['date']}  {r['title']}"
              f"{'  [incentivized]' if r['incentivized'] else ''}")
        print(f"  {r['text'][:250]}{'...' if len(r['text']) > 250 else ''}\n")
