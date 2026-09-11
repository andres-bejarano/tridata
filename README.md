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

The repo includes `daily_update.sh`, a script that runs `tridata sync` and
`tridata export`, and logs everything with a timestamp to `sync.log`.

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

## Weekly wide revalidation sweep

Garmin sometimes finalizes a day's totals (steps, calories) *after* its own
calendar date, so a value cached before that happens can be wrong. The daily
sync above only re-checks the last few days for this (see
`SyncService.RECENT_REVALIDATE_DAYS`) — cheap, but it can miss a day that
stays stale for weeks (this happened for real: 2026-08-20 and 2026-08-24 sat
wrong for over two weeks before anyone noticed).

`weekly_wide_sync.sh` covers that gap: it runs `tridata sync
--revalidate-days 45` (re-checking the last 45 days even where a row already
exists) and then regenerates every export, once a week.

### Linux (systemd)

```bash
chmod +x ~/projects/tridata/weekly_wide_sync.sh
mkdir -p ~/.config/systemd/user
cp .config/systemd/user/tridata-wide-sync.* ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now tridata-wide-sync.timer
```

The timer fires every **Sunday at 03:00** (deliberately off-peak, away from
the daily 08:00 run), and recovers on next boot if the machine was off.

```bash
systemctl --user status tridata-wide-sync.timer   # next trigger time
journalctl --user -u tridata-wide-sync.service    # execution log
tail -30 ~/projects/tridata/sync.log              # script output log
```

### macOS / Linux (cron fallback)

```bash
crontab -e
# Run every Sunday at 03:00
0 3 * * 0 /path/to/tridata/weekly_wide_sync.sh
```

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
