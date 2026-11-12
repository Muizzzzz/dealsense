"""Probe: can we read Newegg search results and reviews? (Temporary, like the Best Buy probes.)

Saves what Newegg returns so we can see its structure:
    1. a keyword search page
    2. the first laptop's product page (reviews may be embedded or loaded separately)

Usage, from the Dealsense folder:
    py -m backend.newegg_probe "ASUS ROG Zephyrus G14 Ryzen 9 270 RTX 5060"
"""
import re
import sys
import time
import urllib.parse
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "reviews_live"


def get(url):
    from curl_cffi import requests as creq
    started = time.time()
    try:
        r = creq.get(url, impersonate="chrome", timeout=30)
        return r.status_code, r.text, time.time() - started
    except Exception as e:
        return type(e).__name__, str(e), time.time() - started


def save(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / name
    f.write_text(text, encoding="utf-8")
    return f.name


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    q = sys.argv[1]
    status, html, secs = get("https://www.newegg.com/p/pl?d=" + urllib.parse.quote(q))
    print(f"Search: status {status}, {len(html):,} chars in {secs:.1f}s "
          f"-> {save('newegg_search.html', html)}")
    if "cf-chl" in html or "Just a moment" in html:
        print("  Looks like a Cloudflare challenge page.")
    links = list(dict.fromkeys(re.findall(r'https://www\.newegg\.com/[^"\s]*?/p/[A-Za-z0-9-]+', html)))
    items = list(dict.fromkeys(re.findall(r"\b(N82E\d{11}|9SIA[A-Z0-9]{10,})\b", html)))
    print(f"  Item numbers seen: {items[:8]}")
    for link in links[:5]:
        print(f"  link: {link[:120]}")
    if links:
        status, page, secs = get(links[0])
        print(f"\nFirst product page: status {status}, {len(page):,} chars in {secs:.1f}s "
              f"-> {save('newegg_product.html', page)}")
        for key in ("ReviewSummary", "reviewCount", "ProductReview", "Reviews", "ratingValue"):
            print(f"  '{key}' appears {page.count(key)} times")
