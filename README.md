# DealSense

DealSense tells online shoppers whether a product's current price is actually a good deal. It combines two agents:

- **Price agent:** compares today's price with the product's price history and labels it a genuine low, a typical price, or an inflated "discount".
- **Research agent:** searches real customer reviews for the product and summarizes what owners report, with citations.

A coordinating step combines both into one short answer, for example: *"20% below its 90-day average; reviewers flag battery life as a weak point."*

The pilot covers laptops. The data model is category-based, so headphones and TVs can be added later.

DealSense is a CMSC 698 senior capstone (Computer Science and Data Science) at Lawrence University, and the basis of an I-E 200 venture pitch.

## Status

| Part | Status |
|---|---|
| Data pipeline (laptop shortlist and reviews from Amazon Reviews 2023) | Done |
| Price history (Keepa API) | Done |
| Price agent: genuine low / typical / inflated, with reasons | Done |
| Backtest over 3 years of history | Done |
| Review search (BM25 keyword ranking) | Done |
| Claude write-up with citations | Done |
| Paste any Amazon laptop link | Done |
| Web app (FastAPI + one-page front end) | Done |
| Embedding search, compared against BM25 | Planned |
| Live review text for laptops newer than 2023 | Planned |

## Data

Review text and product metadata come from [Amazon Reviews 2023](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023) (McAuley Lab, UCSD), Electronics category. Price history will come from the [Keepa API](https://keepa.com).

The raw files are large (5.2 GB of metadata, 22.6 GB of reviews), so the pipeline streams them instead of downloading them. Generated data files are not committed; the scripts below recreate them.

### Test set

12 laptops still sold new on Amazon, 4 per price tier, across 7 brands. It is fixed so that every part of the system is built and evaluated on the same products.

| Tier | Laptops |
|---|---|
| Budget | Acer Aspire 5, HP 15-dy1036nr, Lenovo IdeaPad 3 15.6", ASUS VivoBook 15 (i3) |
| Mid-range | MacBook Air M1, Dell Inspiron 15 5000, Lenovo Flex 5 14, Acer Swift 3 |
| Premium | ASUS ROG Zephyrus G14, MacBook Pro 16" M1 Pro, Razer Blade 15 Advanced, Acer Predator Helios 300 17.3" |

Three more laptops that are no longer sold new (MacBook Air M2, Razer Blade 15 Base, Predator
Helios 300 15.6") are kept only for testing the price model on their real sale history.

The list, with ASINs, is in `laptops_testset.csv`.

## Setup

Requires Python 3.10 or newer. The pipeline and price agent use only the standard library;
the web app needs FastAPI.

```
py -m pip install -r requirements.txt
```

API keys go in a `.env` file in the project folder (never committed):

```
KEEPA_API_KEY=...
ANTHROPIC_API_KEY=...
```

```
git clone https://github.com/Muizzzzz/Dealsense.git
cd Dealsense
```

## Pipeline

Run these from the project folder, in order.

1. **Shortlist laptops (Pass 1).** Streams the Electronics metadata, keeps laptops with 50+ ratings, and picks 2 candidates per test-set slot.
   ```
   py shortlist.py
   ```
   Writes `laptops_all.csv` and `laptops_shortlist.csv`.

2. **Pull reviews (Pass 2).** Streams the Electronics reviews and keeps those for the test set. Fills in each product's buyable ASIN and review count.
   ```
   py pass2_reviews.py --input laptops_testset.csv
   py pass2_reviews.py --input laptops_all.csv     # optional: reviews for ~4,100 laptops
   ```
   Writes `reviews_testset.jsonl` and `laptops_testset_pass2.csv`.

3. **Build the database.** Loads products and cleaned reviews into `dealsense.db`.
   ```
   py -m backend.load_data
   ```

4. **Search reviews.** Returns the reviews most relevant to a question about one laptop. Accepts an ASIN or part of the model name.
   ```
   py -m backend.search "MacBook Air M1" "battery life"
   py -m backend.search B0869L1326 "screen brightness" -k 3
   ```

5. **Fetch price history.** One Keepa token per laptop.
   ```
   py -m backend.prices fetch
   ```

6. **Price verdicts and backtest.**
   ```
   py -m backend.price_agent            # every test-set laptop
   py -m backend.price_agent "Swift"    # one laptop, with reasons
   py -m backend.backtest               # replay 3 years of history
   ```

7. **Run the web app.**
   ```
   py -m uvicorn backend.api:app --reload
   ```
   Then open http://127.0.0.1:8000 and paste an Amazon laptop link, or pick a test laptop.

## How the verdict works

The price agent turns Keepa's price-change log into a daily price, then compares today's price
with the last 180 days and the last 2 years, using medians and percentiles so short glitches
don't matter. A **genuine low** must be in the cheapest 20% of recent days, at least 5% below the
median, and a real dip below the last 30 days. **Inflated** means well above the recent range,
or more than 25% above the 2-year median. Claude never decides the verdict; it only explains it,
citing the price facts and reviews it was given.

**Backtest (Oct 2023 to Oct 2026, about 15,000 laptop-days):** buying on a "genuine low" day was
7.0% cheaper than the median price of the following 30 days; buying on an "inflated" day was 1.3%
more expensive. A "fake discount" rule (a drop from a recent high that stays above normal) did
the opposite of what its name claims (buying on those days saved 12.6%), so it was removed from
the product and is kept only in the backtest.

## Project structure

```
shortlist.py          Pass 1: find laptop candidates in the metadata
pass2_reviews.py      Pass 2: pull reviews and ASINs for a product list
laptops_testset.csv   the 12-laptop test set
scan_live.py          finds dataset laptops still sold new (used to pick the test set)
backend/
  db.py               SQLite schema: products, reviews, price_history
  load_data.py        loads the pipeline output into dealsense.db
  keepa.py            Keepa API access
  prices.py           price history from Keepa into the database
  price_agent.py      the price verdict
  backtest.py         replays the price agent over past dates
  search.py           BM25 review search
  writeup.py          Claude write-up from the verdict and reviews
  lookup.py           paste-a-link: any Amazon laptop by link or ASIN
  api.py              FastAPI web API; serves web/index.html
web/
  index.html          the web page
```

## Evaluation plan

- **Price model:** backtest against periods with known real sales (such as holiday price drops) and against listings flagged as inflated discounts.
- **Retrieval:** measure precision@k on a set of labelled queries, comparing embedding search with the BM25 keyword baseline.
