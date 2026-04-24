# ainews.hotloop.ai — Migration Plan v3

**Version:** v3
**Date:** 2026-04-24 (NZ)
**Author:** Karl + Claude
**Supersedes:** v1, v2 (multi-Worker / bridge-Worker / prefetch.py designs — all retired)

---

## 1. TL;DR

- The 2026-04-24 06:02 NZ scheduled run failed: the routine stalled on the first `Write` prompt and never produced `items.json`.
- **Root cause A:** `Write` is entirely absent from the declared allow list — every run blocks on the first file write.
- **Root cause B:** the allow list lives at `Projects/Ainews-sitre/.claude/settings.local.json`, but the scheduler spawns Claude Code from `~/Documents/Claude/Scheduled/{taskId}/`, so the project-local file never loads.
- **Fix:** one long Claude Code routine, one cron slot, allow list promoted to user-global `~/.claude/settings.json` with `Write` + wrangler + git + heredoc Bash patterns.
- Zero new Workers. Existing `ainews` serve Worker stays as-is. KV writes go through `wrangler` inside the routine.

---

## 2. Target Model (one routine, one prompt, one permission surface)

One Claude Code routine invoked by the scheduled-tasks MCP on Karl's Mac Mini, using his Claude Code subscription (no paid API keys except optional Gemini for cover). End-to-end steps:

1. Fetch 18 RSS feeds (openai.com, deepmind.google, blog.google, blogs.nvidia.com, developer.nvidia.com, www.techmeme.com, simonwillison.net, www.latent.space, importai.substack.com, www.interconnects.ai, hnrss.org, ai.meta.com, www.ncsc.govt.nz, mistral.ai, huggingface.co, blogs.microsoft.com, api-docs.deepseek.com, qwen.readthedocs.io).
2. Scrape X / tweets via Grok MCP (`mcp__Grok__search_x`, `mcp__Grok__search_web`).
3. Scrape/enrich `og:image` per candidate — closes the `tweets.json` orphan and the missing `image_url` regression in one pass.
4. Dedupe against KV history (`wrangler kv key get`).
5. Classify / score / pick newsflash.
6. Summarise → `daily_summary`.
7. Pick "most interesting" item for the daily cover.
8. Generate/compose cover image (Karl picks one of three options in §6).
9. Write `items.json` + `tweets.json` + `cover.png` → `public/data/`.
10. `wrangler deploy` → ainews.hotloop.ai.
11. Compose daily brief → Nova Inbox (Creator Workspace).
12. `git add` → `git commit` → `git push`.
13. Telegram success/failure ping.

**Existing `ainews` serve Worker stays as-is. No prefetch.py, no bridge Worker, no second Worker.**

---

## 3. Current State (verbatim)

### 3.1 Project-local allow list — `/Users/hotloopai-macmini/Projects/Ainews-sitre/.claude/settings.local.json` (last edited 2026-04-23 23:47 UTC, immediately after the failed 06:02 NZ run)

```json
{"permissions":{"allow":[
  "Bash(mkdir -p /Users/hotloopai-macmini/.claude/projects/ainews)",
  "Read(//Users/hotloopai-macmini/.claude/projects/**)",
  "Bash(python3.13 -c ' *)","Bash(echo \"Exit: $?\")","Bash(npx wrangler@latest whoami)",
  "Bash(/opt/homebrew/bin/python3.13 /Users/hotloopai-macmini/Projects/Ainews-sitre/prefetch.py)",
  "Bash(echo \"EXIT:$?\")",
  "WebFetch(domain:openai.com)","WebFetch(domain:deepmind.google)","WebFetch(domain:blog.google)",
  "WebFetch(domain:blogs.nvidia.com)","mcp__Grok__search_x","mcp__Grok__search_web",
  "WebFetch(domain:developer.nvidia.com)","WebFetch(domain:www.techmeme.com)",
  "WebFetch(domain:simonwillison.net)","WebFetch(domain:www.latent.space)",
  "WebFetch(domain:importai.substack.com)","WebFetch(domain:www.interconnects.ai)",
  "WebFetch(domain:hnrss.org)","WebFetch(domain:ai.meta.com)","WebFetch(domain:www.ncsc.govt.nz)"
]}}
```

