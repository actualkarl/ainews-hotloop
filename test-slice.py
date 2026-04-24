#!/usr/bin/env python3
"""
test-slice.py — Phase A standalone test for the grid-cover + slice idea.

Calls gpt-image-2 once ($0.006) with the new grid-composition prompt,
saves the raw cover to test-output/grid-cover.png, slices it into a
3-by-2 grid of 512x512 tiles, and writes each tile to disk.

Does NOT touch prefetch.py, items.json, cover-meta.json, KV, or git.
"""

import base64
import json
import os
import re
import sys
from pathlib import Path

import requests
from PIL import Image

REPO = Path(__file__).parent
OUT = REPO / "test-output"
COVER = OUT / "grid-cover.png"

SECRETS_ENV = Path.home() / ".openclaw" / "ainews-secrets.env"
OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"

TOP_HEADLINE = "Sanders and AOC introduce AI Data Center Moratorium Act"

PROMPT = (
    'Editorial illustration grid for today\'s AI news, themed around: "{top_headline}". '
    'Composition: 3-by-2 grid of six distinct vignettes, each a discrete scene on its own '
    'plate with subtle negative-space gutters between cells. Each cell depicts a different '
    'angle, object, or metaphor related to the theme. Clean minimalist modern tech-journalism '
    'aesthetic. Muted palette with rose (#FF5B6E) and teal (#14B8BF) accents, warm white '
    'background. No text or letters anywhere. Square composition within each cell, balanced '
    'negative space in every cell.'
)


def load_openai_key() -> str | None:
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k.strip()
    try:
        if SECRETS_ENV.exists():
            for line in SECRETS_ENV.read_text().splitlines():
                m = re.match(r'\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*"?([^"\s]+)"?', line)
                if m:
                    return m.group(1).strip()
    except Exception as e:
        print(f"[test] secrets.env read failed: {e}", flush=True)
    try:
        if OPENCLAW_JSON.exists():
            d = json.loads(OPENCLAW_JSON.read_text())
            v = d.get("models", {}).get("providers", {}).get("openai", {}).get("apiKey")
            if v:
                return v.strip()
    except Exception as e:
        print(f"[test] openclaw.json read failed: {e}", flush=True)
    return None


def generate_cover(api_key: str, out_path: Path) -> tuple[bool, str | None, str | None]:
    """Try gpt-image-2 → gpt-image-1 (matches production fallback chain)."""
    prompt = PROMPT.format(top_headline=TOP_HEADLINE.replace('"', ''))
    chain = [
        {"model": "gpt-image-2", "size": "1536x1024", "quality": "low"},
        {"model": "gpt-image-1", "size": "1536x1024", "quality": "low"},
    ]
    last_err: str | None = None
    for cfg in chain:
        body = {"model": cfg["model"], "prompt": prompt,
                "size": cfg["size"], "quality": cfg["quality"], "n": 1}
        try:
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
        except Exception as e:
            last_err = f"{cfg['model']}: request error: {e}"
            continue
        if not resp.ok:
            last_err = f"{cfg['model']}: HTTP {resp.status_code} {resp.text[:300]}"
            print(f"[test] {last_err}", flush=True)
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
            img_resp = requests.get(entry["url"], timeout=60)
            img_resp.raise_for_status()
            out_path.write_bytes(img_resp.content)
        else:
            last_err = f"{cfg['model']}: no b64 or url"
            continue
        return True, cfg["model"], None
    return False, None, last_err


def slice_cover(cover_path: Path, out_dir: Path) -> list[Path]:
    im = Image.open(cover_path)
    W, H = im.size
    cols, rows = 3, 2
    cell_w, cell_h = W // cols, H // rows
    paths: list[Path] = []
    for row in range(rows):
        for col in range(cols):
            box = (col * cell_w, row * cell_h,
                   (col + 1) * cell_w, (row + 1) * cell_h)
            tile = im.crop(box)
            p = out_dir / f"slice-{row}-{col}.png"
            tile.save(p)
            paths.append(p)
    return paths


def validate_png(p: Path) -> tuple[bool, str]:
    if not p.exists():
        return False, "missing"
    size = p.stat().st_size
    if size == 0:
        return False, "empty"
    try:
        with Image.open(p) as im:
            im.verify()
        # re-open to read size (verify() invalidates the image)
        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode
        return True, f"{size} bytes, {w}x{h}, {mode}"
    except Exception as e:
        return False, f"PIL error: {e}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    key = load_openai_key()
    if not key:
        print("[test] FAIL: no OPENAI_API_KEY found", flush=True)
        return 2

    print(f"[test] headline: {TOP_HEADLINE}", flush=True)
    print(f"[test] calling gpt-image-2 → gpt-image-1 fallback chain (low 1536x1024)...", flush=True)
    ok, model_used, err = generate_cover(key, COVER)
    if not ok:
        print(f"[test] FAIL: cover generation: {err}", flush=True)
        return 3
    print(f"[test] generated via {model_used}", flush=True)

    cover_ok, cover_info = validate_png(COVER)
    print(f"[test] cover: {COVER}  [{cover_info}]  ok={cover_ok}", flush=True)
    if not cover_ok:
        return 4

    print(f"[test] slicing into 3x2 grid...", flush=True)
    tile_paths = slice_cover(COVER, OUT)
    print(f"[test] --- slices ---", flush=True)
    all_ok = True
    for p in tile_paths:
        ok, info = validate_png(p)
        flag = "✓" if ok else "✗"
        print(f"[test] {flag} {p}  [{info}]", flush=True)
        if not ok:
            all_ok = False

    print(f"[test] done. cover={COVER}", flush=True)
    for p in tile_paths:
        print(f"[test] open: {p}", flush=True)
    return 0 if all_ok else 5


if __name__ == "__main__":
    sys.exit(main())
