// ainews.hotloop.ai — Worker entry. Pure static asset passthrough.
// The site is populated by the `ainews` routine on the Mac mini, which runs
// `wrangler deploy` after writing public/data/items.json. There is no
// runtime API — no /api/refresh, no secrets, nothing to configure.

export default {
  async fetch(request, env) {
    return env.ASSETS.fetch(request);
  },
};
