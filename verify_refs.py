#!/usr/bin/env python3
"""
Verify references.bib entries against authoritative sources.

For each entry:
- If `eprint` (arXiv ID) is set: query arXiv API and compare title + first author.
- Else if `doi` is set (and not arXiv's own DOI prefix): query Crossref and compare.
- Else if `url` is set: fetch the page and check whether title + author appear in the body.
- Else: flag as unverifiable.

Title comparison uses difflib.SequenceMatcher on normalized strings (lowercased,
non-alphanumerics stripped). Author comparison checks whether the bib's first
author surname appears in the API's first-author field.

Pass threshold:
  - title similarity >= 0.70  AND  first-author surname match
  - URL-only entries pass if the title appears in the fetched page

Run:   python3 verify_refs.py
"""

import difflib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BIB = Path(__file__).parent / "references.bib"
# Bumped from 0.70 after red-team review: "Eliciting Secret Knowledge..." vs
# "Eliciting Latent Knowledge..." scored 0.93 — distinct papers with similar
# names. With eprint-confirmed identity we still allow 0.70 via the arXiv
# fallback for stale S2 titles (e.g. betley2025).
TITLE_SIM_THRESHOLD = 0.85
ARXIV_DELAY_SEC = 1.0
HTTP_TIMEOUT = 15
URL_BODY_MIN_BYTES = 1024  # below this, treat fetch as suspicious
SOFT_404_MARKERS = (
    "page not found", "404 not found", "404 - ", "this post has been removed",
    "this content has been removed", "the page you are looking for", "deleted page",
)


# ---------- bib parsing (regex-only — sufficient for our well-formed file) ----------

ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.DOTALL)
_FIELD_NAME_RE = re.compile(r"(\w+)\s*=\s*")
_BARE_VALUE_RE = re.compile(r"\w+")


def _parse_fields(body):
    """Scan a bibtex entry body and yield (name, value) pairs, tracking
    brace depth manually so that arbitrarily-nested LaTeX markup like
    `{\\v{Z}}` or `{\\c{s}}` is handled correctly (FIELD_RE's once-nested
    regex couldn't balance these and silently dropped the whole field)."""
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " \t\n,":
            i += 1
        if i >= n:
            break
        m = _FIELD_NAME_RE.match(body, i)
        if not m:
            break
        name = m.group(1).lower()
        i = m.end()
        if i >= n:
            break
        if body[i] == "{":
            depth, i = 1, i + 1
            start = i
            while i < n and depth > 0:
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            val = body[start:i]
            if i < n:
                i += 1
        elif body[i] == '"':
            i += 1
            start = i
            while i < n and body[i] != '"':
                i += 1
            val = body[start:i]
            if i < n:
                i += 1
        else:
            bm = _BARE_VALUE_RE.match(body, i)
            if not bm:
                break
            val = bm.group(0)
            i = bm.end()
        val = re.sub(r"[{}]", "", val)
        val = re.sub(r"\s+", " ", val).strip()
        yield name, val


def parse_bib(text):
    entries = []
    for m in ENTRY_RE.finditer(text):
        etype = m.group(1).lower()
        key = m.group(2).strip()
        body = m.group(3)
        fields = dict(_parse_fields(body))
        entries.append({"type": etype, "key": key, **fields})
    return entries


# ---------- normalization helpers ----------

