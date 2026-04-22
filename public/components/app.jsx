// Main App for ainews.hotloop.ai. Reads /data/items.json, normalizes,
// filters/searches client-side, renders the design-system feed.

const ALL_TAGS = ["Models", "Agents", "Automation", "Coding", "Skills", "Content", "Governance"];
const ALL_REGIONS = [{ id: 'us', label: 'US' }, { id: 'cn', label: 'CN' }, { id: 'eu', label: 'EU' }, { id: 'nz', label: 'NZ' }];
const DEFAULT_WINDOW = '24h';

const TIME_WINDOWS = [
  { id: 'today',  label: 'Today',   maxMins: 60 * 24 },
  { id: '24h',    label: '24h',     maxMins: 60 * 24 },
  { id: 'week',   label: 'Week',    maxMins: 60 * 24 * 7 },
  { id: 'all',    label: 'All',     maxMins: Infinity },
];

function App() {
  const [items, setItems] = React.useState([]);
  const [activeTag, setActiveTag] = React.useState(null);
  const [activeRegions, setActiveRegions] = React.useState(new Set());
  const [windowId, setWindowId] = React.useState(DEFAULT_WINDOW);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [generatedAt, setGeneratedAt] = React.useState(null);
  const [nextRefreshAt, setNextRefreshAt] = React.useState(null);
  const [showBreaking, setShowBreaking] = React.useState(true);
  const [newsflashIds, setNewsflashIds] = React.useState([]);
  const [dailySummary, setDailySummary] = React.useState('');
  const [newsflashDismissed, setNewsflashDismissed] = React.useState(false);
  const [tweets, setTweets] = React.useState([]);
  const [xfeedDismissed, setXfeedDismissed] = React.useState(false);
  const [tweaks, setTweaks] = React.useState(() => {
    const defaults = window.TWEAK_DEFAULTS || { dark: false, density: 'comfortable', layout: 'grid', showBreaking: true };
    try {
      const stored = JSON.parse(localStorage.getItem('ainews:tweaks') || 'null');
      if (stored && typeof stored === 'object') return { ...defaults, ...stored };
    } catch (e) {}
    return defaults;
  });
  const [tweaksOpen, setTweaksOpen] = React.useState(false);

  // Edit mode wiring (only fires inside Claude Design tool; harmless in prod)
  React.useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === '__activate_edit_mode') setTweaksOpen(true);
      if (e.data?.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', handler);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', handler);
  }, []);

  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    if (tweaks.dark) { html.classList.add('dark'); body.classList.add('dark'); }
    else { html.classList.remove('dark'); body.classList.remove('dark'); }
  }, [tweaks.dark]);

  React.useEffect(() => { loadItems(); loadTweets(); }, []);

  async function loadTweets() {
    try {
      const res = await fetch('/data/tweets.json', { cache: 'no-store' });
      if (!res.ok) return;
      const raw = await res.json();
      setTweets(Array.isArray(raw.tweets) ? raw.tweets : []);
    } catch (err) {
      // graceful fallback — hide ticker
    }
  }

  async function loadItems() {
    setLoading(true);
    try {
      const res = await fetch('/data/items.json', { cache: 'no-store' });
      const raw = await res.json();
      const arr = Array.isArray(raw) ? raw : (raw.items || []);
      setItems(arr.map(window.normalizeItem));
      if (!Array.isArray(raw)) {
        setGeneratedAt(raw.generated_at ? new Date(raw.generated_at) : null);
        setNextRefreshAt(raw.next_refresh_at ? new Date(raw.next_refresh_at) : null);
        setNewsflashIds(Array.isArray(raw.newsflash) ? raw.newsflash : []);
        setDailySummary(typeof raw.daily_summary === 'string' ? raw.daily_summary : '');
        const dismissed = localStorage.getItem('ainews:newsflash_dismissed');
        setNewsflashDismissed(dismissed === raw.generated_at);
      }
    } catch (err) {
      console.error('Failed to load items.json', err);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  const updateTweak = (patch) => {
    const next = { ...tweaks, ...patch };
    setTweaks(next);
    try { localStorage.setItem('ainews:tweaks', JSON.stringify(next)); } catch (e) {}
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: patch }, '*');
  };

  const tagCounts = React.useMemo(() => {
    const c = {};
    ALL_TAGS.forEach(t => c[t] = 0);
    items.forEach(i => i.tags.forEach(t => { if (c[t] !== undefined) c[t]++; }));
    return c;
  }, [items]);

  const filtered = React.useMemo(() => {
    const win = TIME_WINDOWS.find(w => w.id === windowId) || TIME_WINDOWS[3];
    return items
      .filter(i => i.timeAgoMins <= win.maxMins)
      .filter(i => !activeTag || i.tags.includes(activeTag))
      .filter(i => activeRegions.size === 0 || (i.region && activeRegions.has(i.region)))
      .sort((a, b) => a.timeAgoMins - b.timeAgoMins);
  }, [items, activeTag, activeRegions, windowId]);

  const activeFilterCount =
    (activeTag ? 1 : 0) +
    activeRegions.size +
    (windowId !== DEFAULT_WINDOW ? 1 : 0);

  const resetAllFilters = () => {
    setActiveTag(null);
    setActiveRegions(new Set());
    setWindowId(DEFAULT_WINDOW);
  };

  // Close drawer on ESC
  React.useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setDrawerOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  const breaking = React.useMemo(() => {
    return [...items]
      .filter(i => i.timeAgoMins < 60 * 24)
      .sort((a, b) => {
        if (b.importance !== a.importance) return b.importance - a.importance;
        return a.timeAgoMins - b.timeAgoMins;
      })
      .slice(0, 5);
  }, [items]);

  const feedItems = filtered;

  const newsflashItems = React.useMemo(() => {
    if (!newsflashIds.length) return [];
    if (typeof newsflashIds[0] === 'object' && newsflashIds[0] !== null) {
      return newsflashIds;
    }
    const idSet = new Set(newsflashIds);
    return items.filter(i => idSet.has(i.id));
  }, [items, newsflashIds]);

  const dismissNewsflash = () => {
    setNewsflashDismissed(true);
    try {
      const key = generatedAt ? generatedAt.toISOString().replace('.000Z', 'Z') : '';
      localStorage.setItem('ainews:newsflash_dismissed', key);
    } catch (e) {}
  };

  const toggleSet = (set, value) => {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    return next;
  };

  return (
    <div className="app" data-density={tweaks.density} data-layout={tweaks.layout}>
      <TopBar
        generatedAt={generatedAt}
        nextRefreshAt={nextRefreshAt}
        loading={loading}
        dark={tweaks.dark}
        onToggleDark={() => updateTweak({ dark: !tweaks.dark })}
        activeFilterCount={activeFilterCount}
        onOpenFilters={() => setDrawerOpen(true)}
      />
      <XFeedTicker tweets={tweets} dismissed={xfeedDismissed} onDismiss={() => setXfeedDismissed(true)} />
      <main className="main">
        <div className="main__inner">
          <PageHeader total={items.length} fresh={items.filter(i => i.timeAgoMins < 60).length} />

          {dailySummary && (
            <DailySummary text={dailySummary} />
          )}

          {showBreaking && tweaks.showBreaking !== false && breaking.length > 0 && (
            <BreakingStrip items={breaking} />
          )}

          <div className="feed-meta">
            <span className="feed-meta__count">
              <span className="feed-meta__count-num">{feedItems.length}</span> stor{feedItems.length === 1 ? 'y' : 'ies'} showing
            </span>
            {activeFilterCount > 0 && (
              <button className="feed-meta__reset" onClick={resetAllFilters}>Reset filters</button>
            )}
          </div>

          {feedItems.length === 0 ? (
            <EmptyState
              hasItems={items.length > 0}
              onReset={() => { resetAllFilters(); setQuery(''); }}
            />
          ) : (
            <div className={"feed feed--" + (tweaks.layout || 'grid')}>
              {feedItems.map(item => (
                <NewsCard key={item.id} item={item} density={tweaks.density} />
              ))}
            </div>
          )}
        </div>
      </main>

      <FilterDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        tags={ALL_TAGS}
        tagCounts={tagCounts}
        activeTag={activeTag}
        onTagClick={t => setActiveTag(activeTag === t ? null : t)}
        windowId={windowId}
        onWindowChange={setWindowId}
        windows={TIME_WINDOWS}
        regions={ALL_REGIONS}
        activeRegions={activeRegions}
        onRegionToggle={r => setActiveRegions(s => toggleSet(s, r))}
        count={feedItems.length}
        activeFilterCount={activeFilterCount}
        onResetAll={resetAllFilters}
      />

      {tweaksOpen && <TweaksPanel tweaks={tweaks} onChange={updateTweak} onClose={() => setTweaksOpen(false)} />}
    </div>
  );
}

