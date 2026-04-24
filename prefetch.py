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

import base64
import hashlib
import json
import os
import re
import time
import tomllib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_DIR = Path(__file__).parent
ITEMS_JSON = REPO_DIR / "public" / "data" / "items.json"
CANDIDATES_JSON = REPO_DIR / "prefetch-candidates.json"
COVER_IMAGE = REPO_DIR / "public" / "data" / "cover.png"
COVER_IMAGE_MOBILE = REPO_DIR / "public" / "data" / "cover-mobile.png"
COVER_META = REPO_DIR / "public" / "data" / "cover-meta.json"
SLICES_DIR = REPO_DIR / "public" / "data" / "slices"
SECRETS_ENV = Path.home() / ".openclaw" / "ainews-secrets.env"
OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"

# Slice geometry — must match the Polaroid grid in COVER_PROMPT.
SLICE_COLS = 3
SLICE_ROWS = 2
SLICE_COUNT = SLICE_COLS * SLICE_ROWS  # 6 Polaroids, one per cell

# og:image enrichment: per-item timeout + total wall-clock cap so one bad batch
# of slow domains never blocks the whole run.
ENRICH_PER_ITEM_TIMEOUT = 10
ENRICH_TOTAL_BUDGET_S = 60
ENRICH_USER_AGENT = "Mozilla/5.0 AINewsBot/1.0"

# ── Daily cover-banner config ─────────────────────────────────────────────────
# ONE image per day, generated off the top trending candidate.
# Primary: gpt-image-2 low 1536x1024 ≈ $0.006/image = $2.19/year.
# Fallbacks (all reach for the same visual slot):
#   gpt-image-1 low 1536x1024 ≈ $0.011, dall-e-3 standard 1792x1024 ≈ $0.080.
# If ALL image-gen fails, caller should fall back to the existing SVG cover.

COVER_TIMEOUT = 120
COVER_PRIMARY = {"model": "gpt-image-2", "size": "1536x1024", "quality": "low"}
COVER_FALLBACKS = [
    {"model": "gpt-image-1", "size": "1536x1024", "quality": "low"},
    {"model": "gpt-image-1", "size": "1024x1024", "quality": "low"},
    {"model": "dall-e-3", "size": "1792x1024", "quality": "standard"},
]
COVER_PROMPT_TEMPLATE = (
    "Editorial mood-board photograph, detective-investigation aesthetic. "
    "Dark corkboard or black velvet background with subtle texture. "
    "Six Polaroid-style instant photos arranged in a rough 3×2 grid, "
    "slightly rotated at varied angles (-10° to +10°), corners overlapping, "
    "pinned with gold/brass pushpins. Red investigation string loosely connects "
    "some photos. Each Polaroid has a handwritten-in-black-marker label on its "
    "white bottom strip.\n\n"
    "Polaroid labels (top-left → bottom-right, render each EXACTLY as written):\n"
    "{labels_block}\n\n"
    "For each Polaroid, render a minimal editorial illustration that visually "
    "captures the theme of its label — abstract motifs, objects, or metaphors. "
    "Muted palette — warm cream Polaroid frames, rose (#FF5B6E) and teal (#14B8BF) "
    "hints naturally in the photo subjects and the string. Slight chaos and grain; "
    "informality is the point. Shot from directly above, flat lay. 3:2 landscape.\n\n"
    "No real human faces or identifiable likenesses. No real brand logos or "
    "trademarked symbols. No text outside the handwritten labels."
)

# Max chars per Polaroid label — long headlines get truncated with ellipsis so
# the model can render them legibly on a Polaroid's white bottom strip.
LABEL_MAX_CHARS = 55


