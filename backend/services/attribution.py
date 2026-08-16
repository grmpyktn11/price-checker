import logging
import re

# identity from search-tile titles only: Best Buy tiles carry no model number and its product
# page is unreachable live, so a title is the only evidence there is. every rail below is
# biased toward preferring a miss over a wrong match, because inherited specs feed a hard
# filter - a wrong match silently admits a product that fails the user's stated requirement.
GENERIC_BRAND_WORDS = ("portable", "power", "usb", "wireless", "battery", "charger", "external",
                       "fast", "slim", "magnetic", "travel", "compact", "mini", "new", "the")
STOPWORDS = GENERIC_BRAND_WORDS + ("with", "for", "and", "built", "in", "pack", "bank", "cable",
                                   "cables", "plug", "wall", "display", "led", "black", "white",
                                   "gray", "grey", "blue", "red", "silver", "pink", "purple")
SCALE_SUFFIXES = {"k": 1000, "m": 1000000}   # Best Buy writes "20K" where Amazon writes "20000mAh"
UNIT_ALIASES = {"mah": "capacity", "wh": "energy", "w": "power", "watt": "power",
                "watts": "power", "v": "volts", "gb": "storage", "tb": "storage",
                "in": "length", "inch": "length", "inches": "length", "mm": "length",
                "lb": "weight", "lbs": "weight", "oz": "weight"}
BRAND_SEARCH_DEPTH = 3     # all generic this far in means the title has no brand at the front
DISTINCTIVE_MIN_LENGTH = 3
DISTINCTIVE_MIN_DIGITS = 3   # a shared number needs 3+ digits to count as model-like evidence
NUMBER_PATTERN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([a-z]+)?")

logger = logging.getLogger(__name__)


# lowercase, punctuation to spaces, split. "Power Bank (20K, 87W)" -> [power, bank, 20k, 87w]
def title_tokens(title: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split()


# first purely alphabetic non-stopword in the opening tokens. half the Amazon tiles start with
# generic words ("Portable Charger with Wall Plug...") and get None, so they never donate
def brand_token(title: str) -> str | None:
    for token in title_tokens(title)[:BRAND_SEARCH_DEPTH]:
        if token.isalpha() and token not in STOPWORDS:
            return token
    return None


# numbers split into two pools: those with a recognised unit, keyed by unit kind, and bare
# ones. an unrecognised unit means bare, because comparing unknown units would invent conflicts
def title_numbers(title: str) -> tuple[dict, set]:
    united: dict[str, set] = {}
    bare: set[float] = set()
    for digits, unit in NUMBER_PATTERN.findall((title or "").lower()):
        value = float(digits.replace(",", ""))
        if unit in SCALE_SUFFIXES:
            bare.add(value * SCALE_SUFFIXES[unit])
            continue
        kind = UNIT_ALIASES.get(unit or "")
        if kind:
            united.setdefault(kind, set()).add(value)
        else:
            bare.add(value)
    return united, bare


def all_numbers(title: str) -> set:
    united, bare = title_numbers(title)
    return set(bare).union(*united.values()) if united else set(bare)


# a bare number has no unit, so it is compared against every number the other title states,
# united or bare. "10K" is capacity written without a unit, and only this comparison can see
# that it disagrees with "20000mAh". an empty pool on the other side is no evidence, not a
# conflict: "Anker 737" vs "Anker Power Bank" stays a maybe
def unmatched_bare(bare: set, other_numbers: set) -> bool:
    return bool(bare and other_numbers and not (bare & other_numbers))


# rail 2, the most valuable one. a wrong match defeats a stated hard filter, which is worse
# than no match at all, so this rail rejects on any numeric disagreement it can see
def numbers_conflict(a_title: str, b_title: str) -> bool:
    a_united, a_bare = title_numbers(a_title)
    b_united, b_bare = title_numbers(b_title)
    # same unit kind, different values: "24,000 mAh" vs "20,000 mAh"
    for kind in set(a_united) & set(b_united):
        if a_united[kind] != b_united[kind]:
            return True
    # a bare number the other title never states: "Anker 737" vs "Anker 733", and
    # "Power Bank (10K, 87W)" vs "Power Bank 20000mAh 87W"
    return (unmatched_bare(a_bare, all_numbers(b_title))
            or unmatched_bare(b_bare, all_numbers(a_title)))


# rail 3: generic word overlap can never establish identity, so a shared token must carry
# digits and letters, or be a shared number of at least DISTINCTIVE_MIN_DIGITS digits.
# numbers are compared on normalized values, so Best Buy's "20K" matches Amazon's "20000mAh"
def distinctive_shared(a_title: str, b_title: str, brand: str | None) -> set[str]:
    shared = set(title_tokens(a_title)) & set(title_tokens(b_title))
    tokens = {
        token for token in shared
        if token != brand and token not in STOPWORDS
        and len(token) >= DISTINCTIVE_MIN_LENGTH
        and any(char.isdigit() for char in token) and any(char.isalpha() for char in token)
    }
    numbers = {
        str(int(value)) for value in all_numbers(a_title) & all_numbers(b_title)
        if len(str(int(value))) >= DISTINCTIVE_MIN_DIGITS
    }
    return tokens | numbers


# all three rails must pass.
# accepted limit, documented rather than tightened because no rail can close it: titles that
# are silent about the difference. "Anker Power Bank 20,000mAh" matches "Anker Zolo Power Bank
# 20,000mAh" - same brand, same stated capacity, probably different product lines. the rails
# only compare what the titles say. inherited specs feed a HARD filter, so specs_inherited_from
# is what makes such a case findable after the fact
def same_product(a_title: str, b_title: str) -> bool:
    brand = brand_token(a_title)
    if brand is None or brand != brand_token(b_title):
        return False
    if numbers_conflict(a_title, b_title):
        return False
    return bool(distinctive_shared(a_title, b_title, brand))


def candidate_title(candidate) -> str:
    return candidate.product.get("name") or ""


# fills empty spec dicts only: a candidate with even one first-party spec is never touched,
# and an inheritor is never added to the donor pool, so specs cannot propagate onward
def attribute_specs(candidates: list) -> None:
    donors = [c for c in candidates if c.specs]
    for candidate in candidates:
        if candidate.specs:
            continue
        title = candidate_title(candidate)
        matches = [donor for donor in donors if same_product(title, candidate_title(donor))]
        # ambiguity is exactly where a wrong match is most likely: resolve it to a miss
        if len(matches) != 1:
            if matches:
                logger.info("ambiguous spec donor for %s: %d candidates", title, len(matches))
            continue
        donor = matches[0]
        candidate.specs = dict(donor.specs)
        candidate.specs_inherited_from = donor.retailer
        candidate.specs_donor = donor
        logger.info("specs inherited: %r <- %r [%s]", title, candidate_title(donor),
                    donor.retailer)
