"""Product categories Myrmech Explorer covers, and everything that differs between them.

Adding a category means adding an entry here: how to recognise it from Keepa's data,
which topics to pull reviews for, its price tiers, and the words the app uses for it.
The price verdict, live Best Buy reviews and the web page work the same for every category.
"""
import re

CATEGORIES = {
    "laptop": {
        "noun": "laptop", "plural": "laptops", "short": "laptops",
        # Keepa category tree names (any match), unless the tree also has an accessories node
        "tree": re.compile(r"\bLaptops?\b"),
        "parents": {"Computers & Accessories"},     # tree names with "Accessories" that are fine
        "title": re.compile(r"\b(laptop|notebook|macbook|chromebook|ultrabook)\b", re.I),
        "not_title": re.compile(r"\b(case|sleeve|bag|backpack|stand|charger|adapter|skin|"
                                r"keyboard cover|screen protector|dock|docking)\b", re.I),
        "aspects": ["battery life", "performance speed", "screen display", "build quality",
                    "keyboard trackpad", "heat fan noise",
                    "problem defect stopped working return"],
        "tiers": (500, 1000),
        "examples": ["A laptop with an RTX 50-series card around $800",
                     "A light laptop for college under $600"],
    },
    "headphones": {
        "noun": "headphones", "plural": "headphones and earbuds", "short": "headphones",
        "tree": re.compile(r"^(Headphones|Headphones & Earbuds|Earbud Headphones|"
                           r"Over-Ear Headphones|On-Ear Headphones|Earbuds)$"),
        "parents": {"Headphones, Earbuds & Accessories"},
        "title": re.compile(r"\b(headphones?|earbuds?|earphones?|ear ?buds|in-ear|over-ear|"
                            r"on-ear|airpods|headset)\b", re.I),
        "not_title": re.compile(r"\b(ear ?tips?|replacement|case|cover|stand|hanger|holder|"
                                r"cable|adapter|cushions?|ear ?pads?|hook|charger|"
                                r"cleaning)\b", re.I),
        "aspects": ["sound quality bass", "noise cancelling", "comfort fit",
                    "battery life", "microphone calls", "bluetooth connection app",
                    "problem broke stopped working return"],
        "tiers": (100, 250),
        "examples": ["Noise-cancelling headphones under $200",
                     "Earbuds for running that stay in"],
    },
}
DEFAULT = "laptop"

# Examples on the home page, in order. Each must be in dealsense.db with price history:
# test-set laptops already are; add others with  py -m backend.lookup <ASIN>
FEATURED = {
    "laptop": ["B09HVC79PC", "B085B5SXLY", "B086KKKT15", "B08DDGJQDW", "B09JQKBQSB"],
    "headphones": ["B0D1XD1ZV3",          # AirPods Pro 2 (USB-C)
                   "B0CZPGX972"],         # Beats Solo 4
}


def category_of(p):
    """Category key for a Keepa product, or None if we don't cover it."""
    tree = [c.get("name", "") for c in (p.get("categoryTree") or [])]
    title = p.get("title") or ""
    for key, c in CATEGORIES.items():
        head = title[:80]
        acc, thing = c["not_title"].search(head), c["title"].search(head)
        if acc and (not thing or acc.start() < thing.start()):
            continue                     # "Ear Tips for AirPods": an accessory, not the thing
        if tree:
            in_tree = any(c["tree"].search(n) for n in tree)
            accessory = any("Accessor" in n and n not in c["parents"] for n in tree)
            if in_tree and not accessory:
                return key
        elif c["title"].search(title):
            return key
    return None


def get(key):
    return CATEGORIES.get(key or DEFAULT, CATEGORIES[DEFAULT])


def tier_for(price, key=DEFAULT):
    if price is None:
        return ""
    low, high = get(key)["tiers"]
    return "budget" if price < low else "mid-range" if price < high else "premium"


def covered():
    """'laptops and headphones', for messages."""
    names = [c["short"] for c in CATEGORIES.values()]
    return " and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0]
