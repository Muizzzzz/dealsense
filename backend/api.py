"""MyrmX web API (FastAPI). Also serves the web page in /web.

Run, from the Dealsense folder:
    py -m pip install fastapi uvicorn      (once)
    py -m uvicorn backend.api:app --reload
then open http://127.0.0.1:8000

Endpoints:
    GET  /api/examples              home-page examples with today's verdicts (fast, no Claude)
    GET  /api/laptops               the laptop test set with today's verdicts
    GET  /api/check/{asin}          full result for a product already in the database
    POST /api/lookup  {"query": …}  any Amazon laptop or headphones link or ASIN
                                    (1 Keepa token if new)
    GET  /api/uses                  the "What's it for?" shortcuts per category
    POST /api/ask     {"question": …, "asin": optional, "preset": optional}
                                    shopping question (Keepa search, ~10-20 tokens), or a
                                    question about one product when asin is given
Add ?writeup=false to skip the Claude write-up (faster, free).
"""
import asyncio
import hmac
import os
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import categories
from backend.db import connect
from backend.keepa import load_env
from backend.lookup import LookupError_, lookup
from backend.price_agent import analyze, daily_prices
from backend.writeup import writeup

WEB = Path(__file__).resolve().parent.parent / "web"
CHART_DAYS = 730

app = FastAPI(title="MyrmX")

load_env()
PASSCODE = os.environ.get("APP_PASSCODE", "")   # unset = no passcode (local demo)


@app.middleware("http")
async def require_passcode(request: Request, call_next):
    """Every /api/ call must carry the passcode, so strangers can't spend our API credit."""
    if PASSCODE and request.url.path.startswith("/api/"):
        given = request.headers.get("x-passcode", "")
        if not hmac.compare_digest(given.encode(), PASSCODE.encode()):
            await asyncio.sleep(1)              # slows down guessing
            return JSONResponse({"detail": "Passcode required."}, status_code=401)
    return await call_next(request)


def chart(conn, asin):
    """Daily lowest-new price for the last 2 years; null where nobody was selling it."""
    days = daily_prices(conn, asin)
    start = date.today() - timedelta(days=CHART_DAYS)
    return [{"date": str(d), "price": p} for d, p in sorted(days.items()) if d >= start]


def display_name(product):
    """Brand + model, without doubling the brand (Amazon titles usually start with it)."""
    brand, model = (product["brand"] or "").strip(), (product["model"] or "").strip()
    return model if not brand or model.lower().startswith(brand.lower()) else f"{brand} {model}"


def full_result(conn, product, with_writeup=True):
    price = analyze(conn, product)
    result = {
        "asin": product["asin"], "name": display_name(product),
        "title": product["title"], "tier": product["price_tier"], "status": product["status"],
        "category": product["category"], "noun": categories.get(product["category"])["noun"],
        "review_count": product["review_count"],
        "amazon_url": f"https://www.amazon.com/dp/{product['asin']}",
        "price": price, "chart": chart(conn, product["asin"]),
        "writeup": None, "reviews": [], "bestbuy": None, "review_counts": None,
    }
    if with_writeup:
        try:
            w = writeup(conn, product)
            result["writeup"], result["reviews"] = w.get("answer"), w.get("reviews", [])
            result["bestbuy"], result["review_counts"] = w.get("bestbuy"), w.get("review_counts")
        except SystemExit as e:              # e.g. missing API key or API error
            result["writeup"] = {"error": str(e)}
    return result


def summary_row(conn, p):
    r = analyze(conn, p)
    return {"asin": p["asin"], "name": display_name(p), "category": p["category"],
            "tier": p["price_tier"], "status": p["status"], "verdict": r["verdict"],
            "current_price": r.get("current_price"), "median": r.get("median"),
            "review_count": p["review_count"]}


@app.get("/api/laptops")
def laptops():
    conn = connect()
    return [summary_row(conn, p) for p in conn.execute(
        "SELECT * FROM products WHERE status IN ('live', 'sold_out') "
        "ORDER BY CASE price_tier WHEN 'budget' THEN 0 WHEN 'mid-range' "
        "THEN 1 ELSE 2 END, model")]


@app.get("/api/uses")
def uses():
    return categories.all_uses()


@app.get("/api/examples")
def examples():
    """Home-page examples per category, skipping any not yet in the database."""
    conn = connect()
    out = []
    for key, asins in categories.FEATURED.items():
        rows = [conn.execute("SELECT * FROM products WHERE asin = ?", (a,)).fetchone()
                for a in asins]
        items = [summary_row(conn, p) for p in rows if p]
        if items:
            out.append({"category": key, "label": categories.get(key)["plural"].capitalize(),
                        "items": items})
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


class Ask(BaseModel):
    question: str = ""
    asin: str | None = None
    preset: str | None = None        # "laptop:designer": a "What's it for?" shortcut


@app.post("/api/ask")
def ask_route(q: Ask):
    from backend.shop import ask_product, ask_shopping
    if q.preset:
        key, plan, label = categories.use_plan(q.preset)
        if not plan:
            raise HTTPException(400, "Unknown shortcut.")
        question = f"The best {categories.get(key)['plural']} for: {label.lower()}"
        try:
            return ask_shopping(connect(), question, preset_plan=plan, picks=5)
        except SystemExit as e:
            raise HTTPException(502, str(e))
    question = q.question.strip()
    if len(question) < 3:
        raise HTTPException(400, "Type a shopping question, e.g. "
                                 "\"a light laptop for college under $700\".")
    conn = connect()
    try:
        if q.asin:
            return ask_product(conn, q.asin.upper(), question)
        return ask_shopping(conn, question)
    except SystemExit as e:                  # Keepa or Claude errors
        raise HTTPException(502, str(e))


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/web", StaticFiles(directory=WEB), name="web")
