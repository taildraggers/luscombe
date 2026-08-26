"""Scraper for Luscombe taildragger listings on barnstormers.com.

Barnstormers' single-manufacturer category pages (the same pattern seen in
the companion Aviat, CubCrafters, de Havilland, Maule, Van's RV, and RANS
repos) can mix in off-brand or off-topic listings with no distinguishing
HTML markup from the genuine ones. So results are filtered by title against
a small allowlist of Luscombe product names before being published.

On top of that brand allowlist, only whole-aircraft-for-sale listings are
kept: each ad's title must match a recognized Luscombe model code, and
titles that look like parts/accessories/services/raffles are dropped.
Surviving titles are rewritten to a canonical "YEAR LUSCOMBE MODEL" form
when the ad states a model year, or just "LUSCOMBE MODEL" when it doesn't.

taildraggers.com is taildragger-only, so the Model 11E Sedan is excluded:
unlike the rest of the Luscombe lineup it's a dedicated tricycle-gear
modernization of the Model 11A with no taildragger option. On top of that,
any individual ad (any model) whose own text explicitly calls out
tricycle/nosewheel gear is dropped too, the same policy applied in the
companion RANS repo.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .common import (
    Listing,
    extract_date,
    extract_location,
    extract_price,
    fetch,
    format_aircraft_title,
)

SITE_NAME = "Barnstormers.com"
BASE = "https://www.barnstormers.com"
MAKE = "Luscombe"

# Category page for Luscombe taildragger listings on Barnstormers.
CATEGORY_URLS = [
    f"{BASE}/category-22401-Taildragger--Luscombe.html",
]

MAX_PAGES = 10
LISTING_LINK_RE = re.compile(r"^/classified-(\d+)-(.+)\.html$")
GENERIC_SITE_TITLE_SNIPPET = "barnstormers.com find aircraft"


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text.lower())


# Model 8 family (Silvaire): "8" optionally followed by a single trim
# letter (A-F), or "T8F" for the tandem-seat Observer variant. Model 11
# family (Sedan): "11" optionally followed by "A" - "11E" is deliberately
# NOT matched here, since the 11E is a tricycle-gear-only modernization
# with no taildragger option (see module docstring).
_MODEL_CODE_RE = re.compile(r"\b(t8f|8[a-f]?|11a?)\b", re.IGNORECASE)

# Explicit rejection: an ad naming the 11E specifically must never fall
# through to the generic "Sedan" marketing-name rule below and get
# mislabeled as a plain (taildragger) Model 11 - the same class of bug
# fixed in the companion RANS repo, where a bare "Coyote" marketing name
# overrode an explicit but unmatched model suffix.
_REJECTED_MODEL_RE = re.compile(r"\b11e\b", re.IGNORECASE)

# Only ads whose title matches one of these (case/hyphen/space-insensitive,
# compared against a fully compacted - no spaces or hyphens - form of the
# title) are kept, since the category page itself isn't reliably
# Luscombe-only.
TARGET_MODEL_PHRASES = [
    "luscombe",
    "silvaire",
    "sedan",
    "observer",
    "8a", "8b", "8c", "8d", "8e", "8f", "t8f",
    "11a",
]


def _matches_target_models(title: str) -> bool:
    compact = _compact(title)
    return any(phrase in compact for phrase in TARGET_MODEL_PHRASES)


# Common marketing names, used only as a fallback when no explicit model
# code is present. Unlike the model codes, these plain English words
# (especially "Sedan") aren't distinctive enough to trust on their own (a
# lesson learned the hard way in the companion Piper repo, where a bare
# "Cub" mislabeled non-Piper homebuilts) - so each one also requires the
# title to say "Luscombe" explicitly.
_MARKETING_NAME_RULES = [
    (re.compile(r"\bobserver\b", re.IGNORECASE), "T8F"),
    (re.compile(r"\bsilvaire\b", re.IGNORECASE), "8"),
    (re.compile(r"\bsedan\b", re.IGNORECASE), "11"),
]

# Ads whose title or body text explicitly calls out tricycle/nosewheel gear
# are dropped, regardless of which model they are - see module docstring.
_NON_TAILWHEEL_KEYWORDS = (
    "tricycle gear",
    "tricycle landing gear",
    "trike gear",
    "tri-gear",
    "tri gear",
    "nosewheel",
    "nose wheel",
    "nose-wheel",
)


def _is_non_tailwheel(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _NON_TAILWHEEL_KEYWORDS)


def _extract_model(title: str) -> tuple[str, str] | None:
    if _REJECTED_MODEL_RE.search(title):
        return None

    match = _MODEL_CODE_RE.search(title)
    if match:
        return MAKE, match.group(1).upper()

    if "luscombe" in _compact(title):
        for pattern, canonical in _MARKETING_NAME_RULES:
            if pattern.search(title):
                return MAKE, canonical
    return None


def _title_from_url(url: str) -> str:
    """Listing pages share a generic <title>/<h1>, but the URL slug is the ad's own title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = LISTING_LINK_RE.match("/" + slug)
    if not match:
        return unquote(slug)
    return unquote(match.group(2)).replace("-", " ").strip()


