// Feed components for the ainews.hotloop.ai site.
// Provides: TagChip, TagBadge, NewsCard, TimeBadge, SourcePill,
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
  const FALLBACK_IMG = '/images/hero-fallback.svg';
  const cardImg = item.image_url || FALLBACK_IMG;
  const isFallback = !item.image_url;
  return (
    <article className={"news-card" + (featured ? " news-card--featured" : "") + (density === 'compact' ? " news-card--compact" : "")}>
      {density !== 'compact' && (
        <div className={
          "news-card__image"
          + (item.image_type === 'avatar' && !isFallback ? ' news-card__image--avatar' : '')
          + (isFallback ? ' news-card__image--fallback' : '')
        }>
          <img
            src={cardImg}
            alt=""
            loading="lazy"
            onError={e => { e.currentTarget.src = FALLBACK_IMG; }}
          />
        </div>
      )}
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

// Normalize an items.json record into the shape the cards expect.
// Source shape (from routine):  { id, url, title, summary, tags, source, source_tier,
//                                 region, published_at, trending_score, ... }
// Card shape: { id, url, title, summary, tags, region, source_tier,
//               timeAgoMins, importance, citations[{domain,label,url}] }
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
    image_url: raw.image_url || null,
    image_source: raw.image_source || null,
    image_type: raw.image_type || 'photo',
    title: raw.title_translated || raw.title,
    summary: raw.summary || '',
    tags: raw.tags || [],
    region: raw.region || null,
    source_tier: raw.source_tier || null,
    timeAgoMins,
    importance,
    citations,
  };
}

function NewsflashBanner({ items, onDismiss }) {
  if (!items || items.length === 0) return null;
  return (
    <section className="newsflash" role="alert" aria-live="polite">
      <div className="newsflash__strip">
        <span className="newsflash__pulse" aria-hidden="true">
          <span className="newsflash__pulse-dot" />
          <span className="newsflash__pulse-ring" />
        </span>
        <span className="newsflash__label">Newsflash</span>
        <div className="newsflash__links">
          {items.map((item, i) => {
            const src = item.source || (item.citations && item.citations[0]?.label) || '';
            return (
              <React.Fragment key={item.id || i}>
                {i > 0 && <span className="newsflash__sep" aria-hidden="true">·</span>}
                <span className="newsflash__entry">
                  <a href={item.url || '#'} target="_blank" rel="noreferrer" className="newsflash__link">{item.title}</a>
                  {src && <span className="newsflash__src">— {src}</span>}
                </span>
              </React.Fragment>
            );
          })}
        </div>
        <button className="newsflash__dismiss" onClick={onDismiss} aria-label="Dismiss newsflash">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </section>
  );
}

function DailySummary({ text }) {
  const [expanded, setExpanded] = React.useState(false);
  if (!text) return null;
  const THRESHOLD = 320;
  const isLong = text.length > THRESHOLD;
  const displayed = isLong && !expanded ? text.slice(0, THRESHOLD - 1) + '…' : text;
  return (
    <section className="daily-summary">
      <div className="daily-summary__label">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 16 14"/></svg>
        Today's Brief
      </div>
      <p className="daily-summary__text">{displayed}</p>
      {isLong && (
        <button className="daily-summary__toggle" onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Show less' : 'Read more'}
        </button>
      )}
    </section>
  );
}

function XFeedTicker({ tweets, dismissed, onDismiss }) {
  const [current, setCurrent] = React.useState(0);
  const [fading, setFading] = React.useState(false);

  React.useEffect(() => {
    if (!tweets || tweets.length <= 1) return;
    let timeoutId;
    const id = setInterval(() => {
      setFading(true);
      timeoutId = setTimeout(() => {
        setCurrent(c => (c + 1) % tweets.length);
        setFading(false);
      }, 280);
    }, 5000);
    return () => { clearInterval(id); clearTimeout(timeoutId); };
  }, [tweets]);

  if (!tweets || tweets.length === 0 || dismissed) return null;

  const tweet = tweets[current];
  const timeAgo = formatAgo(Math.max(0, (Date.now() - new Date(tweet.posted_at).getTime()) / 60000));
  const truncated = tweet.text.length > 110 ? tweet.text.slice(0, 109) + '…' : tweet.text;

  return (
    <div className="xfeed-ticker" role="marquee" aria-label="X feed">
      <div className="xfeed-ticker__inner">
        <span className="xfeed-ticker__badge" aria-hidden="true">𝕏 FEED</span>
        <a
          className={"xfeed-ticker__content" + (fading ? " xfeed-ticker__content--fade" : "")}
          href={tweet.url}
          target="_blank"
          rel="noreferrer"
          aria-label={"@" + tweet.handle + ": " + tweet.text}
        >
          <span className="xfeed-ticker__handle">@{tweet.handle}</span>
          <span className="xfeed-ticker__sep" aria-hidden="true">·</span>
          <span className="xfeed-ticker__text">{truncated}</span>
          <span className="xfeed-ticker__sep xfeed-ticker__time-sep" aria-hidden="true">·</span>
          <span className="xfeed-ticker__time">{timeAgo}</span>
        </a>
        <span className="xfeed-ticker__counter" aria-live="polite" aria-atomic="true">
          {current + 1}/{tweets.length}
        </span>
        <button
          className="xfeed-ticker__dismiss"
          onClick={onDismiss}
          aria-label="Dismiss X feed ticker"
          title="Dismiss"
        >
          <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>
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
  TagChip, TagBadge, NewsCard, NewsflashBanner, DailySummary,
  XFeedTicker, TimeBadge, SourcePill, RegionBadge, TAG_COLORS, REGION_LABELS,
  formatAgo, normalizeItem, makeSearchIndex,
});
