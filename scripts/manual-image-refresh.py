#!/usr/bin/env python3.13
"""manual-image-refresh.py — one-off script to regenerate the daily Polaroid
cover banner + 6 slices, then backfill every image-less item in the CURRENT
public/data/items.json with a deterministic slice URL.

Writes:
  public/data/cover.png
  public/data/cover-mobile.png
  public/data/slices/slice-0-0.png … slice-1-2.png
  public/data/items.json  (updated in place, items with image_url=null get slice URLs)
  public/data/cover-meta.json  (tracks top-6 fingerprint for next-run idempotency)

Does NOT touch prefetch-candidates.json or run the RSS fetch step. Does NOT
call the Claude routine. Designed for manual mid-day refresh — tomorrow's
06:00 NZT cron still runs the full pipeline.

Usage:
  cd ~/Projects/Ainews-sitre
  /opt/homebrew/bin/python3.13 scripts/manual-image-refresh.py
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-use prefetch's helpers so we stay in sync with production logic.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from prefetch import (  # type: ignore
    COVER_IMAGE,
    COVER_IMAGE_MOBILE,
    COVER_META,
    ITEMS_JSON,
    SLICE_COUNT,
    SLICES_DIR,
    _cover_headline_norm,
    _openai_key,
    assign_slices_to_items,
    crop_cover_to_mobile,
    generate_cover,
    read_cover_meta,
    slice_cover_into_tiles,
    write_cover_meta,
)


def load_items_json() -> dict:
    with ITEMS_JSON.open() as f:
        return json.load(f)


def save_items_json(data: dict) -> None:
    tmp = ITEMS_JSON.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(ITEMS_JSON)


def main() -> int:
    data = load_items_json()
    items = data.get("items", []) or []
    if not items:
        print("[refresh] items.json has no items — abort", flush=True)
        return 1

    top_titles = [it.get("title", "") for it in items[:SLICE_COUNT] if it.get("title")]
    if not top_titles:
        print("[refresh] no titles in top items — abort", flush=True)
        return 1

    fingerprint = " || ".join(_cover_headline_norm(t) for t in top_titles)
    prev = read_cover_meta()
    prev_fp = prev.get("top_fingerprint", "") or _cover_headline_norm(prev.get("top_headline", ""))
    needs_regen = (not COVER_IMAGE.exists()) or (prev_fp != fingerprint)

    api_key = _openai_key()
    if not api_key:
        print("[refresh] no OpenAI key — abort", flush=True)
        return 2

    if needs_regen:
        print(f"[refresh] regenerating cover — {len(top_titles)} Polaroid labels", flush=True)
        ok, model_used, err = generate_cover(top_titles, api_key)
        if not ok:
            print(f"[refresh] cover gen failed: {err}", flush=True)
            return 3
        write_cover_meta({
            "top_headline": top_titles[0],
            "top_headlines": top_titles,
            "top_fingerprint": fingerprint,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "model": model_used,
            "regen_reason": "manual-image-refresh",
        })
        print(f"[refresh] cover ✓ via {model_used}", flush=True)
    else:
        print(f"[refresh] cover unchanged — top-6 fingerprint matches prior run", flush=True)

    # Mobile crop always re-runs if source is newer.
    mok, merr = crop_cover_to_mobile()
    print(f"[refresh] mobile crop: {'ok' if mok and not merr else merr}", flush=True)

    # Slice (always — cheap, idempotent on disk)
    sok, slice_urls, serr = slice_cover_into_tiles()
    if not sok:
        print(f"[refresh] slice failed: {serr}", flush=True)
        return 4
    print(f"[refresh] slices ✓ {len(slice_urls)} tiles", flush=True)

    # Assign slices to items missing image_url
    assigned = assign_slices_to_items(items, slice_urls)
    data["items"] = items
    data["cover_image_url"] = "/data/cover.png"
    data["cover_image_mobile_url"] = "/data/cover-mobile.png"
    data["cover_slice_urls"] = slice_urls
    save_items_json(data)
    total = len(items)
    with_image = sum(1 for it in items if it.get("image_url"))
    print(f"[refresh] items.json updated — {assigned} newly assigned slice thumbnails; "
          f"{with_image}/{total} now have image_url (100% = {with_image == total})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
