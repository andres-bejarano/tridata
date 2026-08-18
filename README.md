# tridata

Pull your fitness data (activities, sleep, HRV, daily stats) from supported
platforms and hand it to Claude for analysis — free, local, and yours. No
TrainingPeaks subscription, no Strava premium, no third-party connector
required.

**Currently functional:** Garmin Connect  
**Planned (pending partner credentials):** Suunto, COROS

## Why

Your fitness platform already has all your training data. This tool exports it
into a clean, structured file you can paste straight into Claude (or attach)
and ask questions like *"how has my resting heart rate trended since I started
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
authenticate directly against the platform via
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

Both commands default to `--source garmin`. When other platforms become
available, pass `--source suunto` or `--source coros` to switch:

```bash
tridata sync --source suunto   # not yet available — requires partner API key
tridata sync --source coros    # not yet available — requires partner API key
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
cp daily_update.sh ~/projects/tridata/
chmod +x ~/projects/tridata/daily_update.sh
mkdir -p ~/.config/systemd/user
cp .config/systemd/user/tridata-sync.* ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now tridata-sync.timer
```

The timer fires every day at **08:00**. If the computer was off at that time,
it runs as soon as you log in.

Check status and logs:

```bash
systemctl --user status tridata-sync.timer
journalctl --user -u tridata-sync.service
tail -20 ~/projects/tridata/sync.log
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
├── clients/
│   ├── base.py         # FitnessClient — Protocol every brand client implements
│   ├── garmin.py       # re-export shim (implementation in garmin_client.py)
│   ├── suunto.py       # skeleton — pending Suunto partner API key
│   └── coros.py        # skeleton — pending COROS developer API approval
├── garmin_client.py    # GarminClient — auth + typed fetch methods
├── models.py           # Activity, DailyStats, SleepRecord, HRVRecord
├── storage.py          # DataStore — local SQLite repository
├── exporters.py        # JSONExporter, MarkdownExporter
├── sync.py             # SyncService — orchestrates incremental sync
└── cli.py              # command-line entry point (tridata sync / export)
```

## Adding a new platform

1. Implement the five methods of `FitnessClient` in `src/tridata/clients/<brand>.py`.
2. Register it in `src/tridata/clients/__init__.py` and `cli.py` (`_build_client`).
3. Add fixture JSON files under `tests/fixtures/<brand>_*.json`.
4. Remove the `@pytest.mark.skip` from `TestSuuntoContract` / `TestCorosContract`
   in `tests/test_client_contract.py` — all contract assertions run automatically.

## Roadmap

- [x] v1: CLI sync + export to Markdown/JSON (Garmin)
- [x] v1.1: regression tests for GarminClient and SyncService
- [x] v1.2: multi-brand `FitnessClient` architecture + Suunto/COROS skeletons
- [ ] v2: Suunto and COROS once partner credentials are obtained
- [ ] v3: ready-made analysis prompt templates (training load, VO2max trend…)
- [ ] v4: lightweight dashboard / historical comparisons

## License

MIT — see [LICENSE](LICENSE).
