"""Ask Myrmech Explorer a question.

Two kinds of question:
1. Shopping ("a good laptop with an RTX 50-series card around $800"):
   Claude turns the question into Amazon search terms + a price range + must-haves,
   Keepa searches Amazon live (10 tokens per search of 10 results), each match gets a
   price verdict, and Claude recommends from those real listings, citing them.
2. About one laptop ("is the battery good?"): its most relevant reviews, price facts and
   listing go to Claude, which answers with citations.

Usage, from the Dealsense folder:
    py -m backend.shop "good laptop with an rtx 5060 around $800"
    py -m backend.shop "is the battery life good?" --asin B086KKKT15
"""
import json
import re
import sys
import time
from pathlib import Path

from backend import keepa
from backend.db import connect
from backend.lookup import current_price, is_laptop, listing_for, register
from backend.price_agent import analyze
from backend.search import bm25_search
from backend.writeup import VERDICT_TEXT, call_claude, parse_answer

SEARCH_CACHE = Path(__file__).resolve().parent.parent / "data" / "keepa_search"
CACHE_HOURS = 6
MAX_TERMS = 2          # Keepa searches per question (10 tokens each)
MAX_CANDIDATES = 8

PLANNER = """You turn a shopper's question into an Amazon laptop search. Today's date is \
{today}. Return only JSON:
{{"is_laptop_question": true or false,
 "search_terms": up to 2 short Amazon search phrases, most specific first,
 "min_price": number or null, "max_price": number or null,
 "must_have": list of short regular expressions that a matching listing title must contain \
(case-insensitive), e.g. "RTX ?50[5-9]0" for an NVIDIA RTX 50-series GPU; [] if none,
 "summary": one sentence restating what they want}}
If they give one target price ("around $800"), use a range of about -20% to +15%."""

RECOMMENDER = """You are Myrmech Explorer, a laptop shopping assistant. Answer the shopper's \
question using only the CANDIDATES provided: real Amazon listings found just now, each with \
its current price and a price verdict from a statistical model.

Rules:
- Recommend only from the candidates. Cite each one you mention as [A1], [A2], etc.
- Never invent specs, prices or reviews. Listing details are the seller's claims; say so \
when it matters. Owner reviews are rare for new laptops; use them only if given.
- Respect the price verdicts: don't call an INFLATED price a good deal.
- If nothing fits well, say so plainly and suggest what to change (budget, specs).
- Be brief: under 200 words.

Return only JSON:
{"answer": 2-4 sentences answering the question,
 "picks": list of up to 3 {"id": "A1", "why": one sentence, citing it},
 "caveats": one sentence on what to double-check before buying, or ""}"""

PRODUCT_QA = """You are Myrmech Explorer. Answer the shopper's question about ONE laptop using \
only the PRICE FACTS [P], LISTING [L] and REVIEWS provided. Cite review claims with their IDs \
([R2]). If the evidence doesn't answer the question, say so plainly. Under 150 words.
Return only JSON: {"answer": your answer, "confidence": "high" | "medium" | "low"}"""


# ---------------------------------------------------------------- product questions

def ask_product(conn, asin, question):
    product = conn.execute("SELECT * FROM products WHERE asin = ?", (asin,)).fetchone()
    if not product:
        return {"error": "Open a laptop first, then ask about it."}
    price = analyze(conn, product)
    hits = bm25_search(conn, product["parent_asin"], question, k=8, min_words=8)
    reviews = [{"id": f"R{i + 1}", "rating": r["rating"], "title": r["title"],
                "text": r["text"][:700]} for i, (_, r) in enumerate(hits)]
    listing = listing_for(asin) or {}
    lines = [f"LAPTOP: {product['brand']} {product['model']}",
             f"QUESTION: {question}", "",
             f"PRICE VERDICT: {VERDICT_TEXT[price['verdict']]}", "PRICE FACTS [P]:"]
    lines += [f"- {x}" for x in price["reasons"]]
    feats = [f for f in (listing.get("features") or []) if f][:6]
    if feats:
        lines += ["", "LISTING [L]:"] + [f"- {f[:200]}" for f in feats]
    lines += ["", f"REVIEWS ({len(reviews)} most relevant to the question):" if reviews
              else "REVIEWS: none found for this question."]
    for r in reviews:
        lines += [f"[{r['id']}] {r['rating']:.0f}/5. {r['title']}: {r['text']}"]
    text, usage = call_claude("\n".join(lines), system=PRODUCT_QA, max_tokens=1200)
    try:
        answer = parse_answer(text)
    except (ValueError, json.JSONDecodeError):
        answer = {"answer": text.strip()}
    return {"kind": "product", "question": question, "asin": asin, "answer": answer,
            "reviews": reviews, "usage": usage}


# ---------------------------------------------------------------- shopping questions

