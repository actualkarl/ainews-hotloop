// Feed components for the ainews.hotloop.ai site.
// Provides: TagChip, TagBadge, NewsCard, BreakingStrip, TimeBadge, SourcePill,
// RegionBadge, TierBadge, normalizeItem, makeSearchIndex.

const TAG_COLORS = {
  "Models":      { bg: "var(--coral-50)",   fg: "var(--coral-900)",   dot: "var(--coral-600)" },
  "Agents":      { bg: "var(--teal-50)",    fg: "var(--teal-900)",    dot: "var(--teal-700)" },
  "Automation":  { bg: "var(--success-50)", fg: "var(--success-700)", dot: "var(--success-500)" },
  "Coding":      { bg: "var(--info-50)",    fg: "var(--info-700)",    dot: "var(--info-500)" },
  "Skills":      { bg: "var(--burgundy-50)",fg: "var(--burgundy-600)",dot: "var(--burgundy-400)" },
  "Content":     { bg: "var(--warning-50)", fg: "var(--warning-700)", dot: "var(--warning-500)" },
  "Governance":  { bg: "var(--stone-100)",  fg: "var(--stone-900)",   dot: "var(--stone-700)" },
};

const REGION_LABELS = { us: "US", cn: "CN", eu: "EU", nz: "NZ" };

function TagChip({ label, active, onClick, count }) {
  const c = TAG_COLORS[label] || TAG_COLORS["Models"];
  return (
    <button
      onClick={onClick}
      className={"tag-chip" + (active ? " tag-chip--active" : "")}
      style={active ? { background: c.bg, color: c.fg, borderColor: c.dot } : {}}
    >
      <span className="tag-dot" style={{ background: c.dot }} />
      {label}
      {typeof count === 'number' && <span className="tag-count">{count}</span>}
    </button>
  );
}

function TagBadge({ label }) {
  const c = TAG_COLORS[label] || TAG_COLORS["Models"];
  return (
    <span className="tag-badge" style={{ background: c.bg, color: c.fg }}>
      <span className="tag-dot" style={{ background: c.dot }} />
      {label}
    </span>
  );
}

function RegionBadge({ region }) {
  if (!region) return null;
  return <span className="region-badge" data-region={region}>{REGION_LABELS[region] || region.toUpperCase()}</span>;
}

function formatAgo(mins) {
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const h = mins / 60;
  if (h < 24) return `${Math.round(h)}h ago`;
  const d = h / 24;
  return `${Math.round(d)}d ago`;
}

function TimeBadge({ mins, fresh }) {
  return (
    <span className="time-badge">
      {fresh && <span className="fresh-dot" />}
      {formatAgo(mins)}
    </span>
  );
}

function SourcePill({ citation }) {
  const initial = (citation.domain || "?")[0].toUpperCase();
  return (
    <a className="source-pill" href={citation.url} target="_blank" rel="noreferrer"
       onClick={(e) => { if (citation.url === '#') e.preventDefault(); }}>
      <span className="source-favicon">{initial}</span>
      <span className="source-label">{citation.label}</span>
      <svg className="source-ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M7 17L17 7M8 7h9v9"/>
      </svg>
    </a>
  );
}

function NewsCard({ item, density, featured }) {
  const fresh = item.timeAgoMins < 120;
  return (
    <article className={"news-card" + (featured ? " news-card--featured" : "") + (density === 'compact' ? " news-card--compact" : "")}>
      <header className="news-card__head">
        <div className="news-card__tags">
          {item.tags.map(t => <TagBadge key={t} label={t} />)}
          <RegionBadge region={item.region} />
        </div>
        <TimeBadge mins={item.timeAgoMins} fresh={fresh} />
      </header>
      <h2 className="news-card__title">
        {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : item.title}
      </h2>
      {density !== 'compact' && item.summary && <p className="news-card__summary">{item.summary}</p>}
      <footer className="news-card__sources">
        <span className="news-card__sources-label">
          {item.citations.length} source{item.citations.length !== 1 ? 's' : ''}
        </span>
        <div className="news-card__sources-list">
          {item.citations.map((c, i) => <SourcePill key={i} citation={c} />)}
        </div>
      </footer>
    </article>
  );
}

