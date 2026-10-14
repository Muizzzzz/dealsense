"""DealSense - Step 3, Pass 1: shortlist laptop candidates from Amazon Reviews 2023 metadata.

Streams meta_Electronics.jsonl (5.2 GB) line by line, so it never stores the whole file.
Uses only the Python standard library.

Usage:
    python pass1_shortlist.py                       # stream straight from Hugging Face
    python pass1_shortlist.py meta_Electronics.jsonl  # or read a local copy
    python pass1_shortlist.py --max-lines 200000      # quick test on the first 200k products

Outputs (in the current folder):
    laptops_all.csv        every laptop with >= MIN_RATINGS ratings (for manual browsing)
    laptops_shortlist.csv  up to PER_SLOT candidates for each of the 12 test-set slots
    Also prints the laptop category paths it found, so we can check the filter.
"""
import csv
import io
import json
import re
import sys
import urllib.request
from collections import Counter

URL = ("https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/"
       "raw/meta_categories/meta_Electronics.jsonl")
MIN_RATINGS = 50   # rating_number is a cheap proxy for how many reviews a product has
PER_SLOT = 2       # 12 slots x 2 = ~24 candidates

# The 12 slots from laptops_testset.csv: (brand, model, tier, title regex)
SLOTS = [
    ("Acer", "Aspire 5 (A515 series)", "budget", r"aspire 5|a515"),
    ("HP", "15-inch laptop (15-dy / 15-fd series)", "budget", r"\b15[- ]?(dy|fd|ef|dw)\d"),
    ("Lenovo", "IdeaPad 3 15-inch", "budget", r"ideapad 3"),
    ("ASUS", "Vivobook 15", "budget", r"vivobook 15"),
    ("Apple", "MacBook Air M1 (2020)", "mid-range", r"macbook air.*\bm1\b"),
    ("Dell", "Inspiron 15", "mid-range", r"inspiron 15|inspiron 35\d\d"),
    ("Lenovo", "IdeaPad Flex 5", "mid-range", r"flex 5"),
    ("HP", "Envy x360", "mid-range", r"envy x360"),
    ("Apple", "MacBook Air 15-inch M2 (2023)", "premium", r"macbook air.*\bm2\b"),
    ("Dell", "XPS 13", "premium", r"xps 13"),
    ("Microsoft", "Surface Laptop 4", "premium", r"surface laptop 4"),
    ("ASUS", "ROG Zephyrus G14", "premium", r"zephyrus g14"),
]
# Titles that are accessories or renewed units (renewed listings have their own, unrelated price history)
EXCLUDE = re.compile(r"renewed|refurbished|chromebook", re.I)


def is_laptop(meta):
    cats = meta.get("categories") or []
    if not cats and meta.get("main_category") == "Apple Products":
        return "macbook" in (meta.get("title") or "").lower()
    return ("Computers & Tablets" in cats
            and any("Laptop" in c for c in cats)
            and not any("Accessor" in c and c != "Computers & Accessories" for c in cats))


def brand_of(meta):
    store = (meta.get("store") or "").strip()
    maker = ((meta.get("details") or {}).get("Manufacturer") or "").strip()
    return store or maker


def open_lines(source):
    if source.startswith("http"):
        resp = urllib.request.urlopen(source)
        return io.TextIOWrapper(resp, encoding="utf-8")
    return open(source, encoding="utf-8")


def main():
    args = sys.argv[1:]
    max_lines = None
    if "--max-lines" in args:
        i = args.index("--max-lines")
        max_lines = int(args[i + 1])
        del args[i:i + 2]
    source = args[0] if args else URL

    laptops, paths = [], Counter()
    with open_lines(source) as f:
        for n, line in enumerate(f, 1):
            if max_lines and n > max_lines:
                break
            if n % 200_000 == 0:
                print(f"  scanned {n:,} products, {len(laptops)} laptops kept", flush=True)
            meta = json.loads(line)
            if not is_laptop(meta):
                continue
            paths[" > ".join(meta["categories"])] += 1
            title = meta.get("title") or ""
            if (meta.get("rating_number") or 0) < MIN_RATINGS or EXCLUDE.search(title):
                continue
            laptops.append({
                "parent_asin": meta["parent_asin"],
                "brand": brand_of(meta),
                "title": title,
                "price": meta.get("price"),
                "rating_number": meta.get("rating_number"),
                "average_rating": meta.get("average_rating"),
                "date_first_available": (meta.get("details") or {}).get("Date First Available", ""),
            })

    print(f"\nDone. {len(laptops)} laptops with {MIN_RATINGS}+ ratings.")
    print("\nLaptop category paths found (check the filter):")
    for p, c in paths.most_common(15):
        print(f"  {c:6,}  {p}")

    laptops.sort(key=lambda r: r["rating_number"], reverse=True)
    with open("laptops_all.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=list(laptops[0].keys()) if laptops else ["parent_asin"])
        w.writeheader()
        w.writerows(laptops)

    cols = ["category", "asin", "parent_asin", "brand", "model", "price_tier", "review_count",
            "status", "title", "rating_number", "price_2023"]
    shortlist, empty = [], []
    for brand, model, tier, pattern in SLOTS:
        rx = re.compile(pattern, re.I)
        hits = [r for r in laptops if brand.lower() in (r["brand"] + " " + r["title"]).lower()
                and rx.search(r["title"])][:PER_SLOT]
        if not hits:
            empty.append(f"{brand} {model}")
        for r in hits:
            shortlist.append({
                "category": "laptop", "asin": "", "parent_asin": r["parent_asin"],
                "brand": brand, "model": model, "price_tier": tier, "review_count": "",
                "status": "shortlisted", "title": r["title"][:150],
                "rating_number": r["rating_number"], "price_2023": r["price"] or "",
            })
    with open("laptops_shortlist.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=cols)
        w.writeheader()
        w.writerows(shortlist)

    print(f"\nShortlist: {len(shortlist)} candidates across {12 - len(empty)} of 12 slots.")
    if empty:
        print("No match for:", "; ".join(empty))
    print("Wrote laptops_all.csv and laptops_shortlist.csv")


if __name__ == "__main__":
    main()