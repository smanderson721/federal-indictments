"""Buncombe County (NC) convicted-offender pipeline.

Downloads NCDPS bulk offender tables, filters to Buncombe County
convictions, tracks every offender ever rendered in a SQLite DB so we
only render newly-appearing convictions.

Run from the repo root:

    python pipeline.py buncombe-daily

The workflow .github/workflows/buncombe.yml invokes this once a day on
public GitHub Actions runners.
"""
