"""Find the Best Buy SKU for an Amazon laptop or pair of headphones, so we can show its live
Best Buy reviews.

Headphones have no CPU or RAM to compare; their identity is the model name ("WH-1000XM5",
"AirPods Pro 2"), so for them every model word from the Amazon title must appear in the
Best Buy name, and the Best Buy product must itself be headphones (not a case or ear tips).

Amazon listings often have no model number, and resellers' bundles carry their own barcodes,
so we search Best Buy by name (brand + model family + CPU + GPU) and then check specs:
a candidate is rejected if any spec that both titles state disagrees (CPU, GPU, RAM,
storage). Among the rest we prefer the closest model-family match, then the most reviews.
If nothing passes, there is no match: better no Best Buy reviews than the wrong laptop's.

Usage, from the Dealsense folder:
    py -m backend.bestbuy_match B0GY819DP2            (1 Keepa token if not cached)
    py -m backend.bestbuy_match B0GY819DP2 --refresh
"""
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "reviews_live"
CACHE_DAYS = 30
SEARCH = "https://www.bestbuy.com/site/searchpage.jsp?st={q}"
GENERIC = set("""gaming laptop notebook computer pc touchscreen touch screen thin light
inch with and the for new latest copilot plus oled ips fhd full hd qhd uhd display 2-in-1
x360 convertible business student home in 2 1 inch""".split())
HP_GENERIC = set("""wireless bluetooth noise cancelling canceling cancellation active anc hybrid
headphones headphone earbuds earbud earphones earphone in ear over on true with and for best
sports gaming headset mic microphone long battery life hours hour playtime waterproof deep bass
hi res audio sound premium comfortable lightweight foldable kids open stereo the new ai""".split())


