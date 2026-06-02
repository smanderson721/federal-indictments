# The Verdict

Automated pipeline that turns U.S. federal indictments and DOJ press
releases into 1080×1920 YouTube Shorts.

Stages:

1. **Harvest** — Polls DOJ / FBI / DEA / ATF / ICE / USMS RSS feeds.
2. **Score** — Gemini ranks each new event 0–100 for video-worthiness.
3. **Research** — Top-scored events are expanded into structured
   `case_file.json` (defendants, charges, court, key facts).
4. **Script** — Narration is written, then converted to `script.json`.
5. **Produce** — Images fetched (mugshot via Wikipedia, doc page
   synthesized with PIL, street view from Google Static Street View),
   then scenes rendered via Puppeteer + ffmpeg.

## Local usage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd d3_scenes && npm install && cd ..

# In .env or shell:
#   GEMINI_API_KEY=...
#   GOOGLE_MAPS_API_KEY=...   (optional; falls back to OSM tiles)

python pipeline.py harvest
python pipeline.py score
python pipeline.py research --limit 3
python pipeline.py script projects/crime-<slug>
python pipeline.py produce projects/crime-<slug>
```

## GitHub Actions

- `.github/workflows/harvest.yml` — runs every 6 hours, harvests +
  scores new events, commits the updated SQLite DB back to the repo.
- `.github/workflows/render.yml` — `workflow_dispatch` with a project
  slug input. Builds the full pipeline for one case and uploads the
  resulting mp4 as a workflow artifact (90-day retention, set lower if
  desired).

Secrets required on the repo (Settings → Secrets and variables → Actions):
`GEMINI_API_KEY`, `GOOGLE_MAPS_API_KEY`.
