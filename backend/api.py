"""DealSense web API (FastAPI). Also serves the web page in /web.

Run, from the Dealsense folder:
    py -m pip install fastapi uvicorn      (once)
    py -m uvicorn backend.api:app --reload
then open http://127.0.0.1:8000

Endpoints:
    GET  /api/laptops               the test set with today's verdicts (fast, no Claude)
    GET  /api/check/{asin}          full result for a laptop already in the database
    POST /api/lookup  {"query": …}  any Amazon laptop link or ASIN (1 Keepa token if new)
Add ?writeup=false to skip the Claude write-up (faster, free).
"""
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.db import connect
from backend.lookup import LookupError_, lookup
from backend.price_agent import analyze, daily_prices
from backend.writeup import writeup

WEB = Path(__file__).resolve().parent.parent / "web"
CHART_DAYS = 730

app = FastAPI(title="DealSense")


def chart(conn, asin):
    """Daily lowest-new price for the last 2 years; null where nobody was selling it."""
    days = daily_prices(conn, asin)
    start = date.today() - timedelta(days=CHART_DAYS)
    return [{"date": str(d), "price": p} for d, p in sorted(days.items()) if d >= start]


def full_result(conn, product, with_writeup=True):
    price = analyze(conn, product)
    result = {
        "asin": product["asin"], "name": f"{product['brand']} {product['model']}".strip(),
        "title": product["title"], "tier": product["price_tier"], "status": product["status"],
        "review_count": product["review_count"],
        "amazon_url": f"https://www.amazon.com/dp/{product['asin']}",
        "price": price, "chart": chart(conn, product["asin"]),
        "writeup": None, "reviews": [],
    }
    if with_writeup:
        try:
            w = writeup(conn, product)
            result["writeup"], result["reviews"] = w.get("answer"), w.get("reviews", [])
        except SystemExit as e:              # e.g. missing API key or API error
            result["writeup"] = {"error": str(e)}
    return result


@app.get("/api/laptops")
def laptops():
    conn = connect()
    out = []
    for p in conn.execute("SELECT * FROM products WHERE status IN ('live', 'sold_out') "
                          "ORDER BY CASE price_tier WHEN 'budget' THEN 0 WHEN 'mid-range' "
                          "THEN 1 ELSE 2 END, model"):
        r = analyze(conn, p)
        out.append({"asin": p["asin"], "name": f"{p['brand']} {p['model']}", "tier":
                    p["price_tier"], "status": p["status"], "verdict": r["verdict"],
                    "current_price": r.get("current_price"), "median": r.get("median"),
                    "review_count": p["review_count"]})
    return out


@app.get("/api/check/{asin}")
def check(asin: str, writeup: bool = True):
    conn = connect()
    product = conn.execute("SELECT * FROM products WHERE asin = ?", (asin.upper(),)).fetchone()
    if not product:
        raise HTTPException(404, "Not in the database yet. Use /api/lookup with a link.")
    return full_result(conn, product, writeup)


class Query(BaseModel):
    query: str


@app.post("/api/lookup")
def lookup_route(q: Query, writeup: bool = True):
    conn = connect()
    try:
        product = lookup(conn, q.query)
    except LookupError_ as e:
        raise HTTPException(400, str(e))
    except SystemExit as e:                  # Keepa errors (bad key, out of tokens)
        raise HTTPException(502, str(e))
    return full_result(conn, product, writeup)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/web", StaticFiles(directory=WEB), name="web")
