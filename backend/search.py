"""Review search for one product: BM25 keyword ranking.

This is the research agent's retrieval step and also the keyword baseline that the
embedding-based retriever will be compared against later.

Usage, from the Dealsense folder:
    python -m backend.search B08N5LNQCX "battery life"
    python -m backend.search "MacBook Air M1" "screen quality" -k 3
"""
import math
import re
import sys
from collections import Counter

from backend.db import connect

STOP = set("""a an and are as at be but by for from has have i if in is it its my of on or so
that the this to was were will with you your me we they them very just not no""".split())


def tokenize(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP]


def find_product(conn, key):
    """Accept an ASIN, a parent ASIN, or part of the model/title."""
    row = conn.execute("SELECT * FROM products WHERE asin = ? OR parent_asin = ?",
                       (key, key)).fetchone()
    if row:
        return row
    return conn.execute("SELECT * FROM products WHERE model LIKE ? OR title LIKE ? "
                        "ORDER BY review_count DESC", (f"%{key}%", f"%{key}%")).fetchone()


def bm25_search(conn, parent_asin, query, k=5, k1=1.5, b=0.75, min_words=15):
    # skip one-liners ("Great battery life"): they match well but give the agent nothing to cite
    reviews = [r for r in conn.execute("SELECT * FROM reviews WHERE parent_asin = ?",
                                       (parent_asin,))
               if len(r["text"].split()) >= min_words]
    docs = [tokenize(f"{r['title']} {r['text']}") for r in reviews]
    if not docs:
        return []
    n = len(docs)
    avg_len = sum(map(len, docs)) / n
    df = Counter(t for d in docs for t in set(d))
    q = tokenize(query)
    scored = []
    for r, d in zip(reviews, docs):
        tf = Counter(d)
        score = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg_len))
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


if __name__ == "__main__":
    args = sys.argv[1:]
    k = int(args[args.index("-k") + 1]) if "-k" in args else 5
    key, query = args[0], args[1]
    conn = connect()
    product = find_product(conn, key)
    if not product:
        sys.exit(f"No product matches {key!r}")
    print(f"{product['brand']} {product['model']} ({product['asin']}), query: {query!r}\n")
    for score, r in bm25_search(conn, product["parent_asin"], query, k):
        print(f"[{score:5.2f}] {r['rating']:.0f}* {r['title']}")
        print(f"        {r['text'][:300].replace(chr(10), ' ')}\n")
