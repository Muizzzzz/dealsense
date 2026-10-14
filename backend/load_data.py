"""Load the test-set laptops and their reviews into dealsense.db (safe to re-run; it rebuilds).

Usage, from the Dealsense folder:
    python -m backend.load_data
    python -m backend.load_data laptops_testset_pass2.csv reviews_testset.jsonl
"""
import csv
import html
import json
import re
import sys

from backend.db import connect

products_csv = sys.argv[1] if len(sys.argv) > 1 else "laptops_testset_pass2.csv"
reviews_jsonl = sys.argv[2] if len(sys.argv) > 2 else "reviews_testset.jsonl"

conn = connect()
conn.execute("DELETE FROM reviews")
conn.execute("DELETE FROM products")

products = list(csv.DictReader(open(products_csv, encoding="utf-8")))
conn.executemany(
    "INSERT INTO products VALUES (:parent_asin, :asin, :category, :brand, :model, :price_tier,"
    " :title, :review_count, :status)",
    [{**p, "review_count": int(p["review_count"] or 0)} for p in products])
wanted = {p["parent_asin"] for p in products}



def clean(text):
    """Strip Amazon's markup: <br /> tags, [[VIDEOID:...]] placeholders, HTML entities."""
    text = re.sub(r"<br\s*/?>", " ", text or "")
    text = re.sub(r"\[\[VIDEOID:[^\]]*\]\]", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


rows = []
with open(reviews_jsonl, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r["parent_asin"] in wanted and (r.get("text") or "").strip():
            rows.append((r["parent_asin"], r["asin"], r["rating"], clean(r["title"]), clean(r["text"]),
                         r["timestamp"], r["helpful_vote"], int(bool(r["verified_purchase"]))))
conn.executemany("INSERT INTO reviews (parent_asin, asin, rating, title, text, timestamp,"
                 " helpful_vote, verified) VALUES (?,?,?,?,?,?,?,?)", rows)
conn.commit()

print(f"Loaded {len(products)} products and {len(rows):,} reviews into dealsense.db")
for p in conn.execute("SELECT p.brand, p.model, p.price_tier, COUNT(r.id) n FROM products p "
                      "LEFT JOIN reviews r USING (parent_asin) GROUP BY p.parent_asin "
                      "ORDER BY p.price_tier, n DESC"):
    print(f"  {p['price_tier']:9} {p['brand']:9} {p['model']:32} {p['n']:>5} reviews")
