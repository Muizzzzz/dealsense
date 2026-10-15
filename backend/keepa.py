"""Keepa API access. The key is read from .env (KEEPA_API_KEY=...), never hard-coded.

Check that the key works (costs 0 tokens):
    python -m backend.keepa check
"""
import gzip
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.keepa.com"


def load_env(path=ROOT / ".env"):
    """Minimal .env reader: KEY=value lines; ignores blanks, comments, quotes, spaces."""
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key():
    load_env()
    key = os.environ.get("KEEPA_API_KEY", "")
    if not key:
        sys.exit("KEEPA_API_KEY not found. Add a line KEEPA_API_KEY=your-key to .env "
                 f"in {ROOT}")
    return key


def call(endpoint, **params):
    """GET a Keepa endpoint and return parsed JSON. Keepa gzips its responses."""
    params["key"] = api_key()
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    data = json.loads(body)
    if "error" in data:
        sys.exit(f"Keepa error: {data['error']}")
    return data


def check():
    key = api_key()
    print(f"Key found in .env ({len(key)} characters).")
    t = call("token")
    print(f"Keepa accepted it. Tokens available now: {t.get('tokensLeft')}, "
          f"refill rate: {t.get('refillRate')} per minute.")


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        check()
    else:
        print(__doc__)
