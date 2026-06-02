"""
Indicted pipeline — federal crime video automation.

Modules:
    db          SQLite schema + connection helper for crime.db
    feeds       Federal news feed registry (DOJ / FBI / etc.)
    harvester   Polls every enabled feed and inserts new events
"""
