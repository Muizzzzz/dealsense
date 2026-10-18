"""Database layer: one SQLite file (dealsense.db) holding products, reviews, and price history."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "dealsense.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    parent_asin   TEXT NOT NULL,      -- groups all variants; reviews are keyed by this
    asin          TEXT PRIMARY KEY,   -- buyable variant; what Keepa looks up
    category      TEXT NOT NULL,      -- 'laptop' now; headphones/tvs later
    brand         TEXT,
    model         TEXT,
    price_tier    TEXT,               -- provisional until Keepa prices confirm it
    title         TEXT,
    review_count  INTEGER,
    status        TEXT                -- live / sold_out (test set) or lookup (pasted link)
);
CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY,
    parent_asin   TEXT NOT NULL,
    asin          TEXT,
    rating        REAL,
    title         TEXT,
    text          TEXT,
    timestamp     INTEGER,            -- milliseconds since 1970
    helpful_vote  INTEGER,
    verified      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reviews_parent ON reviews(parent_asin);
CREATE INDEX IF NOT EXISTS idx_reviews_asin ON reviews(asin);
CREATE TABLE IF NOT EXISTS price_history (
    asin          TEXT NOT NULL,
    ts            INTEGER NOT NULL,   -- milliseconds since 1970
    price         REAL,               -- USD; NULL = nobody selling it new at that time
    source        TEXT DEFAULT 'keepa',
    PRIMARY KEY (asin, ts, source)
);
"""


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