def _shorten_label(title: str) -> str:
    """Trim a headline to fit a Polaroid label. Preserves word boundaries."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if len(t) <= LABEL_MAX_CHARS:
        return t
    # Cut at last space before the limit, append ellipsis.
    cut = t[: LABEL_MAX_CHARS - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def _build_cover_prompt(titles: list[str]) -> str:
    """Assemble the Polaroid-banner prompt for the given headlines.

    Expects up to 6 titles. If fewer are supplied, pads with generic
    "AI news update" labels so the 3×2 grid is still complete."""
    labels = [_shorten_label(t) for t in (titles or [])[:SLICE_COUNT]]
    while len(labels) < SLICE_COUNT:
        labels.append("AI news update")
    block = "\n".join(f'{i+1}. "{lbl}"' for i, lbl in enumerate(labels))
    return COVER_PROMPT_TEMPLATE.format(labels_block=block)

# ── Cloudflare KV config ───────────────────────────────────────────────────────

CF_ACCOUNT_ID = "97f9fae52c8c337245f0c1cfff7e5cd3"
KV_NAMESPACE_ID = "491c62f3f6a94b2185edc68a7bf0f30a"
KV_KEY = "prefetch-candidates"


def _cf_token() -> str | None:
    """Return a Cloudflare bearer token. Checks env first, then wrangler config."""
    token = __import__("os").environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    wrangler_config = Path.home() / ".wrangler" / "config" / "default.toml"
    if wrangler_config.exists():
        try:
            cfg = tomllib.loads(wrangler_config.read_text())
            return cfg.get("oauth_token")
        except Exception:
            pass
    return None


def kv_write(payload_json: str) -> bool:
    """Write payload_json to the AINEWS KV namespace. Returns True on success."""
    token = _cf_token()
    if not token:
        print("[prefetch] KV: no auth token found — skipping KV write", flush=True)
        return False
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{KV_KEY}"
    )
    try:
        resp = requests.put(
            url,
            data=payload_json.encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.ok:
            return True
        print(f"[prefetch] KV write failed: {resp.status_code} {resp.text[:200]}", flush=True)
        return False
    except Exception as e:
        print(f"[prefetch] KV write error: {e}", flush=True)
        return False

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


def slugify(text: str, max_len: int = 60) -> str:
    """URL-safe slug from text, truncated to max_len."""
    t = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return t[:max_len].rstrip("-") or "item"


def _openai_key() -> str | None:
    """Resolve the OpenAI API key. Order: env → secrets.env → openclaw.json."""
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k.strip()
    # secrets.env (simple `export OPENAI_API_KEY="..."` style)
    try:
        if SECRETS_ENV.exists():
            for line in SECRETS_ENV.read_text().splitlines():
                m = re.match(r'\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*"?([^"\s]+)"?', line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    # openclaw.json fallback
    try:
        if OPENCLAW_JSON.exists():
            d = json.loads(OPENCLAW_JSON.read_text())
            v = d.get("models", {}).get("providers", {}).get("openai", {}).get("apiKey")
            if v:
                return v.strip()
    except Exception:
        pass
    return None


def _openai_org() -> str | None:
    """Resolve the OpenAI organization ID (optional).
    Order: env → secrets.env → openclaw.json. When present, gets sent as the
    OpenAI-Organization header so every request is explicitly attributed to
    the intended org. Absence is fine — API uses the account's default org."""
    v = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")
    if v:
        return v.strip()
    try:
        if SECRETS_ENV.exists():
            for line in SECRETS_ENV.read_text().splitlines():
                m = re.match(r'\s*(?:export\s+)?OPENAI_ORG(?:_ID|ANIZATION)?\s*=\s*"?([^"\s]+)"?', line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    try:
        if OPENCLAW_JSON.exists():
            d = json.loads(OPENCLAW_JSON.read_text())
            v2 = d.get("models", {}).get("providers", {}).get("openai", {}).get("organization")
            if v2:
                return v2.strip()
    except Exception:
        pass
    return None


def generate_cover(titles: list[str], api_key: str, out_path: Path = COVER_IMAGE) -> tuple[bool, str | None, str | None]:
    """Generate the daily Polaroid-mood-board banner. Tries gpt-image-2 →
    gpt-image-1 → dall-e-3. Writes to out_path. Accepts up to 6 headlines
    (the top stories of the day) and uses them as the Polaroid labels.
    Returns (success, model_used, error_message)."""
    clean_titles = [t.replace('"', "").replace("'", "").strip() for t in (titles or []) if t]
    prompt = _build_cover_prompt(clean_titles)
    org_id = _openai_org()
    last_err: str | None = None
    for cfg in [COVER_PRIMARY, *COVER_FALLBACKS]:
        body: dict = {"model": cfg["model"], "prompt": prompt,
                      "size": cfg["size"], "n": 1}
        if cfg["model"].startswith("dall-e"):
            body["response_format"] = "b64_json"
        if "quality" in cfg:
            body["quality"] = cfg["quality"]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if org_id:
            headers["OpenAI-Organization"] = org_id
        try:
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json=body,
                timeout=COVER_TIMEOUT,
            )
            if resp.status_code in (400, 403, 404):
                last_err = f"{cfg['model']} {cfg['size']}: HTTP {resp.status_code}"
                continue
            if not resp.ok:
                last_err = f"{cfg['model']} {cfg['size']}: HTTP {resp.status_code} {resp.text[:200]}"
                continue
            payload = resp.json()
            data = payload.get("data", [])
            if not data:
                last_err = f"{cfg['model']}: empty data"
                continue
            entry = data[0]
            b64 = entry.get("b64_json")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if b64:
                out_path.write_bytes(base64.b64decode(b64))
            elif entry.get("url"):
                img_resp = requests.get(entry["url"], timeout=COVER_TIMEOUT)
                img_resp.raise_for_status()
                out_path.write_bytes(img_resp.content)
            else:
                last_err = f"{cfg['model']}: no b64 or url in response"
                continue
            label = f"{cfg['model']} {cfg['size']} {cfg.get('quality','')}".strip()
            return True, label, None
        except Exception as e:
            last_err = f"{cfg['model']}: {e}"
            continue
    return False, None, last_err


