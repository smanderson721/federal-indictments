"""
Federal news-feed registry for the Indicted pipeline.

Phase 1 covers only federal-level sources. State / local feeds will be
added once the pipeline is producing reliable output from the federal
slice and the user is ready to scale up.

Each entry is seeded into the `sources` SQLite table on first run by
`harvester.seed_default_sources()`. You can disable a feed afterwards
with:
    UPDATE sources SET enabled = 0 WHERE name = 'doj_main';

Notes on feed reliability (June 2026):
  * The DOJ main feed (`justice.gov/feeds/justice-news.xml`) aggregates
    press releases from all 94 US Attorney's offices + Main Justice
    component press shops. It's the single highest-signal federal feed.
  * The FBI national press feed covers FBI HQ statements. Per-field-office
    feeds exist (e.g. `fbi.gov/contact-us/field-offices/newyork/news/...`)
    but we hold off on adding all 56 until we know the main two aren't
    sufficient.
  * The USMS RSS isn't published on a stable URL anymore — fugitive data
    will be pulled directly from the wanted-person APIs in a separate
    harvester.
"""

from __future__ import annotations

DEFAULT_FEEDS: list[dict] = [
    {
        "name": "doj_main",
        "url": "https://www.justice.gov/feeds/justice-news.xml",
        "kind": "doj",
        "jurisdiction": "federal",
        "enabled": True,
    },
    {
        "name": "fbi_national",
        "url": "https://www.fbi.gov/feeds/national-press-releases/atom.xml",
        "kind": "fbi",
        "jurisdiction": "federal",
        "enabled": True,
    },
    # ATF national news feed. URL pattern stable since 2018.
    {
        "name": "atf_news",
        "url": "https://www.atf.gov/news/press-releases/rss.xml",
        "kind": "atf",
        "jurisdiction": "federal",
        # Disabled by default — verify the URL is live before enabling
        # via `UPDATE sources SET enabled=1 WHERE name='atf_news'`.
        "enabled": False,
    },
    {
        "name": "dea_news",
        "url": "https://www.dea.gov/rss.xml",
        "kind": "dea",
        "jurisdiction": "federal",
        "enabled": False,
    },
]