function formatRelative(ms) {
  const abs = Math.abs(ms);
  const future = ms > 0;
  const mins = Math.round(abs / 60000);
  if (mins < 1) return future ? 'in moments' : 'just now';
  if (mins < 60) return future ? `in ${mins}m` : `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return future ? `in ${hours}h` : `${hours}h ago`;
  const days = Math.round(hours / 24);
  return future ? `in ${days}d` : `${days}d ago`;
}

function TopBar({ generatedAt, nextRefreshAt, loading, dark, onToggleDark, activeFilterCount, onOpenFilters }) {
  const [, setTick] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000);
    return () => clearInterval(id);
  }, []);
  const lastLabel = loading ? 'Loading…' :
    generatedAt ? `Last refresh ${formatRelative(generatedAt.getTime() - Date.now())}` :
    'No refresh yet';
  const nextLabel = nextRefreshAt ? `Next ${formatRelative(nextRefreshAt.getTime() - Date.now())}` : null;
  return (
    <header className="topbar">
      <div className="topbar__inner">
        <div className="topbar__brand">
          <svg viewBox="0 0 100 50" className="topbar__logo" aria-hidden="true">
            <defs><linearGradient id="tbg"><stop offset="0%" stopColor="var(--logo-gradient-start)"/><stop offset="100%" stopColor="var(--logo-gradient-end)"/></linearGradient></defs>
            <path d="M 92,25 L 91.3,29.3 L 89.4,33.2 L 86.5,36.3 L 82.9,38.4 L 79.1,39.5 L 75.3,39.8 L 71.6,39.4 L 68.1,38.5 L 64.9,37.1 L 62,35.4 L 59.3,33.5 L 56.8,31.5 L 54.5,29.4 L 52.2,27.2 L 50,25 L 47.8,22.8 L 45.5,20.6 L 43.2,18.5 L 40.7,16.5 L 38,14.6 L 35.1,12.9 L 31.9,11.5 L 28.4,10.6 L 24.7,10.2 L 20.9,10.5 L 17.1,11.6 L 13.5,13.7 L 10.6,16.8 L 8.7,20.7 L 8,25 L 8.7,29.3 L 10.6,33.2 L 13.5,36.3 L 17.1,38.4 L 20.9,39.5 L 24.7,39.8 L 28.4,39.4 L 31.9,38.5 L 35.1,37.1 L 38,35.4 L 40.7,33.5 L 43.2,31.5 L 45.5,29.4 L 47.8,27.2 L 50,25 L 52.2,22.8 L 54.5,20.6 L 56.8,18.5 L 59.3,16.5 L 62,14.6 L 64.9,12.9 L 68.1,11.5 L 71.6,10.6 L 75.3,10.2 L 79.1,10.5 L 82.9,11.6 L 86.5,13.7 L 89.4,16.8 L 91.3,20.7 L 92,25 Z" fill="none" stroke="url(#tbg)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <div className="topbar__title">
            <span className="topbar__wm">hotloop</span>
            <span className="topbar__slash">/</span>
            <span className="topbar__page">daily</span>
          </div>
        </div>
        <nav className="topbar__nav">
          <a className="topbar__nav-item topbar__nav-item--active" href="#">Feed</a>
          <a className="topbar__nav-item topbar__nav-item--disabled" href="#" title="Coming soon" aria-disabled="true" onClick={e => e.preventDefault()}>Saved</a>
          <a className="topbar__nav-item topbar__nav-item--disabled" href="#" title="Coming soon" aria-disabled="true" onClick={e => e.preventDefault()}>Digest</a>
        </nav>
        <div className="topbar__actions">
          <div className="refresh-status" title={generatedAt ? `Generated ${generatedAt.toLocaleString('en-NZ')}` : ''}>
            <span className="refresh-status__dot" data-state={loading ? 'loading' : 'ok'} />
            <div className="refresh-status__lines">
              <span className="refresh-status__last">{lastLabel}</span>
              {nextLabel && <span className="refresh-status__next">{nextLabel}</span>}
            </div>
          </div>
          <button
            className="filters-trigger"
            onClick={onOpenFilters}
            aria-label="Open filters"
            title="Open filters"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="4" y1="6" x2="14" y2="6"/><circle cx="17" cy="6" r="2"/>
              <line x1="4" y1="12" x2="9" y2="12"/><circle cx="12" cy="12" r="2"/><line x1="14" y1="12" x2="20" y2="12"/>
              <line x1="4" y1="18" x2="14" y2="18"/><circle cx="17" cy="18" r="2"/>
            </svg>
            <span className="filters-trigger__label">Filters</span>
            {activeFilterCount > 0 && <span className="filters-trigger__badge">{activeFilterCount}</span>}
          </button>
          <button
            className="theme-toggle"
            onClick={onToggleDark}
            aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {dark ? (
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            )}
          </button>
        </div>
      </div>
    </header>
  );
}

function PageHeader({ total, fresh }) {
  const today = new Date().toLocaleDateString('en-NZ', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  return (
    <div className="page-header">
      <div>
        <div className="page-header__eyebrow">Daily AI · {today}</div>
        <h1 className="page-header__title">What happened in AI today.</h1>
        <p className="page-header__sub">
          <span className="page-header__num">{total}</span> items tracked · <span className="page-header__num">{fresh}</span> new in the last hour. Sources linked on every story.
        </p>
      </div>
    </div>
  );
}

function FilterDrawer({
  open, onClose,
  tags, tagCounts, activeTag, onTagClick,
  windowId, onWindowChange, windows,
  regions, activeRegions, onRegionToggle,
  count, activeFilterCount, onResetAll,
}) {
  return (
    <React.Fragment>
      <div
        className={"drawer-backdrop" + (open ? " drawer-backdrop--open" : "")}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        className={"drawer" + (open ? " drawer--open" : "")}
        role="dialog"
        aria-label="Filters"
        aria-hidden={!open}
      >
        <header className="drawer__head">
          <h2 className="drawer__title">Filters</h2>
          <button className="drawer__close" onClick={onClose} aria-label="Close filters">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </header>
        <div className="drawer__body">
          <section className="drawer__section">
            <h3 className="drawer__section-title">Tags</h3>
            <div className="drawer__chips">
              <button
                className={"tag-chip" + (!activeTag ? " tag-chip--active" : "")}
                onClick={() => onTagClick(null)}
              >
                <span className="tag-dot tag-dot--all" />
                All
              </button>
              {tags.map(t => (
                <TagChip
                  key={t}
                  label={t}
                  active={activeTag === t}
                  onClick={() => onTagClick(t)}
                  count={tagCounts[t]}
                />
              ))}
            </div>
          </section>

          <section className="drawer__section">
            <h3 className="drawer__section-title">Time window</h3>
            <div className="drawer__buttons">
              {windows.map(w => (
                <button
                  key={w.id}
                  className={"window-btn" + (windowId === w.id ? " window-btn--active" : "")}
                  onClick={() => onWindowChange(w.id)}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </section>

          <section className="drawer__section">
            <h3 className="drawer__section-title">Region</h3>
            <div className="drawer__buttons">
              {regions.map(r => (
                <button
                  key={r.id}
                  className={"window-btn" + (activeRegions.has(r.id) ? " window-btn--active" : "")}
                  onClick={() => onRegionToggle(r.id)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </section>
        </div>
        <footer className="drawer__foot">
          <span className="drawer__count">
            <span className="drawer__count-num">{count}</span> stor{count === 1 ? 'y' : 'ies'} matching
          </span>
          <button
            className="btn btn--secondary"
            onClick={onResetAll}
            disabled={activeFilterCount === 0}
          >
            Reset all
          </button>
        </footer>
      </aside>
    </React.Fragment>
  );
}

function EmptyState({ onReset, hasItems }) {
  return (
    <div className="empty">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>
      </svg>
      <h3>{hasItems ? "No stories match these filters." : "No items yet — first run hasn't completed."}</h3>
      <p>{hasItems ? "Try a different tag, region, or widen the time window." : "The 6am NZT routine populates this once a day. Sit tight."}</p>
      {hasItems && <button className="btn btn--secondary" onClick={onReset}>Reset filters</button>}
    </div>
  );
}

function TweaksPanel({ tweaks, onChange, onClose }) {
  return (
    <aside className="tweaks">
      <div className="tweaks__head">
        <h3>Tweaks</h3>
        <button className="tweaks__close" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
      <div className="tweaks__body">
        <TweakField label="Theme">
          <SegmentedControl
            value={tweaks.dark ? 'dark' : 'light'}
            options={[{ v: 'light', l: 'Light' }, { v: 'dark', l: 'Dark' }]}
            onChange={v => onChange({ dark: v === 'dark' })}
          />
        </TweakField>
        <TweakField label="Density">
          <SegmentedControl
            value={tweaks.density}
            options={[{ v: 'comfortable', l: 'Comfortable' }, { v: 'compact', l: 'Compact' }]}
            onChange={v => onChange({ density: v })}
          />
        </TweakField>
        <TweakField label="Layout">
          <SegmentedControl
            value={tweaks.layout}
            options={[{ v: 'grid', l: 'Grid' }, { v: 'list', l: 'List' }, { v: 'magazine', l: 'Magazine' }]}
            onChange={v => onChange({ layout: v })}
          />
        </TweakField>
        <TweakField label="Trending strip">
          <SegmentedControl
            value={tweaks.showBreaking ? 'on' : 'off'}
            options={[{ v: 'on', l: 'Show' }, { v: 'off', l: 'Hide' }]}
            onChange={v => onChange({ showBreaking: v === 'on' })}
          />
        </TweakField>
      </div>
    </aside>
  );
}

function TweakField({ label, children }) {
  return (
    <div className="tweak-field">
      <label className="tweak-field__label">{label}</label>
      {children}
    </div>
  );
}

function SegmentedControl({ value, options, onChange }) {
  return (
    <div className="segmented">
      {options.map(o => (
        <button
          key={o.v}
          className={"segmented__opt" + (value === o.v ? " segmented__opt--active" : "")}
          onClick={() => onChange(o.v)}
        >
          {o.l}
        </button>
      ))}
    </div>
  );
}

Object.assign(window, { App });
