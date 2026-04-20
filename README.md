# ainews.hotloop.ai

Karl's daily AI news aggregator. Static site reading from `public/data/items.json`,
populated by the `ainews` Claude Code routine on the Mac mini (no API costs).

## Architecture

- **Site**: static HTML + React (CDN) + design-system CSS, deployed to Vercel.
- **Data**: `public/data/items.json` committed by the routine on each daily run.
- **Pipeline**: `~/Library/CloudStorage/.../Creator Workspace/Routines/ainews/routine.md` —
  fetches sources, dedups, classifies, scores, writes items.json, `git push`es,
  Vercel auto-deploys, then sends a 400-word brief to Telegram.
- **Refresh button**: posts to `/api/refresh` (Cloudflare Worker) which sends a
  Telegram ping prompting Karl to run `/routine run ainews` locally.

## Tag vocabulary (fixed, 7)

`Models` · `Agents` · `Automation` · `Coding` · `Skills` · `Content` · `Governance`

## Local preview

Open `public/index.html` directly in a browser. The site is fully static — no
build step. The empty `items.json` shows the empty state until the first
routine run populates it.

## Plan

See `~/.claude/plans/below-is-that-adaptive-lovelace.md` for the full plan and
phase breakdown.
