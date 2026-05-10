#!/usr/bin/env python3.13
"""Emit brand-coloured wordmark SVGs into public/images/logos/.

These are placeholder logos for cards lacking an og:image. Run any time the
brand list grows or colours need tweaking. Output is deterministic — same
input always produces same SVG bytes — so re-running won't churn git.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "images" / "logos"

# slug → (display_text, bg_hex, fg_hex, optional_accent_hex)
LOGOS = [
    # ── T1 frontier labs ─────────────────────────────────────────────────
    ("openai",      "OpenAI",    "#000000", "#FFFFFF", "#10A37F"),
    ("anthropic",   "Anthropic", "#F5F4EE", "#191919", "#D97757"),
    ("google",      "Google",    "#FFFFFF", "#202124", "#4285F4"),
    ("deepmind",    "DeepMind",  "#0E1B3D", "#FFFFFF", "#4885ED"),
    ("meta",        "Meta",      "#0866FF", "#FFFFFF", "#1877F2"),
    ("xai",         "xAI",       "#000000", "#FFFFFF", "#FFFFFF"),
    ("microsoft",   "Microsoft", "#F2F2F2", "#202020", "#0078D4"),
    ("nvidia",      "NVIDIA",    "#000000", "#76B900", "#76B900"),
    ("mistral",     "Mistral",   "#0F0F0F", "#FA520F", "#FFD800"),
    ("deepseek",    "DeepSeek",  "#0F1B3F", "#4D6BFE", "#FFFFFF"),
    ("qwen",        "Qwen",      "#615CED", "#FFFFFF", "#FFD21E"),
    ("huggingface", "Hugging\nFace", "#FFD21E", "#0F0F0F", "#FF9D00"),
    ("apple",       "Apple",     "#000000", "#FFFFFF", "#A2AAAD"),
    ("aws",         "AWS",       "#232F3E", "#FF9900", "#FF9900"),
    # ── NZ government / regulators ───────────────────────────────────────
    ("rbnz",        "RBNZ",      "#0E5099", "#FFFFFF", "#FFFFFF"),
    ("ncsc-nz",     "NCSC NZ",   "#1F2A44", "#FFFFFF", "#54B948"),
    ("govt-nz",     "govt.nz",   "#003F87", "#FFFFFF", "#FFFFFF"),
    # ── Aggregators / media ──────────────────────────────────────────────
    ("techmeme",    "Techmeme",  "#E2731D", "#FFFFFF", "#FFFFFF"),
    ("hackernews",  "HN",        "#FF6600", "#FFFFFF", "#FFFFFF"),
    ("the-verge",   "The Verge", "#000000", "#FFFFFF", "#FA4778"),
]

TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" preserveAspectRatio="xMidYMid slice" role="img" aria-label="{label}">
  <rect width="256" height="256" fill="{bg}"/>
  <circle cx="216" cy="40" r="14" fill="{accent}" opacity="0.9"/>
  <g font-family="ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" font-weight="700" fill="{fg}" text-anchor="middle">
{tspans}
  </g>
</svg>
"""


def _tspans(text: str, fg: str) -> str:
    lines = text.split("\n")
    if len(lines) == 1:
        size = 44 if len(text) <= 8 else 36 if len(text) <= 11 else 28
        return f'    <text x="128" y="148" font-size="{size}" letter-spacing="-0.5">{text}</text>'
    size = 44
    spans = []
    base_y = 128 - (len(lines) - 1) * size // 2 + size // 2
    for i, line in enumerate(lines):
        y = base_y + i * (size + 4)
        spans.append(f'    <text x="128" y="{y}" font-size="{size}" letter-spacing="-0.5">{line}</text>')
    return "\n".join(spans)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for slug, label, bg, fg, accent in LOGOS:
        svg = TEMPLATE.format(
            label=label.replace("\n", " "),
            bg=bg,
            fg=fg,
            accent=accent,
            tspans=_tspans(label, fg),
        )
        path = OUT / f"{slug}.svg"
        if not path.exists() or path.read_text() != svg:
            path.write_text(svg)
            written += 1
    print(f"[build-logos] wrote {written} of {len(LOGOS)} → {OUT}")


if __name__ == "__main__":
    main()
