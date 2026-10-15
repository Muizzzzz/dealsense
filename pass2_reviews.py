"""DealSense - Step 3, Pass 2: pull reviews for the shortlisted laptops.

Streams Electronics.jsonl (22.6 GB of reviews) line by line and keeps only reviews whose
parent_asin is in laptops_shortlist.csv. Standard library only.

Usage (run in the folder that has laptops_shortlist.csv):
    python pass2_reviews.py                        # stream from Hugging Face
    python pass2_reviews.py --max-lines 2000000    # quick test on the first 2M reviews
    python pass2_reviews.py --input laptops_testset.csv   # final 12 -> reviews_testset.jsonl

Outputs:
    reviews_shortlist.jsonl        every review for the 24 candidates (the RAG corpus; saved so
                                   we never have to stream the 22 GB file again)
    laptops_shortlist_pass2.csv    the shortlist with asin and review_count filled in
"""
import csv
import io
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

URL = ("https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/"
       "raw/review_categories/Electronics.jsonl")
KEY = '"parent_asin": "'
KEEP = ("rating", "title", "text", "asin", "parent_asin", "timestamp", "helpful_vote",
        "verified_purchase")


def main():
    args = sys.argv[1:]
    max_lines = int(args[args.index("--max-lines") + 1]) if "--max-lines" in args else None
    in_csv = args[args.index("--input") + 1] if "--input" in args else "laptops_shortlist.csv"
    stem = in_csv.rsplit(".", 1)[0].replace("laptops_", "")
    source = next((a for a in args if not a.startswith("--") and not a.isdigit()
                   and a != in_csv), URL)

    shortlist = list(csv.DictReader(open(in_csv, encoding="utf-8")))
    wanted = {r["parent_asin"] for r in shortlist}
    print(f"Looking for reviews of {len(wanted)} products")

    text_count = Counter()              # reviews with real text, per parent
    child_asins = defaultdict(Counter)  # buyable ASINs seen under each parent
    newest = {}
    kept = 0

    if source.startswith("http"):
        f = io.TextIOWrapper(urllib.request.urlopen(source), encoding="utf-8")
    else:
        f = open(source, encoding="utf-8")
    with f, open(f"reviews_{stem}.jsonl", "w", encoding="utf-8") as out:
        for n, line in enumerate(f, 1):
            if max_lines and n > max_lines:
                break
            if n % 2_000_000 == 0:
                print(f"  scanned {n:,} reviews, kept {kept:,}", flush=True)
            # cheap check before parsing JSON: read parent_asin straight from the line
            i = line.find(KEY)
            if i < 0 or line[i + len(KEY):line.find('"', i + len(KEY))] not in wanted:
                continue
            r = json.loads(line)
            p = r["parent_asin"]
            out.write(json.dumps({k: r.get(k) for k in KEEP}) + "\n")
            kept += 1
            child_asins[p][r["asin"]] += 1
            if (r.get("text") or "").strip():
                text_count[p] += 1
            newest[p] = max(newest.get(p, 0), r.get("timestamp") or 0)

    for row in shortlist:
        p = row["parent_asin"]
        top = child_asins[p].most_common(1)
        # keep an ASIN that's already set (e.g. the live one from scan_live.py);
        # otherwise use the variant with the most reviews
        row["asin"] = row.get("asin") or (top[0][0] if top else "")
        row["review_count"] = text_count[p]
        row["n_child_asins"] = len(child_asins[p])
        row["newest_review"] = (datetime.fromtimestamp(newest[p] / 1000, timezone.utc).date()
                                if p in newest else "")
    cols = list(shortlist[0].keys())
    with open(f"laptops_{stem}_pass2.csv", "w", newline="", encoding="utf-8") as o:
        w = csv.DictWriter(o, fieldnames=cols)
        w.writeheader()
        w.writerows(shortlist)

    print(f"\nDone. Kept {kept:,} reviews.")
    for row in sorted(shortlist, key=lambda r: -r["review_count"]):
        print(f"  {row['review_count']:>6} reviews  {row['asin'] or '(none)':12} {row['brand']:9} {row['title'][:55]}")
    print(f"Wrote reviews_{stem}.jsonl and laptops_{stem}_pass2.csv")


if __name__ == "__main__":
    main()