def cover_was_written_today() -> bool:
    """Legacy mtime idempotency — retained for --cover-only convenience but
    NOT used by main(). main() now decides via headline-diff (see
    read_cover_meta / write_cover_meta). This check was too strict because it
    skipped regen whenever the news moved within a single UTC day."""
    if not COVER_IMAGE.exists():
        return False
    mtime = datetime.fromtimestamp(COVER_IMAGE.stat().st_mtime, tz=timezone.utc)
    return mtime.date() == datetime.now(timezone.utc).date()


def read_cover_meta() -> dict:
    """Read cover-meta.json. Returns {} on any error (file missing, corrupt)."""
    try:
        if COVER_META.exists():
            return json.loads(COVER_META.read_text())
    except Exception:
        pass
    return {}


def write_cover_meta(meta: dict) -> None:
    """Persist cover-meta.json atomically. Never raises."""
    try:
        tmp = COVER_META.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        tmp.replace(COVER_META)
    except Exception as e:
        print(f"[prefetch] cover-meta write failed: {e}", flush=True)


def _cover_headline_norm(title: str) -> str:
    """Normalize a headline for stable comparison: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


def crop_cover_to_mobile(
    src: Path = COVER_IMAGE,
    dst: Path = COVER_IMAGE_MOBILE,
    target: int = 1024,
) -> tuple[bool, str | None]:
    """Center-crop the daily cover.png to a mobile-friendly square PNG.

    Source is typically 1536×1024 (gpt-image-2). A center-square crop becomes
    1024×1024 — works as a mobile hero in both portrait and landscape viewports;
    the frontend can CSS-crop further if needed.

    Idempotent: if dst already exists and was written on the same UTC day as src,
    skip the re-crop. Returns (success, error_message). Never raises — a crop
    failure should not fail the whole prefetch run.
    """
    if not src.exists():
        return False, f"source {src.name} does not exist"
    if not _PIL_AVAILABLE:
        return False, "Pillow not installed — run `pip3.13 install --break-system-packages Pillow`"

    # Idempotency: if dst is newer than src, the crop is still in sync with
    # the current cover.png. Same-day is NOT enough — a mid-day regen of
    # cover.png needs to trigger a fresh crop.
    if dst.exists():
        src_mtime = src.stat().st_mtime
        dst_mtime = dst.stat().st_mtime
        if dst_mtime >= src_mtime:
            return True, "crop already in sync with cover.png"

    try:
        with Image.open(src) as img:
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            cropped = img.crop((left, top, left + side, top + side))
            if side != target:
                cropped = cropped.resize((target, target), Image.LANCZOS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            # PIL default PNG — preserves source, no recompression artifacts.
            cropped.save(dst, format="PNG", optimize=True)
        return True, None
    except Exception as e:
        return False, f"crop failed: {e}"


def slice_cover_into_tiles(
    src: Path = COVER_IMAGE,
    out_dir: Path = SLICES_DIR,
) -> tuple[bool, list[str], str | None]:
    """Slice the Polaroid cover into 6 tiles (3 cols × 2 rows).

    The cover is prompted to render six Polaroids in a 3×2 grid, so a straight
    grid crop produces one tile per Polaroid (with its white frame, slight
    rotation, and corkboard background all included in the crop — that's a
    feature, not a bug; reinforces the mood-board look when used as a story
    thumbnail).

    Writes `slice-{row}-{col}.png` into `out_dir`. Returns
    (success, list_of_public_urls, error_message). Never raises."""
    if not src.exists():
        return False, [], f"source {src.name} does not exist"
    if not _PIL_AVAILABLE:
        return False, [], "Pillow not installed — run `pip3.13 install --break-system-packages Pillow`"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        urls: list[str] = []
        with Image.open(src) as img:
            W, H = img.size
            cell_w = W // SLICE_COLS
            cell_h = H // SLICE_ROWS
            for row in range(SLICE_ROWS):
                for col in range(SLICE_COLS):
                    box = (
                        col * cell_w,
                        row * cell_h,
                        (col + 1) * cell_w,
                        (row + 1) * cell_h,
                    )
                    tile = img.crop(box)
                    fname = f"slice-{row}-{col}.png"
                    tile.save(out_dir / fname, format="PNG", optimize=True)
                    urls.append(f"/data/slices/{fname}")
        return True, urls, None
    except Exception as e:
        return False, [], f"slice failed: {e}"


def assign_slices_to_items(
    items: list[dict],
    slice_urls: list[str],
) -> int:
    """Assign a cover-slice URL to each item that has no `image_url`.

    Deterministic — same item.url always maps to the same slice across runs,
    so thumbnails are stable until the cover itself regenerates. Items that
    already have an og:image / twitter:image / avatar keep it untouched.

    Returns the number of items that got a slice assigned."""
    if not slice_urls:
        return 0
    assigned = 0
    for item in items:
        if item.get("image_url"):
            continue
        url = item.get("url") or item.get("id") or ""
        if not url:
            continue
        idx = int(hashlib.md5(url.encode("utf-8")).hexdigest(), 16) % len(slice_urls)
        item["image_url"] = slice_urls[idx]
        item["image_source"] = "cover_slice"
        assigned += 1
    return assigned


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
    """Stdlib-only fallback extractor used when bs4 is unavailable.

    Scans for <meta property="og:image">, <meta name="twitter:image">,
    <meta property="og:image:secure_url">. First-article-img fallback is
    skipped here — bs4 path covers it; stdlib falls through to None.
    """
    def __init__(self):
        super().__init__()
        self.found: str | None = None
        self._priority = 99  # lower = better

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        d = {k.lower(): (v or "").strip() for k, v in attrs}
        content = d.get("content", "")
        if not content:
            return
        prop = d.get("property", "").lower()
        name = d.get("name", "").lower()
        # Rank: og:image (0) > twitter:image (1) > og:image:secure_url (2)
        priority = None
        if prop == "og:image":
            priority = 0
        elif name == "twitter:image":
            priority = 1
        elif prop == "og:image:secure_url":
            priority = 2
        if priority is not None and priority < self._priority:
            self.found = content
            self._priority = priority


def _extract_image_bs4(html: str, base_url: str) -> str | None:
    """bs4-based extractor. Tries og:image → twitter:image → og:image:secure_url
    → first <img> inside an <article> tag. Returns absolute URL or None."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None
    # 1. og:image
    for sel in [
        ("meta", {"property": "og:image"}),
        ("meta", {"name": "twitter:image"}),
        ("meta", {"property": "og:image:secure_url"}),
    ]:
        tag = soup.find(*sel)
        if tag and tag.get("content"):
            url = tag.get("content").strip()
            if url:
                return urljoin(base_url, url)
    # 4. first <img> inside an <article>
    article = soup.find("article")
    if article:
        img = article.find("img")
        if img and img.get("src"):
            url = img.get("src").strip()
            if url:
                return urljoin(base_url, url)
    return None