def keepa_search(term):
    """One Keepa product search (10 tokens), cached for a few hours."""
    SEARCH_CACHE.mkdir(parents=True, exist_ok=True)
    f = SEARCH_CACHE / (re.sub(r"[^a-z0-9]+", "_", term.lower())[:80] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_HOURS * 3600:
        return json.loads(f.read_text(encoding="utf-8"))
    data = keepa.call("search", domain=1, type="product", term=term, page=0, history=1)
    products = data.get("products") or []
    if products and not any(p.get("csv") for p in products):
        # the search came back without price history: fetch it (1 token per product)
        asins = ",".join(p["asin"] for p in products if p.get("asin"))
        products = keepa.call("product", domain=1, asin=asins, history=1).get("products", [])
    f.write_text(json.dumps(products), encoding="utf-8")
    return products


def plan(question):
    from datetime import date
    text, _ = call_claude(question, system=PLANNER.format(today=date.today()), max_tokens=600)
    return parse_answer(text)


def ask_shopping(conn, question):
    p = plan(question)
    if not p.get("is_laptop_question", True):
        return {"kind": "shopping", "question": question, "plan": p, "candidates": [],
                "answer": {"answer": "Myrmech Explorer only covers laptops for now. Try "
                           "asking about a laptop, or paste a laptop link.", "picks": [],
                           "caveats": ""}}
    lo, hi = p.get("min_price"), p.get("max_price")
    must = [re.compile(m, re.I) for m in (p.get("must_have") or []) if m]
    found, seen = [], set()
    for term in (p.get("search_terms") or [question])[:MAX_TERMS]:
        for prod in keepa_search(term):
            asin = prod.get("asin")
            if not asin or asin in seen or not is_laptop(prod):
                continue
            seen.add(asin)
            price = current_price(prod)
            title = prod.get("title") or ""
            if price is None or (lo and price < lo) or (hi and price > hi):
                continue
            if not all(m.search(title) for m in must):
                continue
            found.append(prod)
    found = found[:MAX_CANDIDATES]

    candidates = []
    for i, prod in enumerate(found):
        row = register(conn, prod)
        verdict = analyze(conn, row)
        feats = [f[:160] for f in (prod.get("features") or []) if f][:4]
        reviews = [{"rating": r["rating"], "text": r["text"][:300]}
                   for _, r in bm25_search(conn, row["parent_asin"],
                                           "performance battery screen problem", k=2)]
        candidates.append({"id": f"A{i + 1}", "asin": prod["asin"],
                           "name": (prod.get("title") or "")[:90],
                           "price": verdict.get("current_price"), "verdict": verdict["verdict"],
                           "median": verdict.get("median"), "reasons": verdict["reasons"][:3],
                           "features": feats, "review_count": row["review_count"],
                           "reviews": reviews})

    if not candidates:
        return {"kind": "shopping", "question": question, "plan": p, "candidates": [],
                "answer": {"answer": "I searched Amazon but found no laptops matching that "
                           f"({p.get('summary', '')}). Try a wider price range or fewer "
                           "must-have specs.", "picks": [], "caveats": ""}}

    lines = [f"QUESTION: {question}", f"WHAT THEY WANT: {p.get('summary', '')}", "",
             f"CANDIDATES ({len(candidates)} live Amazon listings):"]
    for c in candidates:
        lines += [f"[{c['id']}] {c['name']}",
                  f"  Price now: ${c['price']:,.2f}; verdict: {VERDICT_TEXT[c['verdict']]}"
                  if c["price"] else f"  verdict: {VERDICT_TEXT[c['verdict']]}"]
        lines += [f"  Price fact: {r}" for r in c["reasons"]]
        lines += [f"  Listing: {f}" for f in c["features"]]
        lines += [f"  Owner review ({r['rating']:.0f}/5): {r['text']}" for r in c["reviews"]]
        lines.append("")
    text, usage = call_claude("\n".join(lines), system=RECOMMENDER, max_tokens=1500)
    try:
        answer = parse_answer(text)
    except (ValueError, json.JSONDecodeError):
        answer = {"answer": text.strip(), "picks": [], "caveats": ""}
    return {"kind": "shopping", "question": question, "plan": p, "candidates": candidates,
            "answer": answer, "usage": usage}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    conn = connect()
    if "--asin" in args:
        i = args.index("--asin")
        r = ask_product(conn, args[i + 1], " ".join(args[:i] + args[i + 2:]))
        print(json.dumps(r["answer"], indent=2))
    else:
        r = ask_shopping(conn, " ".join(args))
        print("Plan:", json.dumps(r["plan"]))
        for c in r["candidates"]:
            print(f"  [{c['id']}] ${c['price'] or 0:,.2f} {c['verdict']:12} {c['name'][:70]}")
        print("\n" + json.dumps(r["answer"], indent=2))
