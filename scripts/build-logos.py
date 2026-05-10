#!/usr/bin/env python3.13
"""Emit branded gradient logo SVGs into public/images/logos/.

Glyphs come from simple-icons (CC0). For brands not in simple-icons (NZ govt
entities), we emit a wordmark on the same gradient template so the cards
look visually consistent.

Run any time the brand list grows or colours need tweaking. Output is
deterministic — re-running won't churn git.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parents[1] / "public" / "images" / "logos"
SI_CDN = "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{slug}.svg"

# Each entry: our_slug, label, simple-icons slug or None (=wordmark fallback),
# aurora1_color (top-left), aurora2_color (bottom-right), glyph color.
# Auroras are bright, saturated brand colours — they bleed into the dark base
# at low opacity. Glyph is mostly white; deviations carry brand signature
# (NVIDIA green, Hugging Face yellow).
LOGOS = [
    # ── T1 frontier labs ─────────────────────────────────────────────────
    ("openai",      "OpenAI",      "openai",          "#10A37F", "#0FA67E", "#FFFFFF"),
    ("anthropic",   "Anthropic",   "anthropic",       "#D97757", "#7A3B1E", "#F2D8C5"),
    ("google",      "Google",      "google",          "#4285F4", "#EA4335", "#FFFFFF"),
    ("deepmind",    "DeepMind",    "googlegemini",    "#4885ED", "#8B5CF6", "#FFFFFF"),
    ("meta",        "Meta",        "meta",            "#0866FF", "#3B82F6", "#FFFFFF"),
    ("xai",         "xAI",         "x",               "#FF6A3D", "#A855F7", "#FFFFFF"),
    ("microsoft",   "Microsoft",   "microsoftazure",  "#0078D4", "#7FBA00", "#FFFFFF"),
    ("nvidia",      "NVIDIA",      "nvidia",          "#76B900", "#3D5A00", "#76B900"),
    ("mistral",     "Mistral",     None,              "#FA520F", "#FFD800", "#FFFFFF"),
    ("deepseek",    "DeepSeek",    "deepseek",        "#4D6BFE", "#1F2A6C", "#FFFFFF"),
    ("qwen",        "Qwen",        "alibabacloud",    "#615CED", "#A855F7", "#FFFFFF"),
    ("huggingface", "Hugging Face","huggingface",     "#FFD21E", "#FF9D00", "#FFD21E"),
    ("apple",       "Apple",       "apple",           "#A1A1AA", "#52525B", "#FFFFFF"),
    ("aws",         "AWS",         "amazonwebservices","#FF9900", "#232F3E", "#FF9900"),
    # ── Aggregators / media ──────────────────────────────────────────────
    ("techmeme",    "Techmeme",    None,              "#E2731D", "#A14A0F", "#FFFFFF"),
    ("hackernews",  "HN",          "ycombinator",     "#FF6600", "#A33D00", "#FFFFFF"),
    ("the-verge",   "The Verge",   None,              "#FA4778", "#7A1F38", "#FFFFFF"),
    # ── NZ government / regulators (no simple-icons; wordmarks) ──────────
    ("rbnz",        "RBNZ",        None,              "#0E5099", "#3A7DC7", "#FFFFFF"),
    ("ncsc-nz",     "NCSC NZ",     None,              "#54B948", "#1F2A44", "#FFFFFF"),
    ("govt-nz",     "govt.nz",     None,              "#003F87", "#1A6BB8", "#FFFFFF"),
]

CACHE = Path(__file__).resolve().parents[1] / ".cache" / "simple-icons"


def fetch_glyph_path(si_slug: str) -> str:
    """Return the path-d attribute of the simple-icons glyph (cached)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{si_slug}.svg"
    if not cache.exists():
        req = Request(SI_CDN.format(slug=si_slug), headers={"User-Agent": "ainews-build"})
        with urlopen(req, timeout=15) as r:
            cache.write_bytes(r.read())
    raw = cache.read_text()
    m = re.search(r'<path[^>]*\sd="([^"]+)"', raw)
    if not m:
        raise RuntimeError(f"no path in {si_slug}.svg")
    return m.group(1)


# Style reference: x.ai/news cover images — near-black base with two
# soft auroras of brand colour + clean centered glyph in white. The fg
# parameter is preserved per-brand for cases where a coloured glyph reads
# better than white (e.g. NVIDIA green-on-black).

DEFS = """  <defs>
    <radialGradient id="aurora1" cx="0.25" cy="0.32" r="0.55" fx="0.18" fy="0.28">
      <stop offset="0" stop-color="{c1}" stop-opacity="0.85"/>
      <stop offset="0.55" stop-color="{c1}" stop-opacity="0.18"/>
      <stop offset="1" stop-color="{c1}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="aurora2" cx="0.78" cy="0.78" r="0.55" fx="0.85" fy="0.82">
      <stop offset="0" stop-color="{c2}" stop-opacity="0.65"/>
      <stop offset="0.55" stop-color="{c2}" stop-opacity="0.14"/>
      <stop offset="1" stop-color="{c2}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="vignette" cx="0.5" cy="0.5" r="0.7">
      <stop offset="0" stop-color="#000000" stop-opacity="0"/>
      <stop offset="1" stop-color="#000000" stop-opacity="0.55"/>
    </radialGradient>
    <filter id="grain">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3"/>
      <feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.08 0"/>
      <feComposite in2="SourceGraphic" operator="in"/>
    </filter>
  </defs>"""

BACKDROP = """  <rect width="256" height="256" fill="#0A0A0F"/>
  <rect width="256" height="256" fill="url(#aurora1)"/>
  <rect width="256" height="256" fill="url(#aurora2)"/>
  <rect width="256" height="256" fill="url(#vignette)"/>
  <rect width="256" height="256" filter="url(#grain)" opacity="0.4"/>"""

GLYPH_TEMPLATE = (
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" preserveAspectRatio="xMidYMid slice" role="img" aria-label="{label}">
"""
    + DEFS
    + """
"""
    + BACKDROP
    + """
  <g transform="translate(72 72) scale(4.667)" fill="{fg}">
    <path d="{glyph_d}"/>
  </g>
</svg>
"""
)

WORDMARK_TEMPLATE = (
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" preserveAspectRatio="xMidYMid slice" role="img" aria-label="{label}">
"""
    + DEFS
    + """
"""
    + BACKDROP
    + """
  <text x="128" y="144" font-family="ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" font-weight="600" font-size="{font_size}" letter-spacing="-1" fill="{fg}" text-anchor="middle">{label}</text>
</svg>
"""
)


def render(slug: str, label: str, si_slug: str | None,
           c1: str, c2: str, fg: str) -> str:
    if si_slug:
        d = fetch_glyph_path(si_slug)
        return GLYPH_TEMPLATE.format(label=label, c1=c1, c2=c2, fg=fg, glyph_d=d)
    size = 56 if len(label) <= 5 else 44 if len(label) <= 8 else 36
    return WORDMARK_TEMPLATE.format(label=label, c1=c1, c2=c2, fg=fg, font_size=size)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for slug, label, si_slug, c1, c2, fg in LOGOS:
        svg = render(slug, label, si_slug, c1, c2, fg)
        path = OUT / f"{slug}.svg"
        if not path.exists() or path.read_text() != svg:
            path.write_text(svg)
            written += 1
    print(f"[build-logos] wrote {written} of {len(LOGOS)} → {OUT}")


if __name__ == "__main__":
    main()
