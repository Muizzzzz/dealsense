"""Research step: combine the price verdict with relevant reviews, and have Claude write
a short, cited answer. Claude explains the verdict; it does not decide it.

Usage, from the Dealsense folder:
    py -m backend.writeup "Swift" --dry-run     # show what would be sent; no API call
    py -m backend.writeup "Swift"               # call Claude (needs ANTHROPIC_API_KEY in .env)
    py -m backend.writeup "Swift" --refresh     # ignore the cached answer

Answers are cached in data/writeups/<asin>.json.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from backend.db import connect
from backend.keepa import load_env
from backend.lookup import listing_for
from backend.price_agent import analyze
from backend import categories
from backend import reviews as review_pool
from backend.search import find_product

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "writeups"
MODEL = "claude-sonnet-5-5"     # override with DEALSENSE_MODEL in .env
PER_ASPECT = 2
SNIPPET = 600

SYSTEM = """You are Myrmech Explorer, a shopping assistant that tells people whether a product's \
current price is a good deal and what owners say about it. It covers laptops and headphones.

Rules:
- Use only the PRICE FACTS, LISTING, BEST BUY SUMMARY and REVIEWS provided. Do not add specs, \
prices, or opinions from your own knowledge.
- Reviews come from two sources: current Best Buy reviews of the same model, and older Amazon \
reviews (2023 dataset). Prefer the Best Buy reviews when they disagree, since they are newer. \
Cite the BEST BUY SUMMARY as [B].
- If most Best Buy reviews are marked incentivized, say once, briefly, that many reviews were \
written in exchange for a promotion and may lean positive. Weigh complaints in those reviews \
more than praise.
- The LISTING is the seller's own description: treat it as claims, not facts, and cite it \
as [L]. Use it only to say what the product is, never as evidence that it is good.
- If there are no REVIEWS, say plainly that we found no owner reviews for this product, leave "pros" and "cons" as empty lists, and base the advice on price alone.
- The price verdict is already decided by a statistical model. Explain it; never change it.
- Cite every claim from a review with its ID in square brackets, e.g. [R3]. Cite price \
facts with [P] and the Best Buy summary with [B].
- If reviews disagree, say so. If the evidence on something is thin, say that too.
- Plain, friendly language. No hype. Be brief: the whole answer should be under 250 words.