Problems: **no `Write` entry at all**; `prefetch.py` entry is obsolete (retired in v3); file loads only when Claude Code's cwd = project at boot, which the scheduler does not guarantee.

### 3.2 User-global — `~/.claude/settings.json`

No `permissions` block. Nothing to inherit. This is the lever we take.

---

## 4. Declared-Permissions Manifest (drop into `~/.claude/settings.json`)

Every entry the v3 routine actually hits. Grouped for review; flatten into one `allow` array when pasting.

### 4.1 Read
```
Read(/Users/hotloopai-macmini/Projects/Ainews-sitre/**)
Read(/Users/hotloopai-macmini/.claude/projects/**)
Read(/Users/hotloopai-macmini/Documents/Claude/Scheduled/**)
Read(/Users/hotloopai-macmini/Creator Workspace/**)
Read(/Users/hotloopai-macmini/Creator Workspace/Nova Inbox/**)
```

### 4.2 Write — **BIGGEST GAP, fixes today's 06:02 failure**
```
Write(/Users/hotloopai-macmini/Projects/Ainews-sitre/public/data/**)
Write(/Users/hotloopai-macmini/Projects/Ainews-sitre/public/feed.xml)
Write(/Users/hotloopai-macmini/Projects/Ainews-sitre/state.json)
Write(/Users/hotloopai-macmini/Projects/Ainews-sitre/**)
Write(/Users/hotloopai-macmini/Creator Workspace/Nova Inbox/**)
Write(/tmp/ainews-*)
```

### 4.3 Edit
```
Edit(/Users/hotloopai-macmini/Projects/Ainews-sitre/**)
Edit(/Users/hotloopai-macmini/Creator Workspace/Nova Inbox/**)
```

### 4.4 Bash (deploy, git, file plumbing)
```
Bash(cd /Users/hotloopai-macmini/Projects/Ainews-sitre && *)
Bash(npx wrangler@latest deploy *)
Bash(npx wrangler@latest kv:key get *)
Bash(npx wrangler@latest kv:key put *)
Bash(npx wrangler@latest kv:key list *)
Bash(npx wrangler@latest whoami)
Bash(git add *)
Bash(git commit -m *)
Bash(git push *)
Bash(git status *)
Bash(git diff *)
Bash(mkdir -p *)
Bash(sleep *)
Bash(curl -fsSL *)
Bash(cat > /tmp/ainews-* << *)
Bash(cat >> /tmp/ainews-* << *)
Bash(cat > /Users/hotloopai-macmini/Projects/Ainews-sitre/public/data/* << *)
Bash(/opt/homebrew/bin/python3.13 -c *)
Bash(python3.13 -c *)
Bash(echo *)
```

### 4.5 WebFetch — all 18 feed domains
```
WebFetch(domain:openai.com)
WebFetch(domain:deepmind.google)
WebFetch(domain:blog.google)
WebFetch(domain:blogs.nvidia.com)
WebFetch(domain:developer.nvidia.com)
WebFetch(domain:www.techmeme.com)
WebFetch(domain:simonwillison.net)
WebFetch(domain:www.latent.space)
WebFetch(domain:importai.substack.com)
WebFetch(domain:www.interconnects.ai)
WebFetch(domain:hnrss.org)
WebFetch(domain:ai.meta.com)
WebFetch(domain:www.ncsc.govt.nz)
WebFetch(domain:mistral.ai)
WebFetch(domain:huggingface.co)
WebFetch(domain:blogs.microsoft.com)
WebFetch(domain:api-docs.deepseek.com)
WebFetch(domain:qwen.readthedocs.io)
```

### 4.6 Grok MCP
```
mcp__Grok__search_x
mcp__Grok__search_web
```

