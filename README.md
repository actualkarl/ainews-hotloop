# ainews.hotloop.ai

Karl's daily AI news aggregator. Static site reading from `public/data/items.json`,
populated by the `ainews` Claude Code routine on the Mac mini (no API costs).

## Architecture

- **Hosting**: a single Cloudflare Worker (`wrangler.toml` → `src/worker.js`)
  with the `[assets]` binding serving `public/`. The route
  `ainews.hotloop.ai` is `custom_domain = true`, so DNS auto-provisions on
  `wrangler deploy` (same pattern as `demos.hotloop.ai`).
- **Data**: `public/data/items.json` written by the routine on each daily run,
  committed to git for audit, and shipped to the Worker via `wrangler deploy`.
- **Pipeline**: `~/Library/CloudStorage/.../Creator Workspace/Routines/ainews/routine.md`
  — fetches sources, dedups, classifies, scores, writes items.json, runs
  `wrangler deploy`, then sends a 400-word brief to Telegram.
- **Refresh button**: site posts `/api/refresh` to the same Worker, which
  sends a Telegram ping to `@clovaagent_bot` prompting Karl to run
  `/routine run ainews` locally.

## Tag vocabulary (fixed, 7)

`Models` · `Agents` · `Automation` · `Coding` · `Skills` · `Content` · `Governance`

## Local preview

```
python3 -m http.server 5173 --directory public
# then open http://localhost:5173
```

The site is fully static — no build step.

## Deploy

```
npx wrangler deploy
```

This uploads `public/` as static assets and the Worker code, and (on first
deploy) creates the DNS record for `ainews.hotloop.ai`.

## Secrets (set once)

```
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
```

Without these, `POST /api/refresh` returns `503` with a clear error.

## Plan

See `~/.claude/plans/below-is-that-adaptive-lovelace.md` for the full plan
and phase breakdown.