Return only JSON, with exactly these keys:
{"headline": one sentence, the overall answer,
 "price_explanation": 2 sentences explaining the price verdict, citing [P],
 "pros": list of up to 4 strings, each under 20 words, citing reviews,
 "cons": list of up to 4 strings, each under 20 words, citing reviews,
 "bottom_line": 1-2 sentences of advice: buy now, wait, or look elsewhere}"""

VERDICT_TEXT = {"genuine_low": "GENUINE LOW (a good time to buy)",
                "typical": "TYPICAL (a normal price for this product)",
                "inflated": "INFLATED (above its usual price)",
                "unavailable": "UNAVAILABLE (nobody is selling it new right now)",
                "insufficient_history": "NOT ENOUGH PRICE HISTORY",
                "no_data": "NO PRICE DATA"}


def gather(conn, product):
    """Price verdict, the most relevant reviews per aspect from every source, and the
    Best Buy summary (or None)."""
    price = analyze(conn, product)
    pool = review_pool.gather_all(conn, product)
    per_source = 1 if len(pool["sources"]) > 1 else PER_ASPECT
    seen, reviews = set(), []
    for aspect in categories.get(product["category"])["aspects"]:
        for r in review_pool.search(pool, aspect, per_source=per_source):
            key = (r["source"], r["review_id"])
            if key in seen:
                continue
            seen.add(key)
            text = r["text"] if len(r["text"]) <= SNIPPET else r["text"][:SNIPPET] + "..."
            reviews.append({"id": f"R{len(reviews) + 1}", "review_id": r["review_id"],
                            "source": r["source"], "aspect": aspect, "rating": r["rating"],
                            "title": r["title"], "text": text, "date": r["date"],
                            "verified": r["verified"], "incentivized": r["incentivized"]})
    counts = {name: len(revs) for name, revs in pool["sources"].items()}
    return price, reviews, pool["bestbuy"], counts


def build_prompt(product, price, reviews, bestbuy=None):
    noun = categories.get(product["category"])["noun"].upper()
    lines = [f"{noun}: {product['brand']} {product['model']}",
             f"Listing title: {product['title']}"]
    listing = listing_for(product["asin"]) or {}
    features = [f for f in (listing.get("features") or []) if f][:6]
    if features:
        lines.append("LISTING [L] (seller's bullet points):")
        lines += [f"- {f[:200]}" for f in features]
    lines += ["",
             f"PRICE VERDICT (as of {price['as_of']}): {VERDICT_TEXT[price['verdict']]}",
             "PRICE FACTS [P]:"]
    lines += [f"- {r}" for r in price["reasons"]]
    facts = review_pool.bestbuy_facts(bestbuy)
    if facts:
        lines += ["", "BEST BUY SUMMARY [B] (all Best Buy reviews of this model):"]
        lines += [f"- {f}" for f in facts]
    if not reviews:
        lines += ["", "REVIEWS: none. We found no owner reviews for this product."]
        return "\n".join(lines)
    lines += ["", f"REVIEWS ({len(reviews)} most relevant):"]
    for r in reviews:
        tags = [r["source"], f"{r['rating']:.0f}/5 stars", str(r["date"])[:7]]
        if r["verified"]:
            tags.append("verified purchase")
        if r["incentivized"]:
            tags.append("incentivized")
        lines.append(f"[{r['id']}] {', '.join(tags)} (found for: {r['aspect']})")
        lines.append(f"Title: {r['title']}")
        lines.append(f"{r['text']}")
        lines.append("")
    return "\n".join(lines)


def call_claude(prompt, system=None, max_tokens=2000):
    """One Claude call. Other modules (shop.py) pass their own system prompt."""
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not found in .env. Use --dry-run to test without it.")
    body = json.dumps({"model": os.environ.get("MODEL", os.environ.get("DEALSENSE_MODEL", MODEL)),
                       "max_tokens": max_tokens, "system": system or SYSTEM,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Claude API error {e.code}: {e.read().decode(errors='replace')[:500]}")
    text = "".join(b.get("text", "") for b in data.get("content", []))
    if data.get("stop_reason") == "max_tokens":
        print("Warning: Claude's answer hit the length limit and was cut off.", file=sys.stderr)
    return text, data.get("usage", {})


def parse_answer(text):
    """Claude is asked for bare JSON; tolerate a code fence around it."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1])


def writeup(conn, product, refresh=False, dry_run=False):
    price, reviews, bestbuy, counts = gather(conn, product)
    prompt = build_prompt(product, price, reviews, bestbuy)
    if dry_run:
        return {"prompt": prompt, "system": SYSTEM, "price": price, "reviews": reviews}
    cache = CACHE / f"{product['asin']}.json"
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if (cached.get("price", {}).get("as_of") == str(date.today())
                and "raw" not in cached.get("answer", {})
                and "review_counts" in cached):    # written before live reviews: redo
            return cached
    text, usage = call_claude(prompt)
    try:
        answer, ok = parse_answer(text), True
    except (ValueError, json.JSONDecodeError):
        answer, ok = {"headline": "Could not parse the write-up.", "raw": text}, False
    result = {"product": f"{product['brand']} {product['model']}", "asin": product["asin"],
              "price": price, "reviews": reviews, "answer": answer, "usage": usage,
              "bestbuy": bestbuy, "review_counts": counts}
    if ok:                                 # never cache a broken answer
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    conn = connect()
    product = find_product(conn, args[0])
    if not product:
        sys.exit(f"No product matches {args[0]!r}")
    r = writeup(conn, product, refresh="--refresh" in sys.argv, dry_run="--dry-run" in sys.argv)
    if "prompt" in r:
        print("=== SYSTEM ===\n" + r["system"] + "\n\n=== USER ===\n" + r["prompt"])
        approx = (len(r["system"]) + len(r["prompt"])) // 4
        print(f"=== about {approx:,} input tokens; at $2 per million that's "
              f"${approx * 2 / 1e6:.3f} per call ===")
    else:
        a = r["answer"]
        print(f"{r['product']}: {VERDICT_TEXT[r['price']['verdict']]}\n")
        print(a.get("headline", ""), "\n")
        print(a.get("price_explanation", ""), "\n")
        for label in ("pros", "cons"):
            print(label.upper())
            for item in a.get(label, []):
                print(f"  + {item}" if label == "pros" else f"  - {item}")
        print("\n" + a.get("bottom_line", a.get("raw", "")))
        u = r.get("usage", {})
        if u:
            print(f"\n({u.get('input_tokens')} input + {u.get('output_tokens')} output tokens)")
