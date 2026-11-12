"""Product categories MyrmX covers, and everything that differs between them.

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
        # "What's it for?" shortcuts: a ready-made search plan per use (no planning step)
        "uses": [
            {"key": "student", "label": "Student",
             "search_terms": ["student laptop 16GB", "lightweight laptop for college"],
             "min_price": 300, "max_price": 800,
             "summary": "A light, reliable laptop for schoolwork: long battery life, 8-16GB "
                        "RAM, a comfortable keyboard, and a good price."},
            {"key": "designer", "label": "Graphic designer",
             "search_terms": ["laptop for graphic design OLED", "creator laptop 32GB"],
             "min_price": 800, "max_price": 2200,
             "summary": "A laptop for graphic design: a sharp, color-accurate screen "
                        "(OLED or wide color gamut), 16-32GB RAM, and a strong CPU/GPU."},
            {"key": "gamer", "label": "Gamer",
             "search_terms": ["gaming laptop RTX 5060", "gaming laptop RTX 5070"],
             "min_price": 800, "max_price": 2000, "must_have": ["RTX"],
             "summary": "A gaming laptop with a recent NVIDIA RTX GPU, a high-refresh screen "
                        "and good cooling."},
            {"key": "programmer", "label": "Programmer",
             "search_terms": ["laptop 32GB RAM 1TB SSD", "developer laptop 32GB"],
             "min_price": 700, "max_price": 1800,
             "summary": "A laptop for programming: 16-32GB RAM, a fast SSD, a good keyboard "
                        "and enough battery for a day."},
            {"key": "travel", "label": "Business travel",
             "search_terms": ["ultralight business laptop", "thin and light laptop long battery"],
             "min_price": 600, "max_price": 1800,
             "summary": "A thin, light laptop for travel: long battery life, a solid build "
                        "and a good webcam for calls."},
        ],
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
        "uses": [
            {"key": "commute", "label": "Commuting and travel",
             "search_terms": ["noise cancelling headphones", "noise cancelling earbuds"],
             "min_price": 60, "max_price": 450, "must_have": ["noise.?cancel|ANC"],
             "summary": "Headphones or earbuds for commuting and flights: strong noise "
                        "cancelling, long battery life and comfort for hours."},
            {"key": "workout", "label": "Working out",
             "search_terms": ["sports earbuds for running", "workout earbuds ear hooks"],
             "min_price": 25, "max_price": 250,
             "summary": "Earbuds for workouts and running: a secure fit, sweat resistance and "
                        "decent battery life."},
            {"key": "gaming", "label": "Gaming",
             "search_terms": ["gaming headset", "wireless gaming headset"],
             "min_price": 40, "max_price": 300,
             "summary": "A gaming headset: a clear microphone, comfort for long sessions and "
                        "good positional sound."},
            {"key": "calls", "label": "Calls and work",
             "search_terms": ["headset with microphone for calls", "bluetooth headset noise cancelling microphone"],
             "min_price": 30, "max_price": 300,
             "summary": "A headset for calls and remote work: a clear microphone that blocks "
                        "background noise, comfort and reliable Bluetooth."},
            {"key": "music", "label": "Music lovers",
             "search_terms": ["hi-fi headphones", "studio monitor headphones"],
             "min_price": 80, "max_price": 500,
             "summary": "Headphones for listening to music: detailed, balanced sound and "
                        "comfort, wired or wireless."},
        ],
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


def use_plan(preset):
    """'laptop:designer' -> (category key, search plan, label), or (None, None, None)."""
    key, _, use = (preset or "").partition(":")
    for u in CATEGORIES.get(key, {}).get("uses", []):
        if u["key"] == use:
            plan = {"category": key, "search_terms": u["search_terms"],
                    "min_price": u["min_price"], "max_price": u["max_price"],
                    "must_have": u.get("must_have", []), "summary": u["summary"]}
            return key, plan, u["label"]
    return None, None, None


def all_uses():
    """For the web page: [{category, label, uses: [{key, label}]}]."""
    return [{"category": k, "label": c["short"].capitalize(),
             "uses": [{"key": f"{k}:{u['key']}", "label": u["label"]} for u in c["uses"]]}
            for k, c in CATEGORIES.items()]