### 4.7 Permission-lever fallback order
1. Paste the above into `~/.claude/settings.json` — **preferred**, survives any cwd.
2. Pass `--allowed-tools "Write Edit Bash(npx wrangler@latest deploy *) …"` on the CLI as belt-and-braces.
3. `--permission-mode bypassPermissions` / `--dangerously-skip-permissions` — acceptable for this routine because it runs under Karl's account on his hardware and touches only his repo + his Workspace.
4. SKILL.md frontmatter `allowed-tools:` — lowest priority, only if skill-scoped.

---

## 5. routine.md Edits

Changes to the v3 routine prompt living next to the scheduled-tasks entry:

- **STEP 5 schema:** add `image_url` field to every item record (resolves the missing-image regression; drop the og:image scraper output straight into this field).
- **STEP 9:** add explicit `tweets.json` write alongside `items.json` — the tweets.json orphan goes away once this step is declared.
- **New STEP 7–8:** cover-image pick + render, with a branch on Karl's chosen option (§6).
- **"How this runs" doc:** replace any launchd references with scheduled-tasks MCP wording. The routine is triggered by `mcp__scheduled-tasks__create_scheduled_task` and executes from `~/Documents/Claude/Scheduled/{taskId}/`. Note explicitly that the allow list MUST be at `~/.claude/settings.json`, not project-local.

---

## 6. Cover Image — Three Options for Karl

| Option | Cost | AI? | Karl effort | Failure mode |
|---|---|---|---|---|
| **(a) Gemini 1 call/day** | ~$0.04/day, uses existing `GEMINI_API_KEY` | Yes | None | API outage skips the cover; fall back to (b) or reuse yesterday |
| **(b) Programmatic SVG template** | $0 | None | ~1 hr to build template | Visual sameness over time |
| **(c) Rotating pre-made set** | $0 | None | ~30 min curation, re-curate monthly | Repeat on longer runs |

Recommendation: start (a), keep (b) coded as fallback so the routine never fails for a cover.

---

## 7. Verification Plan

**Stage 1 — manual dry run (today, 2026-04-24 afternoon NZ):**
- Karl fires `claude -p` from Terminal against the updated routine.
- Expect: `items.json`, `tweets.json`, `cover.png` written to `public/data/` with **zero** permission prompts.
- Check: `wrangler deploy` completes; ainews.hotloop.ai shows today's items; `state.json.last_run_kind == "manual"`.

**Stage 2 — scheduled run (2026-04-25 06:02 NZ):**
- Scheduler fires routine from `~/Documents/Claude/Scheduled/{taskId}/`.
- Expect by 06:30 NZ: `state.json.last_run_kind == "scheduled"`, fresh items on ainews.hotloop.ai, Nova Inbox brief present, Telegram success ping received.
- Fail path: Telegram failure ping with stderr tail → Karl intervenes same morning.

---

## 8. Cutover Order

1. **Backup** current `~/.claude/settings.json` and project-local `.claude/settings.local.json`.
2. **Update** `~/.claude/settings.json` with the full manifest from §4.
3. **Edit** routine.md per §5 (image_url field, tweets.json write, cover step, scheduled-tasks doc note).
4. **Stage 1** manual dry run. Block on clean pass.
5. **Stage 2** wait for 06:02 NZ scheduled run.
6. **Telegram alert** on failure — already wired; confirm webhook still live as part of Stage 1.

---

## 9. Red-Herring Fix — OpenClaw 6am jobs

Separate from ainews but co-failing at 6am NZ: OpenClaw jobs fail against `gpt-5.2-codex` (deprecated). Change `openclaw.json` model string to `gpt-5.4`. Not a blocker for ainews cutover; fold into the same morning.

---

## 10. Open Questions for Karl

1. **Cover image choice** — (a) Gemini, (b) programmatic SVG, (c) rotating set? Default assumption: (a) with (b) as fallback.
2. **`--dangerously-skip-permissions`** — acceptable for the scheduled invocation? Risk surface is Karl's own repo + Workspace.
3. **`--allowed-tools` belt-and-braces** — worth adding on top of the user-global allow list, or redundant?
4. **x.ai/news 403 handling** — currently silently dropped. Retry with Grok MCP `search_web`, or accept the gap?
