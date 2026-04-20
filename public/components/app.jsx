// Main App for ainews.hotloop.ai. Reads /data/items.json, normalizes,
// filters/searches client-side, renders the design-system feed.

const ALL_TAGS = ["Models", "Agents", "Automation", "Coding", "Skills", "Content", "Governance"];
const ALL_REGIONS = [{ id: 'us', label: 'US' }, { id: 'cn', label: 'CN' }, { id: 'eu', label: 'EU' }];
const ALL_TIERS = [{ id: 1, label: 'T1' }, { id: 2, label: 'T2' }, { id: 3, label: 'T3' }];

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
  const [activeTiers, setActiveTiers] = React.useState(new Set());
  const [windowId, setWindowId] = React.useState('24h');
  const [query, setQuery] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [refreshState, setRefreshState] = React.useState(null); // null | 'sent' | 'error'
  const [lastRefresh, setLastRefresh] = React.useState(new Date());
  const [showBreaking, setShowBreaking] = React.useState(true);
  const [tweaks, setTweaks] = React.useState(() => window.TWEAK_DEFAULTS || { dark: false, density: 'comfortable', layout: 'grid', showBreaking: true });
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

  React.useEffect(() => { loadItems(); }, []);

  async function loadItems() {
    setLoading(true);
    try {
      const res = await fetch('/data/items.json', { cache: 'no-store' });
      const raw = await res.json();
      const arr = Array.isArray(raw) ? raw : (raw.items || []);
      setItems(arr.map(window.normalizeItem));
      setLastRefresh(new Date());
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
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: patch }, '*');
  };

  const tagCounts = React.useMemo(() => {
    const c = {};
    ALL_TAGS.forEach(t => c[t] = 0);
    items.forEach(i => i.tags.forEach(t => { if (c[t] !== undefined) c[t]++; }));
    return c;
  }, [items]);

  const fuse = React.useMemo(() => window.makeSearchIndex(items), [items]);

  const filtered = React.useMemo(() => {
    const win = TIME_WINDOWS.find(w => w.id === windowId) || TIME_WINDOWS[3];
    let pool = items
      .filter(i => i.timeAgoMins <= win.maxMins)
      .filter(i => !activeTag || i.tags.includes(activeTag))
      .filter(i => activeRegions.size === 0 || (i.region && activeRegions.has(i.region)))
      .filter(i => activeTiers.size === 0 || (i.source_tier && activeTiers.has(i.source_tier)));
    if (query.trim() && fuse) {
      const hits = new Set(fuse.search(query.trim()).map(r => r.item.id));
      pool = pool.filter(i => hits.has(i.id));
    }
    return pool.sort((a, b) => a.timeAgoMins - b.timeAgoMins);
  }, [items, activeTag, activeRegions, activeTiers, windowId, query, fuse]);

  const breaking = React.useMemo(() => {
    return [...items]
      .filter(i => i.timeAgoMins < 60 * 24)
      .sort((a, b) => {
        if (b.importance !== a.importance) return b.importance - a.importance;
        return a.timeAgoMins - b.timeAgoMins;
      })
      .slice(0, 5);
  }, [items]);

  const breakingIds = new Set(breaking.map(b => b.id));
  const feedItems = showBreaking ? filtered.filter(i => !breakingIds.has(i.id)) : filtered;

  const handleRefresh = async () => {
    setRefreshState(null);
    setLoading(true);
    try {
      const res = await fetch('/api/refresh', { method: 'POST' });
      setRefreshState(res.ok ? 'sent' : 'error');
    } catch (err) {
      console.error('Refresh ping failed', err);
      setRefreshState('error');
    } finally {
      await loadItems();
    }
  };

  const toggleSet = (set, value) => {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    return next;
  };

  return (
    <div className="app" data-density={tweaks.density} data-layout={tweaks.layout}>
      <TopBar onRefresh={handleRefresh} loading={loading} lastRefresh={lastRefresh} refreshState={refreshState} query={query} onQuery={setQuery} />
      <main className="main">
        <div className="main__inner">
          <PageHeader total={items.length} fresh={items.filter(i => i.timeAgoMins < 60).length} />

          {showBreaking && tweaks.showBreaking !== false && breaking.length > 0 && (
            <BreakingStrip items={breaking} />
          )}

          <FilterBar
            tags={ALL_TAGS}
            tagCounts={tagCounts}
            activeTag={activeTag}
            onTagClick={t => setActiveTag(activeTag === t ? null : t)}
            windowId={windowId}
            onWindowChange={setWindowId}
            windows={TIME_WINDOWS}
            count={feedItems.length}
            regions={ALL_REGIONS}
            activeRegions={activeRegions}
            onRegionToggle={r => setActiveRegions(s => toggleSet(s, r))}
            tiers={ALL_TIERS}
            activeTiers={activeTiers}
            onTierToggle={t => setActiveTiers(s => toggleSet(s, t))}
          />

          {feedItems.length === 0 ? (
            <EmptyState
              hasItems={items.length > 0}
              onReset={() => { setActiveTag(null); setActiveRegions(new Set()); setActiveTiers(new Set()); setWindowId('all'); setQuery(''); }}
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

      {tweaksOpen && <TweaksPanel tweaks={tweaks} onChange={updateTweak} onClose={() => setTweaksOpen(false)} />}
    </div>
  );
}

function TopBar({ onRefresh, loading, lastRefresh, refreshState, query, onQuery }) {
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000);
    return () => clearInterval(id);
  }, []);
  const ago = Math.max(0, Math.floor((Date.now() - lastRefresh.getTime()) / 60000));
  const refreshLabel =
    refreshState === 'sent' ? 'Pinged your phone' :
    refreshState === 'error' ? 'Refresh failed' :
    loading ? 'Refreshing…' :
    (ago === 0 ? 'Just refreshed' : `Refreshed ${ago}m ago`);
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
        <div className="topbar__search">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
          <input
            type="search"
            placeholder="Search titles, summaries, tags…"
            value={query}
            onChange={e => onQuery(e.target.value)}
            aria-label="Search"
          />
        </div>
        <div className="topbar__actions">
          <span className="topbar__refresh-time">{refreshLabel}</span>
          <button className={"btn btn--primary" + (loading ? " btn--loading" : "")} onClick={onRefresh} disabled={loading}>
            {loading ? (
              <svg className="spin" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-6.22-8.56"/><path d="M21 3v6h-6"/></svg>
            )}
            Refresh
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