def _find_listing_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if LISTING_LINK_RE.match(href):
            links.add(urljoin(BASE, href))
    return links


def _page_url(category_url: str, page: int) -> str:
    """Build a category page's URL directly.

    Barnstormers' category pager renders as page-number buttons with no
    "Next" text or rel="next" attribute for a link-following heuristic to
    find (confirmed on the companion Van's RV, Stearman, Waco, Pitts,
    Taylorcraft, Swift, and Beech repos, where that approach silently
    stopped after page 1) - so each page's URL is built from the known
    ?seocategory=<url-encoded-path>&page=<n> pattern instead.
    """
    if page <= 1:
        return category_url
    path = urlparse(category_url).path
    return f"{category_url}?seocategory={quote(path, safe='')}&page={page}"


def _debug_dump_hrefs(html: str, limit: int = 25) -> None:
    soup = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    interesting = [h for h in hrefs if "classified" in h.lower() or "luscombe" in h.lower()]
    sample = interesting[:limit] or hrefs[:limit]
    print(f"  [debug] {len(hrefs)} total <a href> on page; sample: {sample}")


def _parse_detail_page(url: str, html: str) -> Listing | None:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if title:
        title = re.sub(r"\s*[\|\-]\s*Barnstormers.*$", "", title, flags=re.IGNORECASE).strip()
    if not title or GENERIC_SITE_TITLE_SNIPPET in title.lower():
        title = _title_from_url(url)
    if not title:
        return None

    if not _matches_target_models(title):
        return None

    text = soup.get_text(" ", strip=True)

    if _is_non_tailwheel(title) or _is_non_tailwheel(text):
        return None

    formatted_title = format_aircraft_title(title, text, _extract_model)
    if not formatted_title:
        return None
    title = formatted_title

    price = extract_price(text)
    location = extract_location(text)
    date_posted = extract_date(text)

    return Listing(
        title=title,
        price=price,
        location=location,
        date_posted=date_posted,
        site=SITE_NAME,
        url=url,
    )


def scrape() -> list[Listing]:
    print(f"[{SITE_NAME}] starting scrape")
    all_links: set[str] = set()

    for category_url in CATEGORY_URLS:
        seen_this_category: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            url = _page_url(category_url, page)
            html = fetch(url)
            if not html:
                break
            links = _find_listing_links(html)
            new_links = links - seen_this_category
            print(f"  [{category_url}] page {page}: {len(links)} links ({len(new_links)} new)")
            if page == 1 and not links:
                _debug_dump_hrefs(html)
            seen_this_category |= links
            if not new_links:
                break
        all_links |= seen_this_category

    print(f"[{SITE_NAME}] {len(all_links)} unique listing URLs found")

    candidate_links = {url for url in all_links if _matches_target_models(_title_from_url(url))}
    print(f"[{SITE_NAME}] {len(candidate_links)} match Luscombe product names")

    listings: list[Listing] = []
    for url in sorted(candidate_links):
        html = fetch(url)
        if not html:
            continue
        listing = _parse_detail_page(url, html)
        if listing:
            listings.append(listing)

    print(f"[{SITE_NAME}] parsed {len(listings)} listings")
    return listings
