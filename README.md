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
| SQLite database and review loader | Done |
| Review search (BM25 keyword ranking) | Done |
| Price history (Keepa API) | Waiting on API key |
| Claude-written verdict | In progress |
| Web API and demo page | In progress |
| Embedding search, compared against BM25 | Planned |
| Live product links, more categories | Planned |

## Data

Review text and product metadata come from [Amazon Reviews 2023](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023) (McAuley Lab, UCSD), Electronics category. Price history will come from the [Keepa API](https://keepa.com).

The raw files are large (5.2 GB of metadata, 22.6 GB of reviews), so the pipeline streams them instead of downloading them. Generated data files are not committed; the scripts below recreate them.

### Test set

12 laptops, 4 per price tier, across 7 brands. It is fixed so that every part of the system is built and evaluated on the same products.

| Tier | Laptops |
|---|---|
| Budget | Acer Aspire 5, HP 15, Lenovo IdeaPad 3 15.6", ASUS VivoBook 15 |
| Mid-range | MacBook Air M1, Dell Inspiron 15 5000, Lenovo Flex 5 14, HP Envy x360 15 |
| Premium | MacBook Air M2 13", ASUS ROG Zephyrus G14, Razer Blade 15, Acer Predator Helios 300 |

Tiers are provisional until current Keepa prices confirm them. The list, with ASINs, is in `laptops_testset.csv`.

## Setup

Requires Python 3.10 or newer. The current code uses only the standard library.

```
git clone https://github.com/Muizzzzz/dealsense.git
cd dealsense
```

## Pipeline

Run these from the project folder, in order.

1. **Shortlist laptops (Pass 1).** Streams the Electronics metadata, keeps laptops with 50+ ratings, and picks 2 candidates per test-set slot.
   ```
   python shortlist.py
   ```
   Writes `laptops_all.csv` and `laptops_shortlist.csv`.

2. **Pull reviews (Pass 2).** Streams the Electronics reviews and keeps those for the test set. Fills in each product's buyable ASIN and review count.
   ```
   python pass2_reviews.py --input laptops_testset.csv
   ```
   Writes `reviews_testset.jsonl` and `laptops_testset_pass2.csv`.

3. **Build the database.** Loads products and cleaned reviews into `dealsense.db`.
   ```
   python -m backend.load_data
   ```

4. **Search reviews.** Returns the reviews most relevant to a question about one laptop. Accepts an ASIN or part of the model name.
   ```
   python -m backend.search "MacBook Air M1" "battery life"
   python -m backend.search B0869L1326 "screen brightness" -k 3
   ```

## Project structure

```
shortlist.py          Pass 1: find laptop candidates in the metadata
pass2_reviews.py      Pass 2: pull reviews and ASINs for a product list
laptops_testset.csv   the 12-laptop test set
backend/
  db.py               SQLite schema: products, reviews, price_history
  load_data.py        loads the pipeline output into dealsense.db
  search.py           BM25 review search
```

## Evaluation plan

- **Price model:** backtest against periods with known real sales (such as holiday price drops) and against listings flagged as inflated discounts.
- **Retrieval:** measure precision@k on a set of labelled queries, comparing embedding search with the BM25 keyword baseline.