function FilterBar({
  tags, tagCounts, activeTag, onTagClick,
  windowId, onWindowChange, windows, count,
  regions, activeRegions, onRegionToggle,
  tiers, activeTiers, onTierToggle,
}) {
  return (
    <div className="filterbar">
      <div className="filterbar__row">
        <div className="filterbar__chips">
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
      </div>
      <div className="filterbar__row filterbar__row--meta">
        <div className="filterbar__windows">
          <span className="filterbar__meta-label">Time</span>
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
        <div className="filterbar__windows">
          <span className="filterbar__meta-label">Region</span>
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
        <div className="filterbar__windows">
          <span className="filterbar__meta-label">Tier</span>
          {tiers.map(t => (
            <button
              key={t.id}
              className={"window-btn" + (activeTiers.has(t.id) ? " window-btn--active" : "")}
              onClick={() => onTierToggle(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="filterbar__count">
          <span className="filterbar__count-num">{count}</span> stor{count === 1 ? 'y' : 'ies'} showing
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onReset, hasItems }) {
  return (
    <div className="empty">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>
      </svg>
      <h3>{hasItems ? "No stories match these filters." : "No items yet — first run hasn't completed."}</h3>
      <p>{hasItems ? "Try a different tag, region, or widen the time window." : "The 6am NZT routine will populate this. Or trigger a refresh to ping yourself."}</p>
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
