"""Canonical-shoe → retailer-product mapping.

Given a `CanonicalShoe` and a list of `SearchResult` candidates from one
retailer, pick the best product URL and attach a confidence in [0, 1].

Scoring rules (see plan.md chunk 3):
- Hard rejects return 0: brand, gender, version, variant_type mismatches.
- Manufacturer style-code prefix match → 0.99 (the strongest signal available
  on retailers that expose the code).
- Otherwise: weighted token overlap on title tokens, where distinctive tokens
  (brand, model, version) count for more than noise ("men's", "running", etc.).

Tiers:
- confidence ≥ 0.9 → AUTO (write mapping, no review)
- 0.6 ≤ confidence < 0.9 → FLAGGED (write mapping, queue for review)
- confidence < 0.6 → REJECTED (do not map)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .adapters.base import SearchResult
from .models import CanonicalShoe


AUTO_THRESHOLD = 0.9
FLAG_THRESHOLD = 0.6

# Tokens that are common across running-shoe listings and carry no signal.
_NOISE_TOKENS: frozenset[str] = frozenset({
    "men", "mens", "men's",
    "women", "womens", "women's",
    "unisex",
    "running", "road", "trail",
    "shoe", "shoes",
    "the",
})

# Variant-type tokens that appear in listings for specialty editions.
_VARIANT_TOKENS: dict[str, str] = {
    "gtx": "GTX",
    "gore-tex": "GTX",
    "goretex": "GTX",
    "wide": "Wide",
    "trail": "Trail",
}


class MappingTier(Enum):
    AUTO = "auto"
    FLAGGED = "flagged"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MappingOutcome:
    """Result of evaluating a list of SearchResults against a canonical shoe."""
    best: SearchResult | None
    confidence: float
    tier: MappingTier
    # Notes are human-readable; included in mapping_review.md for FLAGGED tier.
    notes: tuple[str, ...] = ()


# --- public API ---

def score_match(canonical: CanonicalShoe, result: SearchResult) -> float:
    """Return a match score in [0, 1]. 0 means a hard reject."""
    tokens = _tokenize(result)
    haystack_text = " ".join(tokens)

    if _is_brand_mismatch(canonical, tokens):
        return 0.0
    if _is_gender_mismatch(canonical, result, tokens):
        return 0.0
    if _is_version_mismatch(canonical, tokens):
        return 0.0
    if _is_variant_type_mismatch(canonical, tokens, haystack_text):
        return 0.0

    if _style_code_prefix_match(canonical, result):
        return 0.99

    return _token_overlap_score(canonical, tokens)


def tier_for(confidence: float) -> MappingTier:
    if confidence >= AUTO_THRESHOLD:
        return MappingTier.AUTO
    if confidence >= FLAG_THRESHOLD:
        return MappingTier.FLAGGED
    return MappingTier.REJECTED


def pick_best(
    canonical: CanonicalShoe, results: list[SearchResult]
) -> MappingOutcome:
    """Pick the highest-scoring candidate and classify it into a tier."""
    best: SearchResult | None = None
    best_score = 0.0
    notes: list[str] = []
    for r in results:
        s = score_match(canonical, r)
        if s > best_score:
            best_score = s
            best = r

    if best is None or best_score < FLAG_THRESHOLD:
        return MappingOutcome(best=None, confidence=0.0, tier=MappingTier.REJECTED)

    tier = tier_for(best_score)
    if tier is MappingTier.FLAGGED:
        notes.append(
            f"manual review: confidence {best_score:.2f} below auto threshold "
            f"{AUTO_THRESHOLD:.2f}"
        )
    return MappingOutcome(
        best=best, confidence=best_score, tier=tier, notes=tuple(notes),
    )


# --- hard-reject checks ---

def _is_brand_mismatch(canonical: CanonicalShoe, tokens: list[str]) -> bool:
    want = _norm(canonical.brand)
    if not want:
        return False
    # Brand tokens can be single words ("asics") or compound ("new", "balance").
    text = " ".join(tokens)
    return want not in text


def _is_gender_mismatch(
    canonical: CanonicalShoe, result: SearchResult, tokens: list[str]
) -> bool:
    wanted = canonical.gender
    url_lower = result.product_url.lower()
    title_lower = (result.title or "").lower()
    url_letter = _gender_letter_from_url(url_lower)
    title_letter = _gender_letter_from_title(title_lower)
    evidence = title_letter or url_letter
    if evidence is None:
        return False
    if wanted == "mens" and evidence == "W":
        return True
    if wanted == "womens" and evidence == "M":
        return True
    return False


def _is_version_mismatch(canonical: CanonicalShoe, tokens: list[str]) -> bool:
    want = canonical.version
    if not want:
        return False
    want_norm = _norm(str(want))
    # Pull numeric/version-like tokens out of the title.
    version_tokens = [t for t in tokens if _looks_like_version(t)]
    if not version_tokens:
        # Title has no version at all; can't prove a mismatch — leave to scorer.
        return False
    return want_norm not in version_tokens


def _is_variant_type_mismatch(
    canonical: CanonicalShoe, tokens: list[str], haystack_text: str,
) -> bool:
    listing_variant = _variant_type_from_tokens(tokens, haystack_text)
    want = canonical.variant_type
    if want is None:
        # Listing declares a specialty variant but we want the plain version.
        return listing_variant is not None
    if want == "Trail":
        # "Trail" canonicals are shoes that are inherently trail (Speedgoat,
        # Peregrine). Listings rarely spell it out, so we only reject when
        # the listing declares an incompatible specialty (GTX, Wide).
        return listing_variant in {"GTX", "Wide"}
    # Strict match for GTX / Wide.
    return listing_variant != want


def _variant_type_from_tokens(tokens: list[str], haystack_text: str) -> str | None:
    for t in tokens:
        if t in _VARIANT_TOKENS:
            return _VARIANT_TOKENS[t]
    # "gore-tex" tokenizes as "gore" + "tex" — check the raw text too.
    if "gore-tex" in haystack_text or "gore tex" in haystack_text:
        return "GTX"
    return None


# --- style-code check ---

def _style_code_prefix_match(canonical: CanonicalShoe, result: SearchResult) -> bool:
    prefix = canonical.mfr_style_prefix
    code = result.mfr_style_code
    if not prefix or not code:
        return False
    return code.upper().startswith(prefix.upper())


# --- token-overlap scoring ---

def _token_overlap_score(canonical: CanonicalShoe, tokens: list[str]) -> float:
    """Weighted Jaccard-ish overlap on distinctive tokens.

    Brand tokens are already confirmed (hard-reject would have fired).
    We score on: model tokens present + version present + bonus for no extra
    noise.
    """
    model_tokens = _model_tokens(canonical)
    if not model_tokens:
        return 0.0

    matched = sum(1 for t in model_tokens if t in tokens)
    model_coverage = matched / len(model_tokens)

    version_bonus = 0.0
    if canonical.version:
        if _norm(str(canonical.version)) in tokens:
            version_bonus = 0.15
        else:
            # Missing version in the title → ambiguous; small credit so we stay
            # above the flag floor when brand+model match perfectly.
            version_bonus = 0.05

    score = 0.7 * model_coverage + version_bonus
    # Brand presence gives a small boost (it's required to get here).
    score += 0.10

    return max(0.0, min(score, 0.95))  # cap under the style-code 0.99


# --- helpers ---

def _tokenize(result: SearchResult) -> list[str]:
    pieces = [result.title or ""]
    # Title is the primary signal; the URL slug occasionally helps break ties
    # (e.g. Running Warehouse encodes "Novablast_5" in the path).
    pieces.append(_slug_from_url(result.product_url))
    raw = " ".join(pieces).lower()
    # Split on whitespace / punctuation, keep digit+letter version tokens intact.
    raw = raw.replace("_", " ").replace("-", " ")
    tokens = re.split(r"[^a-z0-9]+", raw)
    out: list[str] = []
    for t in tokens:
        if not t:
            continue
        if t in _NOISE_TOKENS:
            continue
        out.append(t)
    return out


def _slug_from_url(url: str) -> str:
    # "https://www.runningwarehouse.com/ASICS_Novablast_5/descpage-ANB5W1.html"
    # → "ASICS_Novablast_5"
    m = re.search(r"/([^/]+)/descpage-", url)
    return m.group(1) if m else ""


def _model_tokens(canonical: CanonicalShoe) -> list[str]:
    raw = _norm(canonical.model)
    tokens = [t for t in re.split(r"[^a-z0-9]+", raw) if t]
    return [t for t in tokens if t not in _NOISE_TOKENS]


def _norm(s: str) -> str:
    return s.strip().lower()


def _looks_like_version(token: str) -> bool:
    if not token:
        return False
    if token.isdigit():
        return True
    # "18a", "5gs" style version-ish tokens.
    return token[:-1].isdigit() and token[-1].isalpha()


def _gender_letter_from_url(url_lower: str) -> str | None:
    # RW search URLs differentiate mens/womens. Product URLs encode M/W/K in
    # the product code near "descpage-".
    if "search-mens" in url_lower:
        return "M"
    if "search-womens" in url_lower:
        return "W"
    m = re.search(r"descpage-[a-z0-9]*?([mwk])[0-9]", url_lower)
    if m:
        letter = m.group(1).upper()
        if letter == "K":
            return None  # kids — not mens/womens; don't reject on it.
        return letter
    return None


def _gender_letter_from_title(title_lower: str) -> str | None:
    # Check the raw title before the noise filter strips gender markers.
    if re.search(r"\bwomen'?s?\b", title_lower):
        return "W"
    if re.search(r"\bmen'?s?\b", title_lower):
        return "M"
    return None