def norm_title(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Normalize venue strings so "NeurIPS 2024", "Advances in Neural Information
# Processing Systems", "NIPS", "Conference on NeurIPS" all collide on a canonical
# token. Also strips year, ordinal prefixes ("Twelfth", "41st"), and noise words.
_VENUE_ALIASES = {
    "neurips": ["nips", "neural information processing", "advances in neural information"],
    "icml": ["international conference on machine learning"],
    "iclr": ["international conference on learning representations"],
    "acl": ["association for computational linguistics annual meeting", "acl annual meeting"],
    "emnlp": ["empirical methods in natural language processing"],
    "naacl": ["north american chapter of the association for computational linguistics"],
    "aaai": ["aaai conference on artificial intelligence"],
    "tmlr": ["transactions on machine learning research"],
    "biometrika": [],
    "patterns": [],
    "foundations and trends in machine learning": ["foundations and trends ml", "found trends ml"],
}
_VENUE_NOISE = re.compile(
    r"\b(the|proceedings|of|conference|on|in|annual|meeting|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|"
    r"eighteenth|nineteenth|twentieth|workshop|long|papers|volume|"
    r"\d+(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)


def norm_venue(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b\d{4}\b", "", s)  # year
    s = _VENUE_NOISE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Collapse to canonical token if any alias matches
    for canonical, aliases in _VENUE_ALIASES.items():
        if canonical in s:
            return canonical
        for a in aliases:
            if a in s:
                return canonical
    return s


def extract_bib_venue(entry):
    """Extract claimed venue from a bibtex entry, by type."""
    t = entry.get("type", "")
    if t == "inproceedings":
        return entry.get("booktitle", "")
    if t == "article":
        return entry.get("journal", "")
    if t == "techreport":
        return entry.get("institution", "")
    if t == "misc":
        # @misc may have venue in `note` (e.g. "NeurIPS 2024 SoLaR Workshop") or
        # in `howpublished` (e.g. "LessWrong" / "Anthropic blog")
        return entry.get("note", "") or entry.get("howpublished", "")
    return ""


def venue_match(bib_venue, s2_venue, s2_venue_full):
    """Return (status, detail). status ∈ {'pass', 'warn', 'fail', 'skip'}."""
    if not bib_venue:
        return ("skip", "no venue claimed in bib")
    s2_combined = " | ".join(filter(None, [s2_venue, s2_venue_full]))
    if not s2_combined:
        return ("skip", "S2 has no venue (likely arXiv-only)")
    # S2 frequently returns just "arXiv.org" / "arXiv preprint arXiv:..." for
    # papers that have additional venues — workshops, tech reports, Anthropic
    # blog posts, ICLR/NeurIPS acceptances S2 hasn't ingested yet. Treat this
    # as no-info, not as a mismatch.
    if re.fullmatch(r"\s*(arxiv\.org|arxiv preprint[^|]*)\s*(\|\s*(arxiv\.org|arxiv preprint[^|]*)\s*)?",
                    s2_combined.lower()):
        return ("skip", f"S2 only knows arXiv preprint; bib's '{bib_venue[:40]}' not contradicted")
    nb = norm_venue(bib_venue)
    ns2 = norm_venue(s2_venue)
    ns2_full = norm_venue(s2_venue_full)
    if nb and (nb == ns2 or nb == ns2_full):
        return ("pass", f"bib='{bib_venue[:40]}' s2='{s2_combined[:40]}' canonical='{nb}'")
    # Fuzzy: substring or sim
    sim = max(
        title_sim(bib_venue, s2_venue) if s2_venue else 0,
        title_sim(bib_venue, s2_venue_full) if s2_venue_full else 0,
    )
    if sim >= 0.6 or (nb and (nb in ns2 or nb in ns2_full or ns2 in nb)):
        return ("pass", f"fuzzy sim={sim:.2f} bib='{bib_venue[:40]}' s2='{s2_combined[:40]}'")
    return ("warn", f"bib='{bib_venue[:50]}' s2='{s2_combined[:50]}'")


def title_sim(a, b):
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def _strip_to_ascii(s):
    """LaTeX-escape strip + NFKD-decompose unicode + drop non-letters."""
    s = re.sub(r"\\['`\"^~=.uvHc]", "", s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z]", "", s).lower()


def author_parts(name):
    """Split a bib author name into (surname, given). Handles comma-form
    'Lastname, Firstname' and space-form 'Firstname Lastname'. Both returned
    lowercased and ascii-only."""
    if not name:
        return ("", "")
    name = name.strip()
    if "," in name:
        parts = name.split(",", 1)
        surname = parts[0].strip()
        given = parts[1].strip() if len(parts) > 1 else ""
    else:
        toks = name.split()
        surname = toks[-1] if toks else ""
        given = " ".join(toks[:-1]) if len(toks) > 1 else ""
    return (_strip_to_ascii(surname), _strip_to_ascii(given))


def author_to_surname(name):
    """Normalize an author name (any format) to lowercase ascii surname."""
    return author_parts(name)[0]


def levenshtein(a, b):
    """Tiny Levenshtein distance (for surnames only — short strings)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def surname_match(bib_surname, api_full_name):
    """Match a bib surname against an S2 full author name.
    Tolerates compound surnames (van der Weij ↔ weij) AND catches typos
    (Vaswan ↔ Vaswani fails edit-distance ≤ 1 only when len(Vaswan)<5)."""
    if not bib_surname or not api_full_name:
        return False
    api_full = _strip_to_ascii(api_full_name)
    api_last = _strip_to_ascii(api_full_name.split()[-1]) if api_full_name.split() else ""
    # Exact match on last word, or edit-distance ≤ 1 on last word
    if bib_surname == api_last:
        return True
    if api_last and levenshtein(bib_surname, api_last) <= 1 and min(len(bib_surname), len(api_last)) >= 4:
        return True
    # Compound-surname tolerance: bib surname appears as substring of S2 full
    # OR vice versa. Required for "van der Weij" / "weij" cases.
    if len(bib_surname) >= 4 and (bib_surname in api_full or api_full in bib_surname):
        return True
    return False


def detect_name_swap(bib_first_author_field, api_full_name):
    """Detect first/last name swap. Bib has 'Adam, Karvonen' (typo) and S2
    has 'Adam Karvonen' (correct). Return True if the swap is detected."""
    if not bib_first_author_field or not api_full_name or "," not in bib_first_author_field:
        return False
    surname, given = author_parts(bib_first_author_field)
    if not surname or not given:
        return False
    api_full_norm = _strip_to_ascii(api_full_name)
    # Correct: api == "given surname" → norm = given+surname
    correct = given + surname
    swapped = surname + given
    # If the bib's "surname" (post-comma=given) order matches API's normalized
    # full name (givenname + surname), AND the actual claimed surname doesn't,
    # we have a swap.
    if api_full_norm == swapped and api_full_norm != correct and surname != given:
        return True
    return False


def extract_year_from_key(cite_key):
    """Pull a 4-digit year out of a cite key like 'betley2025' or 'park2024lrh'.
    Returns int or None."""
    m = re.search(r"(19|20|21)(\d{2})", cite_key)
    if m:
        return int(m.group(0))
    return None


def all_author_surnames(authors_field):
    """Extract all surnames from a bibtex `author` field."""
    if not authors_field:
        return []
    return [author_to_surname(a) for a in authors_field.split(" and ") if author_to_surname(a)]


def first_author_surname(authors_field):
    """Extract first-author surname from a bibtex `author` field."""
    surnames = all_author_surnames(authors_field)
    return surnames[0] if surnames else ""


# ---------- HTTP helpers ----------

def http_get(url, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-refs/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


_OPENREVIEW_CACHE = {}

def fetch_openreview_venue(forum_id):
    """Cross-check a bib's venue claim against OpenReview when an openreview.net
    URL is available. Returns the venue string (e.g. 'ICLR 2026 Poster') or
    None if the lookup fails."""
    if forum_id in _OPENREVIEW_CACHE:
        return _OPENREVIEW_CACHE[forum_id]
    venue = None
    # Try v2 API first
    for url in (
        f"https://api2.openreview.net/notes?id={forum_id}",
        f"https://api.openreview.net/notes?id={forum_id}",
    ):
        try:
            data = json.loads(http_get(url, timeout=15))
        except Exception:
            continue
        notes = data.get("notes") or []
        if not notes:
            continue
        content = notes[0].get("content") or {}
        v = content.get("venue") or content.get("venueid")
        if isinstance(v, dict):
            venue = v.get("value") or ""
        elif isinstance(v, str):
            venue = v
        if venue:
            break
    _OPENREVIEW_CACHE[forum_id] = venue
    return venue


def extract_openreview_id(url):
    """Pull the forum id from an openreview URL, or return None."""
    if not url:
        return None
    m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


# ---------- per-source checks ----------

def fetch_semanticscholar_batch(identifiers, max_retries=4):
    """
    Fetch multiple papers in a single batch request to Semantic Scholar.
    `identifiers` is a list of strings like 'arXiv:1706.03762' or 'DOI:10.1093/biomet/...'.
    Returns dict {identifier: {title, first_author, year} or None}.
    """
    url = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=title,authors,year,venue,publicationVenue"
    body = json.dumps({"ids": list(identifiers)}).encode()
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json", "User-Agent": "verify-refs/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            out = {}
            for ident, item in zip(identifiers, data):
                if item is None:
                    out[ident] = None  # paper not found
                else:
                    authors = item.get("authors") or []
                    pv = item.get("publicationVenue") or {}
                    out[ident] = {
                        "title": item.get("title") or "",
                        "first_author": authors[0]["name"] if authors else "",
                        "all_authors": [a.get("name", "") for a in authors],
                        "year": item.get("year"),
                        "venue": item.get("venue") or "",
                        "venue_full": pv.get("name", "") if isinstance(pv, dict) else "",
                    }
            return out
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                print(f"  batch HTTP {e.code}, retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  batch failed: HTTP {e.code}", file=sys.stderr)
            return {ident: {"_error": f"HTTP {e.code}"} for ident in identifiers}
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return {ident: {"_error": str(e)} for ident in identifiers}
    return {ident: {"_error": "max retries"} for ident in identifiers}


def check_via_semanticscholar(
    identifier, cache, entry, all_bib_surnames=None
):
    """Verify a bib `entry` against S2 metadata cached at `identifier`.
    Performs all of: title, first-author (edit-distance + swap detection),
    full author list (positional), year, venue (with OpenReview fallback)."""
    info = cache.get(identifier)
    if info is None:
        return {"ok": False, "reason": f"{identifier} not found on Semantic Scholar (paper does not exist?)"}
    if "_error" in info:
        return {"ok": False, "reason": f"S2 fetch error: {info['_error']}"}

    expected_title = entry.get("title", "")
    bib_first_author_field = (entry.get("author", "").split(" and ")[0]) if entry.get("author") else ""
    expected_surname, _expected_given = author_parts(bib_first_author_field)
    bib_venue = extract_bib_venue(entry)
    bib_year = entry.get("year", "")
    cite_key = entry.get("key", "")
    bib_url = entry.get("url", "")

    # ---- title ----
    score = title_sim(expected_title, info["title"])

    # ---- first-author ----
    author_ok = surname_match(expected_surname, info["first_author"])
    swap_detected = detect_name_swap(bib_first_author_field, info["first_author"])
    if swap_detected:
        author_ok = False  # surname-substring may falsely accept; treat swap as failure

    ok = score >= TITLE_SIM_THRESHOLD and author_ok

    # ---- arXiv-fallback for stale S2 titles (preserved from prior version) ----
    arxiv_fallback_used = False
    if not ok and author_ok and identifier.startswith("arXiv:"):
        arxiv_id = identifier.split(":", 1)[1]
        try:
            html = http_get(f"https://arxiv.org/abs/{arxiv_id}", timeout=15)
            tm = re.search(r'<meta name="citation_title" content="([^"]+)"', html)
            if tm:
                arxiv_title = tm.group(1)
                arxiv_score = title_sim(expected_title, arxiv_title)
                if arxiv_score >= 0.70:  # eprint-confirmed identity → looser
                    ok = True
                    arxiv_fallback_used = True
                    score = arxiv_score
        except Exception:
            pass

    # ---- year ----
    s2_year = info.get("year")
    bib_year_int = int(bib_year) if bib_year and bib_year.isdigit() else None
    key_year = extract_year_from_key(cite_key)
    year_status = "skip"
    year_detail = ""
    if bib_year_int and s2_year:
        # arXiv preprint year may differ from venue year by ≤ 2 (preprint Dec X →
        # accepted X+1; or arXiv year is when first posted vs published year).
        if abs(bib_year_int - int(s2_year)) <= 2:
            year_status = "pass"
        else:
            year_status = "fail"
            year_detail = f"bib={bib_year_int} S2={s2_year}"
            ok = False
    if year_status != "fail" and bib_year_int and key_year and abs(bib_year_int - key_year) > 1:
        year_status = "warn"
        year_detail = (year_detail + " " if year_detail else "") + f"key='{cite_key}' year={bib_year_int}"

    # ---- venue (with OpenReview fallback) ----
    venue_status, venue_detail = venue_match(bib_venue, info.get("venue", ""), info.get("venue_full", ""))
    or_id = extract_openreview_id(bib_url)
    if venue_status == "skip" and bib_venue and or_id:
        # The bib claims a non-arXiv venue and provides an OpenReview URL we
        # can cross-check against — the highest-leverage fix from the red team.
        or_venue = fetch_openreview_venue(or_id)
        if or_venue:
            nb = norm_venue(bib_venue)
            nor = norm_venue(or_venue)
            if nb and nor and (nb == nor or nb in nor or nor in nb or title_sim(bib_venue, or_venue) >= 0.6):
                venue_status = "pass"
                venue_detail = f"OpenReview confirms: bib='{bib_venue[:40]}' OR='{or_venue[:40]}'"
            else:
                venue_status = "warn"
                venue_detail = f"OpenReview disagrees: bib='{bib_venue[:50]}' OR='{or_venue[:50]}'"
                ok = False  # this is the venue-fabrication failure mode
        else:
            venue_detail += " (OpenReview lookup failed; manually verify)"

    # ---- positional author-list check (replaces set-based comparison) ----
    bib_full_names = (entry.get("author", "").split(" and ")) if entry.get("author") else []
    api_full_names = info.get("all_authors", [])
    n_bib, n_api = len(bib_full_names), len(api_full_names)

    def _bib_first_word_post_comma(bf):
        """For bib name 'Smith, Logan Riggs' returns 'Smith'. For 'Logan Riggs Smith' returns 'Smith'."""
        return author_parts(bf)[0]

    def _api_last_word(an):
        toks = an.split()
        return _strip_to_ascii(toks[-1]) if toks else ""

    def _name_in_position_match(bib_name, api_name):
        bib_surname = _bib_first_word_post_comma(bib_name)
        api_full_norm = _strip_to_ascii(api_name)
        api_last = _api_last_word(api_name)
        if surname_match(bib_surname, api_name):
            return True
        # Compound surname tolerance
        bf = _strip_to_ascii(bib_name)
        if len(bf) >= 4 and (bf in api_full_norm or api_full_norm in bf):
            return True
        return False

    if not bib_full_names or not api_full_names:
        author_list_status = "skip"
        position_mismatches = []
    else:
        position_mismatches = []
        for i in range(min(n_bib, n_api)):
            if not _name_in_position_match(bib_full_names[i], api_full_names[i]):
                position_mismatches.append((i, bib_full_names[i], api_full_names[i]))
        if abs(n_bib - n_api) <= 1 and not position_mismatches:
            author_list_status = "pass"
        elif len(position_mismatches) <= 1 and abs(n_bib - n_api) <= 2:
            author_list_status = "warn"
        else:
            author_list_status = "fail"

    return {
        "ok": ok,
        "title_sim": score,
        "api_title": info["title"],
        "api_first_author": info["first_author"],
        "expected_surname": expected_surname,
        "author_match": author_ok,
        "swap_detected": swap_detected,
        "arxiv_fallback_used": arxiv_fallback_used,
        "venue_status": venue_status,
        "venue_detail": venue_detail,
        "s2_venue": info.get("venue", ""),
        "year_status": year_status,
        "year_detail": year_detail,
        "bib_year": bib_year_int,
        "s2_year": s2_year,
        "author_list_status": author_list_status,
        "n_bib_authors": n_bib,
        "n_api_authors": n_api,
        "position_mismatches": position_mismatches,
    }


def check_doi(doi, expected_title, expected_surname):
    if doi.startswith("10.48550/"):  # arXiv's own DOI namespace
        return {"ok": None, "reason": "arXiv DOI — should already be verified via eprint"}
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        data = json.loads(http_get(url))
    except Exception as e:
        return {"ok": False, "reason": f"crossref fetch error: {e}"}
    work = data.get("message", {})
    api_title = " ".join(work.get("title", [])) or ""
    authors = work.get("author") or []
    api_first_author = ""
    if authors:
        api_first_author = authors[0].get("family") or authors[0].get("name") or ""

    api_surname_norm = re.sub(r"[^a-zA-Z]", "", api_first_author).lower()
    score = title_sim(expected_title, api_title)
    author_ok = bool(expected_surname) and expected_surname in api_surname_norm

    ok = score >= TITLE_SIM_THRESHOLD and author_ok
    return {
        "ok": ok,
        "title_sim": score,
        "api_title": api_title,
        "api_first_author": api_first_author,
        "expected_surname": expected_surname,
        "author_match": author_ok,
    }


def check_url(url, expected_title, expected_surname):
    """Hardened URL check: require title AND author present, body length ≥ 1KB,
    and no soft-404 markers in the page body. Catches dead/edited blog posts
    that the previous loose substring check would silently pass."""
    try:
        body = http_get(url)
    except Exception as e:
        return {"ok": False, "reason": f"url fetch error: {e}"}
    body_lower = body.lower()
    body_norm = norm_title(body)
    title_norm = norm_title(expected_title)
    title_present = bool(title_norm) and title_norm[:50] in body_norm
    author_present = bool(expected_surname) and expected_surname in body_lower
    too_short = len(body) < URL_BODY_MIN_BYTES
    soft_404 = any(m in body_lower for m in SOFT_404_MARKERS)
    reasons = []
    if too_short:
        reasons.append(f"body only {len(body)} bytes (<{URL_BODY_MIN_BYTES}); likely error page")
    if soft_404:
        reasons.append("body contains soft-404 marker")
    if not title_present:
        reasons.append("title not found in page")
    if not author_present:
        reasons.append("author surname not found in page")
    ok = title_present and author_present and not too_short and not soft_404
    return {
        "ok": ok,
        "title_in_page": title_present,
        "author_in_page": author_present,
        "body_size": len(body),
        "soft_404": soft_404,
        "reason": "; ".join(reasons) if reasons else "",
    }


# ---------- main ----------

def main():
    text = BIB.read_text()
    entries = parse_bib(text)
    print(f"Parsed {len(entries)} entries from {BIB.name}\n")

    # Collect all S2 lookup IDs into one batch
    s2_ids = []
    for e in entries:
        if e.get("eprint"):
            s2_ids.append(f"arXiv:{e['eprint']}")
        elif e.get("doi"):
            if e["doi"].startswith("10.48550/"):
                arxiv_part = e["doi"].split("arXiv.")[-1]
                s2_ids.append(f"arXiv:{arxiv_part}")
            else:
                s2_ids.append(f"DOI:{e['doi']}")
    print(f"Batch-fetching {len(s2_ids)} papers from Semantic Scholar...")
    s2_cache = fetch_semanticscholar_batch(s2_ids)
    n_found = sum(1 for v in s2_cache.values() if v and "_error" not in v)
    print(f"Got {n_found}/{len(s2_ids)} papers back from S2.\n")

    # ---- bib-level sanity checks (independent of S2) ----
    bib_sanity = {}  # key -> list of issue strings
    for e in entries:
        key = e["key"]
        issues = []
        eprint = e.get("eprint", "")
        doi = e.get("doi", "")
        # eprint vs arXiv-DOI consistency
        if eprint and doi.startswith("10.48550/arXiv."):
            doi_arxiv = doi.split("arXiv.")[-1]
            if doi_arxiv != eprint:
                issues.append(f"DOI claims arXiv={doi_arxiv} but eprint={eprint} (mismatch)")
        # cite-key year vs entry year
        bib_year = e.get("year", "")
        key_year = extract_year_from_key(key)
        if bib_year and bib_year.isdigit() and key_year:
            if abs(int(bib_year) - key_year) > 1:
                issues.append(f"cite-key='{key}' year-prefix={key_year} but bib year={bib_year}")
        if issues:
            bib_sanity[key] = issues

    rows = []
    for e in entries:
        key = e["key"]
        title = e.get("title", "")
        eprint = e.get("eprint", "")
        doi = e.get("doi", "")
        url = e.get("url", "")
        surname = first_author_surname(e.get("author", ""))

        all_surnames = all_author_surnames(e.get("author", ""))
        if eprint:
            check = ("S2/arXiv", eprint,
                     check_via_semanticscholar(f"arXiv:{eprint}", s2_cache, e, all_surnames))
        elif doi:
            if doi.startswith("10.48550/"):
                arxiv_part = doi.split("arXiv.")[-1]
                check = ("S2/arXiv", arxiv_part,
                         check_via_semanticscholar(f"arXiv:{arxiv_part}", s2_cache, e, all_surnames))
            else:
                check = ("S2/DOI", doi,
                         check_via_semanticscholar(f"DOI:{doi}", s2_cache, e, all_surnames))
        elif url:
            check = ("url", url, check_url(url, title, surname))
        else:
            check = ("none", "", {"ok": None, "reason": "no eprint, doi, or url"})
        rows.append((key, *check))

    # ---- report ----
    width_key = max(len(r[0]) for r in rows) + 2
    print(f"{'':2} {'KEY':<{width_key}} {'SOURCE':<8} DETAIL")
    print("-" * 110)

    n_pass, n_fail, n_skip = 0, 0, 0
    venue_warns = []
    author_warns = []
    year_warns = []
    swap_alerts = []
    fails = []
    for key, source, ident, res in rows:
        ok = res.get("ok")
        if ok is True:
            mark, n_pass = "✓", n_pass + 1
        elif ok is False:
            mark, n_fail = "✗", n_fail + 1
        else:
            mark, n_skip = "?", n_skip + 1
        sim = res.get("title_sim")
        sim_str = f"sim={sim:.2f}" if sim is not None else (
            "page-match" if res.get("title_in_page") else ""
        )
        v_status = res.get("venue_status", "skip")
        a_status = res.get("author_list_status", "skip")
        y_status = res.get("year_status", "skip")
        v_marker = {"pass": "v✓", "warn": "v⚠", "fail": "v✗", "skip": "v–"}.get(v_status, "v?")
        a_marker = {"pass": "a✓", "warn": "a⚠", "fail": "a✗", "skip": "a–"}.get(a_status, "a?")
        y_marker = {"pass": "y✓", "warn": "y⚠", "fail": "y✗", "skip": "y–"}.get(y_status, "y?")
        b_marker = "b⚠" if key in bib_sanity else "  "
        s_marker = "swap!" if res.get("swap_detected") else ""
        f_marker = "fb" if res.get("arxiv_fallback_used") else ""
        flags = " ".join(filter(None, [v_marker, a_marker, y_marker, b_marker, s_marker, f_marker]))
        if ok is False and "reason" in res:
            detail = f"{ident[:40]:<40}  {res['reason']}"
        elif ok is False:
            api_t = res.get("api_title", "")[:55]
            detail = f"{ident[:40]:<40}  {sim_str}  {flags}  api_title='{api_t}'"
        else:
            detail = f"{ident[:40]:<40}  {sim_str}  {flags}"
        if v_status in ("warn", "fail"):
            venue_warns.append((key, res))
        if a_status in ("warn", "fail"):
            author_warns.append((key, res))
        if y_status in ("warn", "fail"):
            year_warns.append((key, res))
        if res.get("swap_detected"):
            swap_alerts.append((key, res))
        if ok is False:
            fails.append((key, source, ident, res))
        print(f"{mark:2} {key:<{width_key}} {source:<8} {detail}")

    print("\n" + "=" * 110)
    print(f"PASS: {n_pass}    FAIL: {n_fail}    UNVERIFIED: {n_skip}    TOTAL: {len(rows)}")
    print(f"Legend:  v=venue  a=authors  y=year  b=bib-sanity  swap!=first/last name swap  fb=arXiv-title fallback used")

    if swap_alerts:
        print(f"\n!! NAME-SWAP ALERTS ({len(swap_alerts)}) — first author has comma-form swap:")
        for key, res in swap_alerts:
            print(f"  [{key}]  bib_first_author surname='{res.get('expected_surname')}'  S2='{res.get('api_first_author')}'")

    if bib_sanity:
        print(f"\nBib-level sanity issues ({len(bib_sanity)}):")
        for k, issues in bib_sanity.items():
            for iss in issues:
                print(f"  [{k}]  {iss}")

    if year_warns:
        print(f"\nYear issues ({len(year_warns)}):")
        for key, res in year_warns:
            print(f"  [{key}]  status={res.get('year_status')}  detail={res.get('year_detail')}  bib={res.get('bib_year')} S2={res.get('s2_year')}")

    if author_warns:
        print(f"\nAuthor-list issues ({len(author_warns)}):")
        for key, res in author_warns:
            n_b = res.get("n_bib_authors", 0)
            n_a = res.get("n_api_authors", 0)
            mismatches = res.get("position_mismatches", [])
            line = f"  [{key}]  bib={n_b} S2={n_a}"
            if mismatches:
                line += f"  position mismatches:"
                for i, bn, an in mismatches[:3]:
                    line += f"\n      pos {i}: bib='{bn}' S2='{an}'"
                if len(mismatches) > 3:
                    line += f"\n      ... +{len(mismatches)-3} more"
            print(line)

    if venue_warns:
        print(f"\nVenue issues ({len(venue_warns)}):")
        for key, res in venue_warns:
            print(f"  [{key}]  status={res.get('venue_status')}  {res.get('venue_detail', '')}")

    if fails:
        print("\nFailures (full detail):")
        for key, source, ident, res in fails:
            print(f"\n  [{key}] source={source} ident={ident}")
            for k, v in res.items():
                print(f"    {k}: {v}")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
