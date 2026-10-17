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

The repo includes `daily_update.sh`, a single script that runs the full
pipeline (`tridata sync`, laps, weather, and every `export-*`) and logs
everything with a timestamp to `sync.log`. Run it daily (see below) and it
takes care of two things by itself:

- **Short daily revalidation** (cheap): re-checks the last few days even
  where a row already exists, in case Garmin finalized a day's totals
  (steps, calories) *after* its own calendar date — see
  `SyncService.RECENT_REVALIDATE_DAYS`.
- **Wide revalidation sweep** (occasional): every ~7 days, the same script
  automatically runs `tridata sync --revalidate-days 45` instead of the
  short version, catching a day that slipped past the short window before
  anyone noticed (this happened for real: 2026-08-20 and 2026-08-24 sat
  wrong for over two weeks). It tracks the last successful wide sweep in
  `~/.tridata/last_wide_sync` — no separate script or second timer needed.
  If Garmin's login flakes transiently (it does, occasionally, with
  "Failed to retrieve social profile" — unrelated to your password), each
  step retries automatically before giving up; if a wide sweep still fails
  after retries, it simply tries again on the next daily run instead of
  waiting a whole week.

### Linux (systemd — recommended)

Ideal for laptops: if the machine is off at the scheduled time, the job runs
automatically on next boot.

```bash
# Copy the unit files
cp daily_update.sh ~/projects/tridata/
chmod +x ~/projects/tridata/daily_update.sh
mkdir -p ~/.config/systemd/user
cp .config/systemd/user/tridata-sync.* ~/.config/systemd/user/

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now tridata-sync.timer
```

The timer fires every day at **08:00**. If the computer was off at that time,
it runs as soon as you log in.

Check status and logs:

```bash
systemctl --user status tridata-sync.timer   # next trigger time
journalctl --user -u tridata-sync.service    # execution log
tail -20 ~/projects/tridata/sync.log         # script output log
```

### macOS / Linux (cron fallback)

```bash
crontab -e
# Run every day at 08:00
0 8 * * * /path/to/tridata/daily_update.sh
```

Note: cron does not recover missed runs if the machine was off.

## Project structure

```
src/tridata/
├── garmin_client.py   # GarminClient — auth + typed fetch methods
├── models.py           # Activity, DailyStats, SleepRecord, HRVRecord
├── storage.py           # DataStore — local SQLite repository
├── exporters.py         # Exporter (abstract) -> JSONExporter, MarkdownExporter
├── sync.py               # SyncService — orchestrates incremental sync
├── mcp_server.py         # MCP server — read-only query tools over the store
└── cli.py                 # command-line entry point
```

## Backups

`daily_update.sh` starts every run with `tridata backup`: a consistent,
integrity-checked copy of the SQLite store (plus any `--extra` files, e.g.
a food diary that can't be re-downloaded from Garmin) in
`~/.tridata/backups/<timestamp>/`, keeping the last 14. They're on the
same disk, so they protect against a bad sync or a mistake, not against
losing the machine.

## Development

```bash
pip install -e ".[dev]"
./scripts/check.sh   # ruff (incl. security rules), mypy, pytest, pip-audit — same as CI
```

## Querying from Claude (MCP)

Attaching the full exports to every conversation costs ~250K+ tokens.
The MCP server lets Claude fetch only the slice it needs instead (a date
range, one activity, a weekly summary — typically a few hundred to ~2K
tokens per call), which stretches a Claude subscription's usage limits a
lot further.

```bash
pip install -e ".[mcp]"
claude mcp add --scope user tridata -- "$(which tridata-mcp)"
```

Tools (all read-only): `list_activities`, `get_activity`,
`get_daily_metrics`, `get_training_load`, `get_weekly_summary`,
`get_records`. The server reads the same SQLite store the daily sync
writes, so it's always as fresh as the last `daily_update.sh` run.

## Roadmap

- [x] v1: CLI sync + export to Markdown/JSON
- [ ] v2: ready-made analysis prompt templates (training load, VO2max trend...)
- [ ] v3: lightweight dashboard / historical comparisons

## License

MIT — see [LICENSE](LICENSE).
