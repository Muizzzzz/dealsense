"""Find laptops from the 2023 review dataset that are still sold new on Amazon today.

For the top-rated laptops in laptops_all.csv (skipping ones already in the test set), asks
Keepa whether each is still offered. Group listings are expanded into their variants.
Costs about 1 Keepa token per ASIN checked; waits for token refills when needed.

Usage, from the Dealsense folder:
    py scan_live.py              # top 150 candidates
    py scan_live.py --top 60

Writes live_candidates.csv (every candidate, live or not) and saves raw Keepa data
to data/keepa_scan/.
"""
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend import keepa

RAW = Path("data") / "keepa_scan"
EPOCH = 21_564_000          # Keepa minutes -> minutes since 1970
BATCH = 100                 # Keepa max ASINs per request
STOP = set("laptop laptops with and the for inch in gb ram ssd display hd fhd".split())


ACCESSORY = re.compile(r"\b(adapter|charger|sleeve|case|cover|skin|protector|dock|hub|cable|"
                       r"battery|keyboard|mouse|stand|bag|replacement)\b", re.I)


def is_accessory(row):
    """Chargers etc. that slipped in via uncategorized 'Apple Products' rows, plus
    decade-old used laptops. Checks only the first 6 words: real laptop titles mention
    'Backlit Keyboard' later on."""
    head = " ".join(row["title"].split()[:6])
    return bool(ACCESSORY.search(head)) or (row["price"] and float(row["price"]) < 120)


def words(title):
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if w not in STOP}


def similarity(a, b):
    a, b = words(a), words(b)
    return round(len(a & b) / len(a | b), 2) if a and b else 0.0


def wait_for_tokens(need):
    while True:
        t = keepa.call("token")             # free
        if t["tokensLeft"] >= need:
            return
        mins = (need - t["tokensLeft"]) / max(t.get("refillRate") or 1, 1)
        print(f"  waiting for Keepa tokens ({t['tokensLeft']} left, need {need}), "
              f"~{mins:.1f} min...", flush=True)
        time.sleep(min(60 * mins + 5, 300))


def fetch(asins):
    out = []
    for i in range(0, len(asins), BATCH):
        chunk = asins[i:i + BATCH]
        wait_for_tokens(len(chunk))
        data = keepa.call("product", domain=1, asin=",".join(chunk), history=1)
        for p in data.get("products", []):
            (RAW / f"{p['asin']}.json").write_text(json.dumps(p), encoding="utf-8")
            out.append(p)
        print(f"  checked {min(i + BATCH, len(asins))}/{len(asins)} ASINs "
              f"(tokens left {data.get('tokensLeft')})", flush=True)
    return out


def status(p):
    """(live, current_price, which_series, points, history_start) from a Keepa product."""
    csv_ = p.get("csv") or []
    best = (False, None, "", 0, None)
    points = 0
    start = None
    for idx, name in ((0, "amazon"), (1, "new")):
        s = csv_[idx] if idx < len(csv_) and csv_[idx] else []
        pts = list(zip(s[0::2], s[1::2]))
        valid = [x for x in pts if x[1] >= 0]
        points += len(valid)
        if valid:
            start = min(start or valid[0][0], valid[0][0])
        if pts and pts[-1][1] >= 0 and not best[0]:
            best = (True, pts[-1][1] / 100, name, 0, None)
    day = (datetime.fromtimestamp((start + EPOCH) * 60, timezone.utc).date()
           if start else "")
    return best[0], best[1], best[2], points, day


def main():
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 150
    RAW.mkdir(parents=True, exist_ok=True)
    in_set = {r["parent_asin"] for r in csv.DictReader(open("laptops_testset.csv",
                                                            encoding="utf-8"))}
    cands = [r for r in csv.DictReader(open("laptops_all.csv", encoding="utf-8"))
             if r["parent_asin"] not in in_set and not is_accessory(r)]
    cands.sort(key=lambda r: -int(r["rating_number"]))
    seen, unique = set(), []          # same laptop often appears under several listings
    for r in cands:
        key = r["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    cands = unique[:top]
    print(f"Step 1: checking {len(cands)} dataset ASINs on Keepa")
    first = {p["asin"]: p for p in fetch([c["parent_asin"] for c in cands])}

    # group listings: no price history of their own, but a list of variants
    kids = {}
    for c in cands:
        p = first.get(c["parent_asin"]) or {}
        if not status(p)[0]:
            for v in (p.get("variations") or [])[:8]:
                if v.get("asin") and v["asin"] != c["parent_asin"]:
                    kids.setdefault(v["asin"], c["parent_asin"])
    print(f"Step 2: checking {len(kids)} variants of group listings")
    second = {p["asin"]: p for p in fetch(list(kids))} if kids else {}

    rows = []
    for c in cands:
        options = [first.get(c["parent_asin"])] + [second.get(a) for a, par in kids.items()
                                                   if par == c["parent_asin"]]
        options = [p for p in options if p]
        scored = []
        for p in options:
            live, price, series, pts, start = status(p)
            scored.append((live, similarity(c["title"], p.get("title")), pts, p, price,
                           series, start))
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        if scored:
            live, sim, pts, p, price, series, start = scored[0]
        else:
            live, sim, pts, p, price, series, start = False, 0.0, 0, {}, None, "", ""
        rows.append({
            "parent_asin": c["parent_asin"], "live_asin": p.get("asin", "") if live else "",
            "live": live, "current_price": price or "", "series": series,
            "title_similarity": sim, "price_points": pts, "history_since": start,
            "brand": c["brand"], "rating_number": c["rating_number"],
            "price_2023": c["price"], "dataset_title": c["title"][:120],
            "keepa_title": (p.get("title") or "")[:120],
        })

    with open("live_candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    live = [r for r in rows if r["live"]]
    print(f"\nDone. {len(live)} of {len(rows)} candidates are still sold new.")
    for tier, lo, hi in (("budget", 0, 500), ("mid-range", 500, 1000), ("premium", 1000, 1e9)):
        hits = [r for r in live if lo <= r["current_price"] < hi]
        print(f"\n{tier}: {len(hits)} live")
        for r in hits[:8]:
            print(f"  ${r['current_price']:>8.2f}  sim {r['title_similarity']:.2f}  "
                  f"{r['rating_number']:>5} ratings  {r['brand']:8} {r['dataset_title'][:60]}")
    print("\nWrote live_candidates.csv")


if __name__ == "__main__":
    main()
