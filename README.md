# tridata

Pull your own Garmin Connect data (activities, sleep, HRV, daily stats) and
hand it to Claude for analysis — free, local, and yours. No TrainingPeaks
subscription, no Strava premium, no third-party connector required.

## Why

Garmin Connect already has all your training data. This tool exports it into
a clean, structured file you can paste straight into Claude (or attach) and
ask questions like *"how has my resting heart rate trended since I started
marathon training?"* or *"summarize my training load this month."*

It keeps a local SQLite database of everything it has pulled, so:
- re-runs only fetch what's new (no re-downloading a year of data every time)
- you can build up a full history over time and query all of it at once

## Install

```bash
git clone https://github.com/andres-bejarano/tridata.git
cd tridata
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env with your Garmin Connect email/password
```

Your credentials stay local in `.env` (git-ignored) and are only used to
authenticate directly against Garmin Connect via
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect) /
[`garth`](https://github.com/matin/garth). Nothing is sent anywhere else.

## Usage

Backfill your history (first run):

```bash
tridata sync --since 2026-02-01
```

Daily catch-up (only fetches what's missing):

```bash
tridata sync
```

Export everything to a Claude-ready Markdown file:

```bash
tridata export --format markdown --out garmin_export.md
```

Then paste `garmin_export.md` into a Claude conversation, or attach it, and
ask away.

## Automating the daily sync

To keep the local database fresh without running the command by hand, add a
cron job (Linux/macOS):

```bash
crontab -e
# Run every day at 23:00
0 23 * * * cd /path/to/tridata && .venv/bin/tridata sync >> sync.log 2>&1
```

On Windows, use Task Scheduler to run the same command daily.

## Project structure

```
src/tridata/
├── garmin_client.py   # GarminClient — auth + typed fetch methods
├── models.py           # Activity, DailyStats, SleepRecord, HRVRecord
├── storage.py           # DataStore — local SQLite repository
├── exporters.py         # Exporter (abstract) -> JSONExporter, MarkdownExporter
├── sync.py               # SyncService — orchestrates incremental sync
└── cli.py                 # command-line entry point
```

## Roadmap

- [x] v1: CLI sync + export to Markdown/JSON
- [ ] v2: ready-made analysis prompt templates (training load, VO2max trend...)
- [ ] v3: lightweight dashboard / historical comparisons

## License

MIT — see [LICENSE](LICENSE).
