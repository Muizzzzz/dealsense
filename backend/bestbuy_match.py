"""Find the Best Buy SKU for an Amazon laptop, so we can show its live Best Buy reviews.

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


def search_query(title):
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


def choose(title, brand, results):
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
    q = search_query(title)
    results = parse_results(get_text(SEARCH.format(q=urllib.parse.quote(q))))
    match, notes = choose(title, brand, results)
    data = {"asin": product["asin"], "query": q, "match": match, "notes": notes}
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    from backend.lookup import fetch_product
    p = fetch_product(args[0].upper())
    d = find_sku(p, refresh="--refresh" in sys.argv)
    print(f"Amazon: {p.get('title', '')[:110]}")
    print(f"Specs:  {specs(p.get('title', ''))}   family: {sorted(family(p.get('title', ''), p.get('brand')))}")
    print(f"Search: {d['query']!r}\n")
    for n in d["notes"]:
        print("  " + n)
    m = d["match"]
    print(f"\nMatch: {m['sku']} ({m['review_count']} reviews, {m['rating']} stars) {m['name']}"
          if m else "\nMatch: none")
