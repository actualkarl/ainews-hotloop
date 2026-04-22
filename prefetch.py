#!/usr/bin/env python3.13
"""
prefetch.py — ainews pre-fetcher

Runs at 05:30 NZT daily (30 min before the 06:00 ainews routine).
Fetches RSS feeds and website scrapes in parallel using a tiered strategy,
deduplicates against existing items.json, and writes prefetch-candidates.json.

Fetch strategy (reduces ~25 sources → ~10-12 effective fetches):
  Phase 1 — Meta-aggregators (Techmeme, HN): fetch first, collect their URLs.
  Phase 2 — T1 lab blogs: fetch, skip items already covered by meta-aggregators.
  Phase 3 — T2 commentary (Simon Willison, Import AI, Interconnects, Latent
             Space): SKIPPED — Techmeme already picks these up. Check weekly.

Does NOT handle X/Twitter searches or NZ Grok searches — those still need Claude.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_DIR = Path(__file__).parent
ITEMS_JSON = REPO_DIR / "public" / "data" / "items.json"
CANDIDATES_JSON = REPO_DIR / "prefetch-candidates.json"

# ── Source registry ────────────────────────────────────────────────────────────
# Phase 1: meta-aggregators — fetch first, their URLs seed the dedup set
META_AGGREGATOR_SOURCES = [
    {"url": "https://www.techmeme.com/feed.xml",
     "name": "techmeme",   "tier": 2, "region": "us"},
    {"url": "https://hnrss.org/frontpage?points=150&q=AI+OR+LLM+OR+Claude+OR+GPT+OR+Anthropic+OR+OpenAI+OR+Gemini",
     "name": "hackernews", "tier": 2, "region": "us"},
]

# Phase 2: T1 lab blogs — fetched after meta-aggregators, deduped against them
T1_LAB_SOURCES = [
    # anthropic + mistral omitted — they post on X, caught via X handles
    {"url": "https://openai.com/blog/rss.xml",
     "name": "openai",       "tier": 1, "region": "us"},
    {"url": "https://deepmind.google/blog/rss.xml",
     "name": "deepmind",     "tier": 1, "region": "us"},
    {"url": "https://blog.google/technology/ai/rss/",
     "name": "google-ai",    "tier": 1, "region": "us"},
    {"url": "https://blogs.microsoft.com/ai/feed/",
     "name": "microsoft-ai", "tier": 1, "region": "us"},
    {"url": "https://huggingface.co/blog/feed.xml",
     "name": "huggingface",  "tier": 1, "region": "us"},
    {"url": "https://blogs.nvidia.com/feed/",
     "name": "nvidia",       "tier": 1, "region": "us"},
    {"url": "https://developer.nvidia.com/blog/feed/",
     "name": "nvidia-dev",   "tier": 1, "region": "us"},
]

# Phase 3: T2 commentary — SKIPPED (Techmeme picks these up)
# simonwillison, importai, interconnects, latentspace — check weekly instead.
SKIPPED_SOURCES = ["simonwillison", "importai", "interconnects", "latentspace"]

# Website scrapes — all T1 labs not covered by RSS
WEB_SOURCES = [
    {"url": "https://ai.meta.com/blog/",
     "name": "meta-ai",      "tier": 1, "region": "us"},
    {"url": "https://x.ai/news",
     "name": "xai",          "tier": 1, "region": "us"},
    {"url": "https://blogs.nvidia.com/blog/category/generative-ai/",
     "name": "nvidia-genai", "tier": 1, "region": "us"},
    # deepseek omitted — posts on X, caught via X handles
    {"url": "https://qwen.readthedocs.io/en/latest/",
     "name": "qwen",         "tier": 1, "region": "cn"},
]

STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "with", "in", "on", "by",
    "is", "are", "and", "or", "it", "its", "at", "as", "be", "has",
}

REQUEST_TIMEOUT = 20
MAX_WORKERS = 10
ATOM_NS = "http://www.w3.org/2005/Atom"
MEDIA_NS = "http://search.yahoo.com/mrss/"


# ── Helpers ────────────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def tokenize(text: str) -> set:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    union = len(ta | tb)
    return len(ta & tb) / union if union else 0.0


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    return None


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class _OGImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_image: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and self.og_image is None:
            d = dict(attrs)
            if d.get("property") == "og:image":
                url = d.get("content", "").strip()
                if url:
                    self.og_image = url


def fetch_og_image(url: str) -> str | None:
    """Fetch an article page and extract its og:image URL (reads first 64KB only)."""
    try:
        resp = requests.get(url, timeout=8, headers=_HEADERS)
        resp.raise_for_status()
        chunk = resp.content[:65536].decode("utf-8", errors="ignore")
        parser = _OGImageParser()
        parser.feed(chunk)
        return parser.og_image
    except Exception:
        return None


def _extract_rss_image(el) -> str | None:
    """Extract image URL from an RSS/Atom item element via media:thumbnail, media:content, or enclosure."""
    thumb = el.find(f"{{{MEDIA_NS}}}thumbnail")
    if thumb is not None:
        url = thumb.get("url", "").strip()
        if url:
            return url
    for content in el.findall(f"{{{MEDIA_NS}}}content"):
        medium = content.get("medium", "")
        mtype = content.get("type", "")
        url = content.get("url", "").strip()
        if url and (medium == "image" or mtype.startswith("image/")):
            return url
    enc = el.find("enclosure")
    if enc is not None:
        mtype = enc.get("type", "")
        url = enc.get("url", "").strip()
        if url and mtype.startswith("image/"):
            return url
    return None


# ── RSS / Atom parser ──────────────────────────────────────────────────────────

def _make_item(title: str, url: str, pub_dt: datetime | None, summary: str, source: dict,
               image_url: str | None = None) -> dict:
    return {
        "title": title,
        "url": url,
        "published_at": pub_dt.isoformat().replace("+00:00", "Z") if pub_dt else None,
        "summary": summary,
        "image_url": image_url,
        "source_name": source["name"],
        "source_tier": source["tier"],
        "source_region": source["region"],
    }


def parse_rss_xml(content: bytes, source: dict, window_start: datetime) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"XML parse error: {e}") from e

    tag = root.tag
    is_atom = ATOM_NS in tag or tag.endswith("}feed")

    if is_atom:
        ns = {"a": ATOM_NS}
        for entry in root.findall("a:entry", ns):
            title_el = entry.find("a:title", ns)
            link_el = entry.find("a:link", ns)
            pub_el = entry.find("a:published", ns)
            if pub_el is None:
                pub_el = entry.find("a:updated", ns)
            summary_el = entry.find("a:summary", ns)
            if summary_el is None:
                summary_el = entry.find("a:content", ns)

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.get("href", "") if link_el is not None else "").strip()
            pub_raw = (pub_el.text or "").strip() if pub_el is not None else None
            summary = strip_html((summary_el.text or "") if summary_el is not None else "")[:500]

            if not title or not link:
                continue
            pub_dt = parse_date(pub_raw)
            if pub_dt and pub_dt < window_start:
                continue
            items.append(_make_item(title, link, pub_dt, summary, source, _extract_rss_image(entry)))
    else:
        # RSS 2.0
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            desc_el = item.find("description")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pub_raw = (pub_el.text or "").strip() if pub_el is not None else None
            summary = strip_html((desc_el.text or "") if desc_el is not None else "")[:500]

            if not title or not link:
                continue
            pub_dt = parse_date(pub_raw)
            if pub_dt and pub_dt < window_start:
                continue
            items.append(_make_item(title, link, pub_dt, summary, source, _extract_rss_image(item)))

    return items


# ── Web scraper (basic HTML link extraction) ──────────────────────────────────

class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self._href = v
                    self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            text = re.sub(r"\s+", " ", " ".join(self._buf)).strip()
            if len(text) >= 20:
                self.links.append((self._href, text))
            self._href = None
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)


def scrape_website(html: str, source: dict, base_url: str) -> list[dict]:
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass

    seen: set[str] = set()
    items = []
    for href, text in parser.links:
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if not href.startswith("http"):
            continue
        if href in seen or len(text) < 25:
            continue
        seen.add(href)
        items.append({
            "title": text[:200],
            "url": href,
            "published_at": None,
            "summary": "",
            "source_name": source["name"],
            "source_tier": source["tier"],
            "source_region": source["region"],
        })
        if len(items) >= 10:
            break
    return items


# ── Fetchers ───────────────────────────────────────────────────────────────────

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ainews-prefetcher/1.0)"}


def fetch_rss(source: dict, window_start: datetime) -> tuple[str, list[dict], str | None]:
    try:
        resp = requests.get(source["url"], timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        return source["name"], parse_rss_xml(resp.content, source, window_start), None
    except Exception as e:
        return source["name"], [], str(e)


def fetch_web(source: dict) -> tuple[str, list[dict], str | None]:
    try:
        resp = requests.get(source["url"], timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        return source["name"], scrape_website(resp.text, source, source["url"]), None
    except Exception as e:
        return source["name"], [], str(e)


# ── Dedup ──────────────────────────────────────────────────────────────────────

def dedup(
    raw_items: list[dict],
    existing_items: list[dict],
    skip_url_hashes: set[str] | None = None,
) -> tuple[list[dict], dict]:
    existing_ids = {item["id"] for item in existing_items if "id" in item}
    cutoff_48h = datetime.now(timezone.utc) - timedelta(hours=48)
    recent_existing = [
        item for item in existing_items
        if parse_date(item.get("published_at") or item.get("first_seen_at")) and
           parse_date(item.get("published_at") or item.get("first_seen_at")) >= cutoff_48h
    ]
    meta_skips = skip_url_hashes or set()

    stats = {
        "total_fetched": len(raw_items),
        "duplicates_removed": 0,
        "meta_aggregator_skips": 0,
        "candidates_remaining": 0,
    }
    survivors: list[dict] = []
    batch_ids: set[str] = set()
    batch_titles: list[str] = []

    for item in raw_items:
        uid = url_hash(item["url"])

        # Skip items already covered by meta-aggregators (Phase 1 dedup)
        if uid in meta_skips:
            stats["meta_aggregator_skips"] += 1
            stats["duplicates_removed"] += 1
            continue

        # URL hash against existing items.json
        if uid in existing_ids:
            stats["duplicates_removed"] += 1
            continue

        # Title Jaccard against existing (last 48h)
        dropped = False
        for ex in recent_existing:
            if jaccard(item["title"], ex.get("title", "")) >= 0.7:
                stats["duplicates_removed"] += 1
                dropped = True
                break
        if dropped:
            continue

        # URL dedup within this batch
        if uid in batch_ids:
            stats["duplicates_removed"] += 1
            continue

        # Title Jaccard within this batch
        dropped = False
        for seen_title in batch_titles:
            if jaccard(item["title"], seen_title) >= 0.7:
                stats["duplicates_removed"] += 1
                dropped = True
                break
        if dropped:
            continue

        batch_ids.add(uid)
        batch_titles.append(item["title"])
        survivors.append(item)

    stats["candidates_remaining"] = len(survivors)
    return survivors, stats


# ── Source health ──────────────────────────────────────────────────────────────

def update_health(health: dict, name: str, error: str | None) -> None:
    if name not in health:
        health[name] = {"status": "ok", "consecutive_failures": 0, "last_error": None}
    entry = health[name]
    if error:
        entry["status"] = "failed"
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["last_error"] = error
    else:
        entry["status"] = "ok"
        entry["consecutive_failures"] = 0
        entry["last_error"] = None


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=24)
    print(f"[prefetch] start {now.isoformat()}", flush=True)
    print(f"[prefetch] skipping T2 commentary: {SKIPPED_SOURCES}", flush=True)

    # Load existing items
    existing_items: list[dict] = []
    if ITEMS_JSON.exists():
        try:
            data = json.loads(ITEMS_JSON.read_text())
            existing_items = data.get("items") or []
            print(f"[prefetch] loaded {len(existing_items)} existing items", flush=True)
        except Exception as e:
            print(f"[prefetch] WARNING: items.json unreadable: {e}", flush=True)

    # Carry forward source health from previous run
    source_health: dict = {}
    if CANDIDATES_JSON.exists():
        try:
            source_health = json.loads(CANDIDATES_JSON.read_text()).get("source_health", {})
        except Exception:
            pass

    # ── Phase 1: Meta-aggregators (Techmeme + HN) ─────────────────────────────
    print(f"[prefetch] phase 1: fetching {len(META_AGGREGATOR_SOURCES)} meta-aggregators...", flush=True)
    meta_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_rss, src, window_start): src for src in META_AGGREGATOR_SOURCES}
        for fut in as_completed(futs):
            name, items, err = fut.result()
            update_health(source_health, name, err)
            status = f"FAILED — {err}" if err else f"{len(items)} items"
            print(f"[prefetch] rss/{name}: {status}", flush=True)
            meta_items.extend(items)

    # Collect meta-aggregator URL hashes — used to skip duplicates in phase 2
    meta_url_hashes = {url_hash(item["url"]) for item in meta_items}
    print(f"[prefetch] phase 1 complete: {len(meta_items)} items, {len(meta_url_hashes)} unique URLs", flush=True)

    # ── Phase 2: T1 lab blogs ─────────────────────────────────────────────────
    print(f"[prefetch] phase 2: fetching {len(T1_LAB_SOURCES)} T1 lab RSS feeds...", flush=True)
    t1_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_rss, src, window_start): src for src in T1_LAB_SOURCES}
        for fut in as_completed(futs):
            name, items, err = fut.result()
            update_health(source_health, name, err)
            # Filter out items already in meta-aggregators before reporting
            new_only = [i for i in items if url_hash(i["url"]) not in meta_url_hashes]
            status = f"FAILED — {err}" if err else f"{len(items)} items ({len(new_only)} not in meta)"
            print(f"[prefetch] rss/{name}: {status}", flush=True)
            t1_items.extend(items)

    # ── Phase 3: T2 commentary skipped ───────────────────────────────────────
    print(f"[prefetch] phase 3: skipping {SKIPPED_SOURCES} (covered by Techmeme)", flush=True)

    # ── Website scrapes ────────────────────────────────────────────────────────
    print(f"[prefetch] fetching {len(WEB_SOURCES)} websites...", flush=True)
    web_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_web, src): src for src in WEB_SOURCES}
        for fut in as_completed(futs):
            name, items, err = fut.result()
            update_health(source_health, name, err)
            status = f"FAILED — {err}" if err else f"{len(items)} items"
            print(f"[prefetch] web/{name}: {status}", flush=True)
            web_items.extend(items)

    # Combine: meta-aggregator items come first (highest priority)
    # T1 items deduplicated against meta URL hashes
    raw_items = meta_items + t1_items + web_items
    print(f"[prefetch] total raw: {len(raw_items)} items", flush=True)

    # ── Dedup ─────────────────────────────────────────────────────────────────
    print(f"[prefetch] deduplicating...", flush=True)
    candidates, dedup_stats = dedup(raw_items, existing_items, skip_url_hashes=meta_url_hashes)
    print(f"[prefetch] dedup stats: {dedup_stats}", flush=True)

    # ── og:image enrichment (fallback for items with no RSS image) ────────────
    missing_img = [c for c in candidates
                   if not c.get("image_url") and "x.com" not in c.get("url", "")]
    if missing_img:
        print(f"[prefetch] fetching og:image for {len(missing_img)} items...", flush=True)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(fetch_og_image, item["url"]): item for item in missing_img}
            enriched = 0
            for fut in as_completed(futs):
                item = futs[fut]
                og_url = fut.result()
                if og_url:
                    item["image_url"] = og_url
                    enriched += 1
        print(f"[prefetch] og:image enriched {enriched}/{len(missing_img)} items", flush=True)

    # ── Write output atomically ───────────────────────────────────────────────
    output = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "candidates": candidates,
        "source_health": source_health,
        "existing_item_count": len(existing_items),
        "dedup_stats": dedup_stats,
        "skipped_sources": SKIPPED_SOURCES,
    }
    tmp = CANDIDATES_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(output, indent=2))
    tmp.replace(CANDIDATES_JSON)
    print(f"[prefetch] wrote {len(candidates)} candidates → {CANDIDATES_JSON}", flush=True)
    print(f"[prefetch] done {datetime.now(timezone.utc).isoformat()}", flush=True)


def backfill_items():
    """Enrich existing items.json entries that are missing image_url via og:image scraping."""
    if not ITEMS_JSON.exists():
        print("[backfill] items.json not found", flush=True)
        return
    data = json.loads(ITEMS_JSON.read_text())
    items = data.get("items") or []
    missing = [item for item in items
               if not item.get("image_url") and "x.com" not in item.get("url", "")]
    print(f"[backfill] {len(missing)} items missing image_url (of {len(items)} total)", flush=True)
    if not missing:
        print("[backfill] nothing to do", flush=True)
        return
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(fetch_og_image, item["url"]): item for item in missing}
        enriched = 0
        for fut in as_completed(futs):
            item = futs[fut]
            og_url = fut.result()
            if og_url:
                item["image_url"] = og_url
                enriched += 1
                print(f"[backfill] ✓ {item['url'][:60]} → {og_url[:60]}", flush=True)
            else:
                print(f"[backfill] ✗ {item['url'][:60]}", flush=True)
    print(f"[backfill] enriched {enriched}/{len(missing)} items", flush=True)
    tmp = ITEMS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(ITEMS_JSON)
    print(f"[backfill] wrote {ITEMS_JSON}", flush=True)


if __name__ == "__main__":
    import sys
    if "--backfill-items" in sys.argv:
        backfill_items()
    else:
        main()