def get_text(url):
    from curl_cffi import requests as creq
    r = creq.get(url, impersonate="chrome", timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Best Buy search returned {r.status_code}")
    return r.text


# ------------------------------------------------------------------ specs from titles

def specs(title):
    t = (title or "").lower().replace("‑", "-")
    out = {}
    m = re.search(r"\b[rg]tx\s?-?(\d{4})", t)
    if m:
        out["gpu"] = m.group(1)
    cpu = (re.search(r"ryzen\s+(?:ai\s+)?\d\s+(?:pro\s+)?(?:hx\s+)?(\d{3,4}[a-z]{0,3})\b", t)
           or re.search(r"(?:i[3579]|ultra\s*[3579]|core\s+[3579])[\s-]+(\d{3,5}[a-z]{0,2}\d?)\b", t)
           or re.search(r"\b(m[1-5](?:\s+(?:pro|max))?)\b", t)
           or re.search(r"(snapdragon\s+x\d?\s*(?:elite|plus)?)", t))
    if cpu:
        out["cpu"] = re.sub(r"\s+", " ", cpu.group(1))
    m = re.search(r"\b(\d{1,3})\s?gb\s*(?:of\s+)?(?:ram|memory|unified|lpddr\w*|ddr\w*)", t)
    if m:
        out["ram"] = int(m.group(1))
    m = (re.search(r"\b(\d{1,2})\s?tb\b", t))
    if m:
        out["storage"] = int(m.group(1)) * 1000
    else:
        m = re.search(r"\b(\d{3,4})\s?gb\s*(?:ssd|storage|pcie|nvme|emmc|ufs)", t)
        if m:
            out["storage"] = int(m.group(1))
    return out


def family(title, brand):
    """Model-family words: the title's head (before the first comma, or the second
    ' - ' segment of a Best Buy name), without the brand, sizes and generic words."""
    head = title.split(" - ")[1] if " - " in title else re.split(r",|\|", title)[0]
    words = re.findall(r"[a-z0-9]+", head.lower())
    drop = GENERIC | set((brand or "").lower().split())
    # keep one-digit model numbers ("Swift 3", "Aspire 5"); drop sizes like 14, 120hz, 16gb
    return {w for w in words if w not in drop and not re.fullmatch(r"\d{2,}|\d+(hz|k|gb|tb)", w)}


def model_words(title, brand):
    """Headphone model words: the run of words after the brand, up to the first generic
    word, plus the generation number. 'Sony WH-1000XM5 The Best Wireless ...' ->
    ['WH-1000XM5']; 'Apple AirPods Pro (2nd Generation) ...' -> ['AirPods', 'Pro', '2'].
    Returns [] when the title has no real model name (no-name 'Wireless Earbuds 5.3')."""
    head = re.split(r",|\||\s-\s|:", title)[0]
    gen = re.search(r"\b(\d)(?:st|nd|rd|th)\s+gen", head, re.I)
    head = re.sub(r"\(?\b\d(?:st|nd|rd|th)\s+gen(?:eration)?\)?", " ", head, flags=re.I)
    head = re.sub(r"\bby\s+\w+", " ", head, flags=re.I)        # "Soundcore by Anker"
    head = head.split("(")[0]
    brand_words = set(re.findall(r"[a-z0-9]+", (brand or "").lower()))
    out = []
    for w in head.split():
        bare = re.sub(r"[^a-z0-9.]", "", w.lower())
        if not bare or bare in brand_words or re.fullmatch(r"20\d\d", bare):
            continue
        if bare in HP_GENERIC:
            if out:
                break
            continue
        out.append(w.strip(",.;:"))
        if len(out) == 4:
            break
    if gen and gen.group(1) not in out:
        out.append(gen.group(1))
    return out if any(re.search(r"[a-z]", w, re.I) for w in out) else []


def form(text):
    """'earbuds' or 'headphones' (over/on-ear), or None if the text doesn't say."""
    t = text.lower()
    if re.search(r"\b(earbuds?|ear ?buds|in-ear|airpods|earphones?|buds\d?)\b", t):
        return "earbuds"
    if re.search(r"\b(headphones?|over-(the-)?ear|on-ear)\b", t):
        return "headphones"
    return None


def squash(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def search_query(title, brand="", category="laptop"):
    if category == "headphones":
        return " ".join([brand] + model_words(title, brand)).strip()
    head = re.split(r",|\|", title)[0]
    head = re.sub(r'\d+(\.\d+)?"|\b\d+\s?Hz\b|\b\d+K\b|OLED|Gaming|Laptop|Notebook|'
                  r'Computer|\bPC\b', " ", head, flags=re.I)
    s = specs(title)
    extra = [f"RTX {s['gpu']}"] if "gpu" in s else []
    cpu = re.search(r"(Ryzen \w+ \w+|Core (Ultra )?\w+ \w+|\bM\d( Pro| Max)?\b)", title, re.I)
    words = " ".join([head] + ([cpu.group(0)] if cpu else []) + extra)
    return re.sub(r"\s+", " ", words).strip()


# ------------------------------------------------------------------ Best Buy results

def parse_results(html):
    """Search-result products: sku, name, rating, review count, condition, url."""
    h = html.replace('\\"', '"')
    # each product's data starts at "__typename":"Product","skuId":...; read each chunk alone
    starts = [m.start() for m in re.finditer(r'"__typename":"Product","skuId":"\d{7,8}"', h)]
    out = []
    for i, s in enumerate(starts):
        chunk = h[s:starts[i + 1] if i + 1 < len(starts) else s + 30000]

        def field(p):
            m = re.search(p, chunk, re.S)
            return m.group(1) if m else None

        sku, name = field(r'"skuId":"(\d{7,8})"'), field(r'"name":\{"short":"(.*?)","__typename"')
        if not name or any(r["sku"] == sku for r in out):
            continue                                   # variant entries carry no name
        rating = field(r'"averageRating":([\d.]+)')
        out.append({"sku": sku, "name": name,
                    "condition": field(r'"condition":\{"type":"([^"]+)"') or "new",
                    "url": field(r'"url":\{"pdp":"([^"]+)"'),
                    "rating": float(rating) if rating else None,
                    "review_count": int(field(r'"reviewCount":(\d+)') or 0)})
    return out


def headphone_problems(title, brand, name):
    """Reasons a Best Buy name is not the same headphones as the Amazon title."""
    from backend.categories import CATEGORIES
    c, why = CATEGORIES["headphones"], []
    want = model_words(title, brand)
    if not want:
        return ["no model name in the Amazon title"]
    bb_head = name.split(" - ", 1)[-1]
    got = model_words(bb_head, brand)
    if {squash(w) for w in want} != {squash(w) for w in got}:
        why.append(f"model {' '.join(got) or '?'} vs {' '.join(want)}")
    f_want, f_got = form(title), form(bb_head)
    if f_want and f_got and f_want != f_got:
        why.append(f"{f_got} vs {f_want}")
    head = bb_head[:80]
    acc, thing = c["not_title"].search(head), c["title"].search(head)
    if not thing or (acc and acc.start() < thing.start()):
        why.append("not headphones")
    return why


def choose(title, brand, results, category="laptop"):
    """Best matching candidate and the reasons, or (None, reasons)."""
    want, fam = specs(title), family(title, brand)
    best, notes = None, []
    for r in results:
        name = r["name"]
        why = []
        if brand and not name.lower().startswith(brand.lower()):
            why.append("brand")
        if r["condition"] != "new":
            why.append(r["condition"])
        if category == "headphones":
            why += headphone_problems(title, brand, name)
            notes.append(f"{r['sku']} {'OK' if not why else 'no: ' + ', '.join(why)} "
                         f"({r['review_count']} reviews) {name[:80]}")
            if not why and (best is None or r["review_count"] > best[0][1]):
                best = ((1.0, r["review_count"]), r)
            continue
        got = specs(name)
        for k in ("cpu", "gpu", "ram", "storage"):
            if k in want and k in got and want[k] != got[k]:
                why.append(f"{k} {got[k]} vs {want[k]}")
        if ("gpu" in want) != ("gpu" in got):
            why.append("gpu missing on one side")
        # every family word must appear in the Best Buy name (60% if the family is long)
        overlap = len(fam & set(re.findall(r"[a-z0-9]+", name.lower()))) / max(1, len(fam))
        if overlap < (1.0 if len(fam) <= 3 else 0.6):
            why.append(f"family overlap {overlap:.0%}")
        notes.append(f"{r['sku']} {'OK' if not why else 'no: ' + ', '.join(why)} "
                     f"({r['review_count']} reviews) {name[:80]}")
        if not why:
            key = (overlap, r["review_count"])
            if best is None or key > best[0]:
                best = (key, r)
    return (best[1] if best else None), notes


def find_sku(product, refresh=False):
    """Best Buy match for a Keepa product (cached per ASIN), or None."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"match_{product['asin']}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < CACHE_DAYS * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    title, brand = product.get("title") or "", product.get("brand") or ""
    category = product.get("category") or "laptop"
    q = search_query(title, brand, category)
    if not q.strip() or q.strip() == brand:
        return {"asin": product["asin"], "query": q, "match": None,
                "notes": ["no model name to search for"]}
    results = parse_results(get_text(SEARCH.format(q=urllib.parse.quote(q))))
    match, notes = choose(title, brand, results, category)
    data = {"asin": product["asin"], "query": q, "match": match, "notes": notes}
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    from backend.lookup import fetch_product
    from backend.categories import category_of
    p = fetch_product(args[0].upper())
    p["category"] = category_of(p)
    d = find_sku(p, refresh="--refresh" in sys.argv)
    print(f"Amazon: {p.get('title', '')[:110]}")
    if p["category"] == "headphones":
        print(f"Model words: {model_words(p.get('title', ''), p.get('brand'))}")
    else:
        print(f"Specs:  {specs(p.get('title', ''))}   "
              f"family: {sorted(family(p.get('title', ''), p.get('brand')))}")
    print(f"Search: {d['query']!r}\n")
    for n in d["notes"]:
        print("  " + n)
    m = d["match"]
    print(f"\nMatch: {m['sku']} ({m['review_count']} reviews, {m['rating']} stars) {m['name']}"
          if m else "\nMatch: none")