function BreakingStrip({ items, onOpen }) {
  if (!items || items.length === 0) return null;
  const hero = items[0];
  const rest = items.slice(1, 3);
  return (
    <section className="breaking">
      <div className="breaking__label">
        <span className="breaking__pulse">
          <span className="breaking__pulse-dot" />
          <span className="breaking__pulse-ring" />
        </span>
        <span className="breaking__label-text">What's trending</span>
        <span className="breaking__time">Top {items.length} from the last 24h</span>
      </div>
      <div className="breaking__grid">
        <article className="breaking__hero" onClick={() => onOpen && onOpen(hero)}>
          <div className="breaking__hero-tags">
            {hero.tags.slice(0, 2).map(t => <TagBadge key={t} label={t} />)}
            <RegionBadge region={hero.region} />
            <TimeBadge mins={hero.timeAgoMins} fresh />
          </div>
          <h2 className="breaking__hero-title">
            {hero.url ? <a href={hero.url} target="_blank" rel="noreferrer">{hero.title}</a> : hero.title}
          </h2>
          {hero.summary && <p className="breaking__hero-summary">{hero.summary}</p>}
          <div className="breaking__hero-sources">
            {hero.citations.map((c, i) => <SourcePill key={i} citation={c} />)}
          </div>
        </article>
        <div className="breaking__rest">
          {rest.map(item => (
            <article key={item.id} className="breaking__mini" onClick={() => onOpen && onOpen(item)}>
              <div className="breaking__mini-meta">
                {item.tags[0] && <TagBadge label={item.tags[0]} />}
                <TimeBadge mins={item.timeAgoMins} fresh />
              </div>
              <h3 className="breaking__mini-title">
                {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : item.title}
              </h3>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

// Normalize an items.json record into the shape the cards expect.
// Source shape (from routine):  { id, url, title, summary, tags, source, source_tier,
//                                 region, published_at, trending_score, ... }
// Card shape: { id, url, title, summary, tags, region, source_tier,
//               timeAgoMins, importance, trending_score, citations[{domain,label,url}] }
function normalizeItem(raw) {
  const published = raw.published_at ? new Date(raw.published_at).getTime() : Date.now();
  const timeAgoMins = Math.max(0, (Date.now() - published) / 60000);
  const score = typeof raw.trending_score === 'number' ? raw.trending_score : 0;
  const importance = Math.min(3, Math.max(1, Math.round(score * 3) || 1));
  let domain = '';
  try { domain = raw.url ? new URL(raw.url).hostname.replace(/^www\./, '') : ''; } catch (e) {}
  const citations = raw.citations || [
    { domain, label: raw.source || domain || 'source', url: raw.url || '#' }
  ];
  return {
    id: raw.id,
    url: raw.url,
    title: raw.title_translated || raw.title,
    summary: raw.summary || '',
    tags: raw.tags || [],
    region: raw.region || null,
    source_tier: raw.source_tier || null,
    timeAgoMins,
    importance,
    trending_score: score,
    citations,
  };
}

function NewsflashBanner({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="newsflash" role="alert" aria-live="polite">
      <div className="newsflash__inner">
        <div className="newsflash__label">
          <span className="newsflash__pulse">
            <span className="newsflash__pulse-dot" />
            <span className="newsflash__pulse-ring" />
          </span>
          <span className="newsflash__label-text">Newsflash</span>
        </div>
        <div className="newsflash__list">
          {items.map((item, i) => (
            <a
              key={i}
              href={item.url || '#'}
              target="_blank"
              rel="noreferrer"
              className="newsflash__item"
              onClick={e => { if (!item.url || item.url === '#') e.preventDefault(); }}
            >
              <span className="newsflash__item-title">{item.title}</span>
              {item.source && <span className="newsflash__item-source">{item.source}</span>}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function DailySummary({ text }) {
  if (!text) return null;
  return (
    <div className="daily-summary">
      <div className="daily-summary__label">Today's brief</div>
      <p className="daily-summary__text">{text}</p>
    </div>
  );
}

// Build a Fuse index over title + summary + tags. Fuse must be loaded globally.
function makeSearchIndex(items) {
  if (!window.Fuse) return null;
  return new window.Fuse(items, {
    keys: [
      { name: 'title',   weight: 0.6 },
      { name: 'summary', weight: 0.3 },
      { name: 'tags',    weight: 0.1 },
    ],
    threshold: 0.35,
    ignoreLocation: true,
  });
}

Object.assign(window, {
  TagChip, TagBadge, NewsCard, BreakingStrip, TimeBadge, SourcePill,
  RegionBadge, TAG_COLORS, REGION_LABELS, formatAgo,
  normalizeItem, makeSearchIndex,
  NewsflashBanner, DailySummary,
});