def fetch_og_image(url: str, timeout: float = ENRICH_PER_ITEM_TIMEOUT) -> str | None:
    """Fetch an article page and extract its lead image URL.

    Tries (in order): og:image, twitter:image, og:image:secure_url, first
    <img> in <article>. Uses bs4 when available, else a stdlib fallback.
    Wrapped in try/except — never raises.
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": ENRICH_USER_AGENT},
            allow_redirects=True,
        )
        resp.raise_for_status()
        # Cap read to 256KB — og tags live in <head>; avoids huge pages.
        html = resp.content[:262144].decode(
            resp.encoding or "utf-8", errors="ignore"
        )
        if _BS4_AVAILABLE:
            found = _extract_image_bs4(html, resp.url or url)
            if found:
                return found
        # stdlib fallback (or bs4 returned None)
        parser = _OGImageParser()
        try:
            parser.feed(html)
        except Exception:
            pass
        if parser.found:
            return urljoin(resp.url or url, parser.found)
        return None
    except Exception:
        return None


def enrich_images_bounded(candidates: list[dict]) -> dict:
    """Enrich candidates missing image_url. Hard cap ENRICH_TOTAL_BUDGET_S
    across all items. Returns {"enriched": int, "attempted": int, "skipped_over_budget": int}."""
    missing = [c for c in candidates if not c.get("image_url")]
    stats = {"attempted": 0, "enriched": 0, "skipped_over_budget": 0,
             "total_missing": len(missing)}
    if not missing:
        return stats
    start = time.monotonic()

    def worker(item):
        if time.monotonic() - start > ENRICH_TOTAL_BUDGET_S:
            return item, None, True  # skipped
        try:
            og = fetch_og_image(item["url"])
        except Exception:
            og = None
        return item, og, False

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = [pool.submit(worker, it) for it in missing]
        for fut in as_completed(futs):
            try:
                item, og, skipped = fut.result()
            except Exception:
                continue
            if skipped:
                stats["skipped_over_budget"] += 1
                continue
            stats["attempted"] += 1
            if og:
                item["image_url"] = og
                stats["enriched"] += 1
            if time.monotonic() - start > ENRICH_TOTAL_BUDGET_S:
                # Stop draining — the remaining futures may still resolve but
                # we won't wait on new work. ThreadPool will clean up.
                break
    return stats


def _tweet_handle(url: str) -> str | None:
    """Extract Twitter/X handle from a tweet URL, or None if not a tweet URL."""
    m = re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]+)(?:/|$)', url)
    if m and m.group(1).lower() not in ("i", "home", "search", "explore", "notifications", "intent"):
        return m.group(1)
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
    image_type = None
    if not image_url:
        handle = _tweet_handle(url)
        if handle:
            image_url = f"https://unavatar.io/x/{handle}"
            image_type = "avatar"
    return {
        "title": title,
        "url": url,
        "published_at": pub_dt.isoformat().replace("+00:00", "Z") if pub_dt else None,
        "summary": summary,
        "image_url": image_url,
        "image_type": image_type,
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

    # ── og:image enrichment (100% headless — runs here so Claude routine
    # never needs WebFetch for images). Bounded by ENRICH_TOTAL_BUDGET_S.
    # Tweet items already got avatar URLs set in _make_item; skip them here.
    enrich_stats = enrich_images_bounded(candidates)
    if enrich_stats["total_missing"]:
        print(
            f"[prefetch] og:image enrichment: attempted {enrich_stats['attempted']}, "
            f"enriched {enrich_stats['enriched']}/{enrich_stats['total_missing']}, "
            f"skipped {enrich_stats['skipped_over_budget']} (over {ENRICH_TOTAL_BUDGET_S}s budget), "
            f"bs4={_BS4_AVAILABLE}",
            flush=True,
        )

    # ── Daily cover banner (ONE gpt-image-2 call, headline-diff idempotent) ───
    # Regen whenever the top candidate's headline changes. cover-meta.json
    # tracks the last headline we rendered for; if it matches the current top,
    # we skip (cheap). If different, we regen. At $0.006/regen this stays
    # ~$0.01–0.02/day even when the news moves multiple times.
    cover_status: dict = {"generated": False, "model": None, "error": None}
    slice_urls: list[str] = []
    if candidates:
        # Top 6 headlines — one per Polaroid in the mood-board banner.
        top_titles = [c.get("title", "") for c in candidates[: SLICE_COUNT] if c.get("title")]
        current_title = top_titles[0] if top_titles else "AI news"
        # Headline-diff idempotency: we regen when the SET of top-6 headlines
        # changes, not just the #1. Joining them gives a stable fingerprint.
        current_fingerprint = " || ".join(_cover_headline_norm(t) for t in top_titles)

        prev_meta = read_cover_meta()
        prev_fingerprint = prev_meta.get("top_fingerprint", "") or \
            _cover_headline_norm(prev_meta.get("top_headline", ""))
        cover_png_exists = COVER_IMAGE.exists()

        needs_regen = (
            not cover_png_exists
            or not prev_fingerprint
            or prev_fingerprint != current_fingerprint
        )

        if not needs_regen:
            cover_status = {"generated": False, "reused": True, "model": None,
                            "top_headline": current_title,
                            "top_headlines": top_titles,
                            "error": "top-6 headlines unchanged since last regen"}
            print(f"[prefetch] cover: top-6 headlines unchanged — skipping regen", flush=True)
        else:
            api_key = _openai_key()
            if not api_key:
                cover_status["error"] = "no OpenAI key"
                print(f"[prefetch] cover: no OpenAI key — SVG fallback remains", flush=True)
            else:
                reason = "first run / no prior cover" if not prev_fingerprint else "top-6 headlines changed"
                print(f"[prefetch] cover: regenerating ({reason}) — Polaroid mood-board "
                      f"with {len(top_titles)} labels...", flush=True)
                ok, model_used, err = generate_cover(top_titles, api_key)
                if ok:
                    cover_status.update({"generated": True, "model": model_used,
                                         "title": current_title, "top_headline": current_title,
                                         "top_headlines": top_titles,
                                         "error": None})
                    # Persist what we just rendered for — drives next-run diff
                    write_cover_meta({
                        "top_headline": current_title,
                        "top_headlines": top_titles,
                        "top_fingerprint": current_fingerprint,
                        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "model": model_used,
                        "regen_reason": reason,
                    })
                    print(f"[prefetch] cover ✓ {COVER_IMAGE.name} via {model_used}", flush=True)
                else:
                    cover_status["error"] = err
                    print(f"[prefetch] cover ✗ all models failed: {err}  (SVG fallback remains)",
                          flush=True)

    # ── Mobile-cropped companion (same source, zero extra API cost) ───────────
    # Run whenever cover.png exists — idempotent by same-day mtime.
    mobile_ok, mobile_err = crop_cover_to_mobile()
    if mobile_ok:
        cover_status["mobile_crop"] = "ok" if not mobile_err else mobile_err
        if not mobile_err:
            print(f"[prefetch] cover-mobile ✓ {COVER_IMAGE_MOBILE.name} (1024×1024 center crop)",
                  flush=True)
        else:
            # "already cropped today" — fine.
            print(f"[prefetch] cover-mobile: {mobile_err}", flush=True)
    else:
        cover_status["mobile_crop_error"] = mobile_err
        print(f"[prefetch] cover-mobile ✗ {mobile_err} (desktop cover.png still available)",
              flush=True)

    # ── Slice the cover into 6 Polaroid tiles, then fall back to them for
    #    any item still missing image_url. 100% image coverage, zero extra
    #    API calls. Regens in lockstep with the cover — if the cover was
    #    reused, we also reuse existing slices (still stable, still matching
    #    the current cover.png).
    slice_ok, slice_urls, slice_err = slice_cover_into_tiles()
    if slice_ok:
        cover_status["slices"] = slice_urls
        cover_status["slice_count"] = len(slice_urls)
        print(f"[prefetch] slices ✓ {len(slice_urls)} Polaroid tiles → public/data/slices/",
              flush=True)
        # Assign slice URLs to items missing image_url (deterministic hash map)
        sliced = assign_slices_to_items(candidates, slice_urls)
        if sliced:
            print(f"[prefetch] slice fallback: {sliced} items got a Polaroid thumbnail",
                  flush=True)
    else:
        cover_status["slice_error"] = slice_err
        print(f"[prefetch] slices ✗ {slice_err}", flush=True)

    # Compute final image_url coverage for the Final Report (after slice fallback).
    total_c = len(candidates)
    with_image = sum(1 for c in candidates if c.get("image_url"))
    image_coverage_pct = round(100.0 * with_image / total_c, 1) if total_c else 0.0

    # ── Write output atomically ───────────────────────────────────────────────
    output = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "candidates": candidates,
        "source_health": source_health,
        "existing_item_count": len(existing_items),
        "dedup_stats": dedup_stats,
        "enrich_stats": enrich_stats,
        "image_coverage_pct": image_coverage_pct,
        "skipped_sources": SKIPPED_SOURCES,
        "cover_status": cover_status,
        "cover_image_url": "/data/cover.png" if COVER_IMAGE.exists() else None,
        "cover_image_mobile_url": "/data/cover-mobile.png" if COVER_IMAGE_MOBILE.exists() else None,
        "cover_slice_urls": slice_urls,  # empty list if slicing failed
    }
    payload_json = json.dumps(output, indent=2)
    tmp = CANDIDATES_JSON.with_suffix(".json.tmp")
    tmp.write_text(payload_json)
    tmp.replace(CANDIDATES_JSON)
    print(f"[prefetch] wrote {len(candidates)} candidates → {CANDIDATES_JSON}", flush=True)

    # Write to Cloudflare KV for cloud-routine consumption
    kv_ok = kv_write(payload_json)
    print(f"[prefetch] KV write: {'ok' if kv_ok else 'failed (local file is fallback)'}", flush=True)
    print(f"[prefetch] done {datetime.now(timezone.utc).isoformat()}", flush=True)


def backfill_items():
    """Enrich existing items.json entries that are missing image_url via og:image or unavatar."""
    if not ITEMS_JSON.exists():
        print("[backfill] items.json not found", flush=True)
        return
    data = json.loads(ITEMS_JSON.read_text())
    items = data.get("items") or []

    # Pass 1: tweet items — set avatar from unavatar (no HTTP fetch needed)
    # For /i/status/ URLs the handle isn't in the URL; fall back to item["source"].
    avatar_set = 0
    for item in items:
        if not item.get("image_url"):
            url = item.get("url", "")
            if "x.com" in url or "twitter.com" in url:
                handle = _tweet_handle(url) or item.get("source", "")
                if handle:
                    item["image_url"] = f"https://unavatar.io/x/{handle}"
                    item["image_type"] = "avatar"
                    avatar_set += 1
    print(f"[backfill] set {avatar_set} tweet avatars", flush=True)

    # Pass 2: non-tweet items still missing image_url — fetch og:image
    missing = [item for item in items if not item.get("image_url")]
    print(f"[backfill] {len(missing)} non-tweet items still missing image_url", flush=True)
    enriched = 0
    if missing:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(fetch_og_image, item["url"]): item for item in missing}
            for fut in as_completed(futs):
                item = futs[fut]
                og_url = fut.result()
                if og_url:
                    item["image_url"] = og_url
                    enriched += 1
                    print(f"[backfill] ✓ {item['url'][:60]} → {og_url[:60]}", flush=True)
                else:
                    print(f"[backfill] ✗ {item['url'][:60]}", flush=True)
    print(f"[backfill] og:image enriched {enriched}/{len(missing)} items", flush=True)

    tmp = ITEMS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(ITEMS_JSON)
    print(f"[backfill] wrote {ITEMS_JSON}", flush=True)


def cover_only():
    """Run only the mobile-crop step on the existing cover.png. For testing."""
    if not COVER_IMAGE.exists():
        print(f"[cover-only] {COVER_IMAGE} does not exist — nothing to crop", flush=True)
        return
    ok, err = crop_cover_to_mobile()
    if ok and not err:
        from PIL import Image as _I
        with _I.open(COVER_IMAGE_MOBILE) as im:
            print(f"[cover-only] ✓ wrote {COVER_IMAGE_MOBILE} ({im.size[0]}×{im.size[1]}, mode={im.mode})",
                  flush=True)
    elif ok and err:
        print(f"[cover-only] no-op: {err}", flush=True)
    else:
        print(f"[cover-only] FAILED: {err}", flush=True)


if __name__ == "__main__":
    import sys
    if "--backfill-items" in sys.argv:
        backfill_items()
    elif "--cover-only" in sys.argv:
        cover_only()
    else:
        main()
