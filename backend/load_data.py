"""Rebuild products and reviews in dealsense.db from the pipeline output (safe to re-run).

Loads the test set and its reviews, plus every review in reviews_all.jsonl if that file
exists (the full ~4,100-laptop pull), so pasted links can find reviews too.

Usage, from the Dealsense folder:
    py -m backend.load_data
"""
import csv
import html
import json
import re
import sys
from pathlib import Path

from backend.db import connect

products_csv = sys.argv[1] if len(sys.argv) > 1 else "laptops_testset_pass2.csv"
review_files = [f for f in ("reviews_testset.jsonl", "reviews_all.jsonl") if Path(f).exists()]


def clean(text):
    """Strip Amazon's markup: <br /> tags, [[VIDEOID:...]] placeholders, HTML entities."""
    text = re.sub(r"<br\s*/?>", " ", text or "")
    text = re.sub(r"\[\[VIDEOID:[^\]]*\]\]", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


conn = connect()
conn.execute("DROP TABLE IF EXISTS products")
conn.execute("DROP TABLE IF EXISTS reviews")
conn.close()
conn = connect()                      # recreates both tables with the current schema

products = list(csv.DictReader(open(products_csv, encoding="utf-8")))
conn.executemany(
    "INSERT INTO products VALUES (:parent_asin, :asin, :category, :brand, :model, :price_tier,"
    " :title, :review_count, :status)",
    [{**p, "review_count": int(p["review_count"] or 0)} for p in products])

seen, rows = set(), []
for fname in review_files:
    with open(fname, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            text = clean(r.get("text"))
            key = (r["parent_asin"], r["asin"], r["timestamp"], text[:60])
            if not text or key in seen:
                continue
            seen.add(key)
            rows.append((r["parent_asin"], r["asin"], r["rating"], clean(r["title"]), text,
                         r["timestamp"], r["helpful_vote"], int(bool(r["verified_purchase"]))))
conn.executemany("INSERT INTO reviews (parent_asin, asin, rating, title, text, timestamp,"
                 " helpful_vote, verified) VALUES (?,?,?,?,?,?,?,?)", rows)
conn.commit()

n_parents = conn.execute("SELECT COUNT(DISTINCT parent_asin) FROM reviews").fetchone()[0]
print(f"Loaded {len(products)} test-set products and {len(rows):,} reviews "
      f"covering {n_parents:,} laptops (from {', '.join(review_files)}).")
for p in conn.execute("SELECT p.brand, p.model, p.price_tier, p.status, COUNT(r.id) n "
                      "FROM products p LEFT JOIN reviews r USING (parent_asin) "
                      "GROUP BY p.asin ORDER BY p.price_tier, n DESC"):
    print(f"  {p['price_tier']:9} {p['status']:8} {p['brand']:9} {p['model'][:34]:34} "
          f"{p['n']:>5} reviews")
print("\nPrice history is kept. If you rebuilt from scratch, run: py -m backend.prices reparse")
