"""All owner reviews we have for one laptop, from every source, in one shape.

Sources:
- "Amazon 2023": the Amazon Reviews 2023 dataset in dealsense.db (older laptops).
- "Best Buy": live reviews for the same model at Best Buy (current laptops), when
  bestbuy_match finds it. If Best Buy can't be reached, we carry on without it.

Each review is a dict: source, rating, title, text, date, verified, incentivized.
"""
import sys
from datetime import datetime, timezone

from backend.search import bm25_rank

DATASET = "Amazon 2023"
BESTBUY = "Best Buy"


def dataset_reviews(conn, product):
    rows = conn.execute("SELECT * FROM reviews WHERE parent_asin = ?",
                        (product["parent_asin"],)).fetchall()
    return [{"source": DATASET, "review_id": r["id"], "rating": r["rating"],
             "title": r["title"] or "", "text": r["text"] or "",
             "date": datetime.fromtimestamp(r["timestamp"] / 1000, timezone.utc)
                     .strftime("%Y-%m"),
             "verified": bool(r["verified"]), "incentivized": False} for r in rows]


def bestbuy(product):
    """(summary, reviews) for the matching Best Buy product, or (None, [])."""
    from backend.bestbuy_match import find_sku
    from backend.reviews_live import bestbuy_reviews
    try:
        m = find_sku({"asin": product["asin"], "title": product["title"],
                      "brand": product["brand"]})["match"]
        if not m or not m.get("review_count"):
            return None, []
        d = bestbuy_reviews(m["sku"])
    except Exception as e:                      # Best Buy down or blocking: no live reviews
        print(f"Best Buy reviews unavailable for {product['asin']}: {e}", file=sys.stderr)
        return None, []
    revs = [{"source": BESTBUY, "review_id": r["id"], "rating": r["rating"],
             "title": r["title"], "text": r["text"], "date": r["date"],
             "verified": r["verified"], "incentivized": r["incentivized"]}
            for r in d["reviews"]]
    summary = {"sku": m["sku"], "name": m["name"], "url": m["url"],
               "rating": m["rating"], "total_reviews": d["total_reviews"],
               "downloaded": len(revs),
               "incentivized": sum(r["incentivized"] for r in revs),
               "distribution": d["distribution"],
               "pros_tally": d["pros_tally"], "cons_tally": d["cons_tally"]}
    return summary, revs


def gather_all(conn, product):
    """{'sources': {name: [reviews]}, 'bestbuy': summary or None}"""
    bb, bb_revs = bestbuy(product)
    sources = {}
    if bb_revs:
        sources[BESTBUY] = bb_revs
    ds = dataset_reviews(conn, product)
    if ds:
        sources[DATASET] = ds
    return {"sources": sources, "bestbuy": bb}


def search(pool, query, per_source=2, min_words=15):
    """Top reviews for a query from each source (newer Best Buy reviews listed first)."""
    out = []
    for name in (BESTBUY, DATASET):
        out += [r for _, r in bm25_rank(pool["sources"].get(name, []), query,
                                         k=per_source, min_words=min_words)]
    return out


def bestbuy_facts(bb):
    """Plain-language facts about the Best Buy reviews, for the prompt ([B])."""
    if not bb:
        return []
    dist = bb["distribution"] or {}
    total = sum(dist.values()) or bb["total_reviews"] or 0
    facts = [f"Best Buy has {bb['total_reviews']} reviews for this model "
             f"(average {bb['rating']} out of 5)."]
    if total:
        five, low = dist.get(5, 0), dist.get(1, 0) + dist.get(2, 0)
        facts.append(f"{five / total:.0%} are 5-star; {low / total:.0%} are 1- or 2-star.")
    for label, tally in (("pros", bb["pros_tally"]), ("cons", bb["cons_tally"])):
        top = list(tally.items())[:4]
        if top:
            facts.append(f"Best Buy owners most often tag as {label}: "
                         + ", ".join(f"{k} ({v})" for k, v in top) + ".")
    if bb["downloaded"]:
        facts.append(f"Of the {bb['downloaded']} newest Best Buy reviews, {bb['incentivized']} "
                     "were incentivized (the reviewer got a promotion or sweepstakes entry for "
                     "writing it), which tends to make reviews more positive.")
    return facts
