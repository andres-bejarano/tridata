"""Turn stored data into files ready to hand to Claude.

`Exporter` is an abstract base so adding a new output format later
(e.g. a direct MCP resource, or a CSV for spreadsheets) means adding one
small subclass, not touching the sync logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
import json


class Exporter(ABC):
    """Base class for anything that can render the exported dataset to disk."""

    file_extension: str = ""

    @abstractmethod
    def render(self, data: dict[str, Any]) -> str:
        """Return the file contents as a string."""

    def export(self, data: dict[str, Any], out_path: Path | str) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.render(data), encoding="utf-8")
        return out_path


class JSONExporter(Exporter):
    """Full-fidelity machine-readable export."""

    file_extension = "json"

    def render(self, data: dict[str, Any]) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False)


class MarkdownExporter(Exporter):
    """Human/LLM-friendly summary — this is the one meant to be pasted into Claude."""

    file_extension = "md"

    def render(self, data: dict[str, Any]) -> str:
        lines: list[str] = ["# Garmin Connect data export", ""]
        lines += self._render_activities(data)
        lines += self._render_daily_stats(data)
        lines += self._render_sleep(data)
        lines += self._render_hrv(data)
        lines += self._render_vo2max(data)
        lines += self._render_training_readiness(data)
        lines += self._render_training_status(data)
        lines += self._render_body_battery(data)
        lines += self._render_spo2(data)
        lines += self._render_respiration(data)
        lines += self._render_floors(data)
        lines += self._render_intensity_minutes(data)
        lines += self._render_hydration(data)
        lines += self._render_personal_records(data)
        lines += self._render_race_predictions(data)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Section renderers — each returns a list[str] ready to extend into
    # the parent lines list. Subclasses pick which ones to include.
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_activity_line(a: dict) -> str:
        return (
            f"- **{a['activity_date']}** — {a['name']} ({a['activity_type']}), "
            f"{MarkdownExporter._fmt_duration(a['duration_seconds'])}"
            + (f", {a['distance_meters'] / 1000:.2f} km" if a.get("distance_meters") else "")
            + (f", avg HR {a['avg_hr']:.0f}" if a.get("avg_hr") else "")
            + (f", {MarkdownExporter._fmt_pace(a['avg_pace_seconds_per_km'])}/km" if a.get("avg_pace_seconds_per_km") else "")
            + (f", {a['avg_cadence']:.0f} spm" if a.get("avg_cadence") else "")
            + (f", stride {a['avg_stride_length_cm']:.0f} cm" if a.get("avg_stride_length_cm") else "")
            + (f", +{a['elevation_gain_m']:.0f}m" if a.get("elevation_gain_m") else "")
        )

    def _render_activities(self, data: dict[str, Any]) -> list[str]:
        activities = data.get("activities", [])
        lines = [f"## Activities ({len(activities)})"]
        for a in activities:
            lines.append(self._fmt_activity_line(a))
        lines.append("")
        return lines

    def _render_daily_stats(self, data: dict[str, Any]) -> list[str]:
        stats = data.get("daily_stats", [])
        lines = [f"## Daily stats ({len(stats)} days)"]
        for s in stats:
            lines.append(
                f"- **{s['stat_date']}** — steps: {s.get('steps')}, "
                f"resting HR: {s.get('resting_hr')}, stress avg: {s.get('stress_avg')}"
            )
        lines.append("")
        return lines

    def _render_sleep(self, data: dict[str, Any]) -> list[str]:
        sleep = data.get("sleep", [])
        lines = [f"## Sleep ({len(sleep)} nights)"]
        for s in sleep:
            total_h = (s.get("total_sleep_seconds") or 0) / 3600
            lines.append(f"- **{s['sleep_date']}** — {total_h:.1f}h total, score: {s.get('sleep_score')}")
        lines.append("")
        return lines

    def _render_hrv(self, data: dict[str, Any]) -> list[str]:
        hrv = data.get("hrv", [])
        lines = [f"## HRV ({len(hrv)} nights)"]
        for h in hrv:
            lines.append(
                f"- **{h['hrv_date']}** — avg: {h.get('last_night_avg')}, status: {h.get('status')}"
            )
        lines.append("")
        return lines

    def _render_vo2max(self, data: dict[str, Any]) -> list[str]:
        vo2 = data.get("vo2max", [])
        if not vo2:
            return []
        lines = [f"## VO2max ({len(vo2)} days)"]
        for v in vo2:
            cyc = f", cycling {v['vo2max_cycling']}" if v.get("vo2max_cycling") else ""
            lines.append(f"- **{v['vo2max_date']}** — running {v.get('vo2max_running')}{cyc}")
        lines.append("")
        return lines

    def _render_training_readiness(self, data: dict[str, Any]) -> list[str]:
        readiness = data.get("training_readiness", [])
        if not readiness:
            return []
        lines = [f"## Training readiness ({len(readiness)} days)"]
        for r in readiness:
            lines.append(
                f"- **{r['readiness_date']}** — score {r.get('score')}/100 ({r.get('level')}), "
                f"sleep {r.get('sleep_score')}, recovery {r.get('recovery_time_minutes')} min, "
                f"HRV 7d avg {r.get('hrv_weekly_avg')}"
            )
        lines.append("")
        return lines

    def _render_training_status(self, data: dict[str, Any]) -> list[str]:
        status = data.get("training_status", [])
        if not status:
            return []
        _STATUS = {2: "Overstretching", 3: "Recovery", 4: "Maintaining",
                   5: "Improving", 6: "Peaking", 7: "Tapering"}
        lines = [f"## Training status ({len(status)} days)"]
        for s in status:
            code = s.get("training_status")
            label = _STATUS.get(code, str(code)) if code is not None else "—"
            lines.append(f"- **{s['status_date']}** — {label}")
        lines.append("")
        return lines

    def _render_body_battery(self, data: dict[str, Any]) -> list[str]:
        bb = data.get("body_battery", [])
        if not bb:
            return []
        lines = [f"## Body battery ({len(bb)} days)"]
        for b in bb:
            lines.append(
                f"- **{b['bb_date']}** — charged {b.get('charged')}, drained {b.get('drained')}"
            )
        lines.append("")
        return lines

    def _render_spo2(self, data: dict[str, Any]) -> list[str]:
        spo2 = data.get("spo2", [])
        if not spo2:
            return []
        lines = [f"## SpO2 ({len(spo2)} days)"]
        for s in spo2:
            lines.append(
                f"- **{s['spo2_date']}** — avg {s.get('avg_spo2')}, "
                f"sleep avg {s.get('avg_sleep_spo2')}, lowest {s.get('lowest_spo2')}"
            )
        lines.append("")
        return lines

    def _render_respiration(self, data: dict[str, Any]) -> list[str]:
        resp = data.get("respiration", [])
        if not resp:
            return []
        lines = [f"## Respiration ({len(resp)} days)"]
        for r in resp:
            lines.append(
                f"- **{r['respiration_date']}** — sleep avg {r.get('avg_sleep')} brpm, "
                f"waking avg {r.get('avg_waking')} brpm, range {r.get('lowest_value')}–{r.get('highest_value')}"
            )
        lines.append("")
        return lines

    def _render_floors(self, data: dict[str, Any]) -> list[str]:
        floors = data.get("floors", [])
        if not floors:
            return []
        lines = [f"## Floors ({len(floors)} days)"]
        for f in floors:
            lines.append(
                f"- **{f['floors_date']}** — ascended {f.get('floors_ascended')}, "
                f"descended {f.get('floors_descended')}"
            )
        lines.append("")
        return lines

    def _render_intensity_minutes(self, data: dict[str, Any]) -> list[str]:
        intensity = data.get("intensity_minutes", [])
        if not intensity:
            return []
        lines = [f"## Intensity minutes ({len(intensity)} days)"]
        for i in intensity:
            lines.append(
                f"- **{i['intensity_date']}** — moderate {i.get('weekly_moderate')} min, "
                f"vigorous {i.get('weekly_vigorous')} min (weekly total {i.get('weekly_total')}, "
                f"goal {i.get('week_goal')})"
            )
        lines.append("")
        return lines

    def _render_hydration(self, data: dict[str, Any]) -> list[str]:
        hydration = data.get("hydration", [])
        if not hydration:
            return []
        lines = [f"## Hydration ({len(hydration)} days)"]
        for h in hydration:
            lines.append(
                f"- **{h['hydration_date']}** — intake {h.get('value_ml'):.0f} ml, "
                f"goal {h.get('goal_ml'):.0f} ml, sweat loss {h.get('sweat_loss_ml'):.0f} ml"
                if h.get("value_ml") is not None and h.get("goal_ml") is not None and h.get("sweat_loss_ml") is not None else
                f"- **{h['hydration_date']}** — no data"
            )
        lines.append("")
        return lines

    def _render_personal_records(self, data: dict[str, Any]) -> list[str]:
        prs = data.get("personal_records", [])
        if not prs:
            return []
        lines = [f"## Personal records ({len(prs)})"]
        for p in prs:
            lines.append(
                f"- **{p.get('activity_type', '?')}** — {p.get('value'):.1f} "
                f"(set {p.get('pr_date', '?')})"
                if p.get("value") is not None else
                f"- **{p.get('activity_type', '?')}** — set {p.get('pr_date', '?')}"
            )
        lines.append("")
        return lines

    def _render_race_predictions(self, data: dict[str, Any]) -> list[str]:
        preds = data.get("race_predictions", [])
        if not preds:
            return []
        lines = [f"## Race predictions ({len(preds)} snapshots)"]
        for p in preds:
            lines.append(
                f"- **{p['prediction_date']}** — "
                f"5K {self._fmt_time(p.get('time_5k_seconds'))}, "
                f"10K {self._fmt_time(p.get('time_10k_seconds'))}, "
                f"HM {self._fmt_time(p.get('time_half_marathon_seconds'))}, "
                f"M {self._fmt_time(p.get('time_marathon_seconds'))}"
            )
        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Formatters
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_lap_line(lap: dict) -> str:
        """Return a single formatted lap string (no leading bullet/indent)."""
        idx = lap.get("lap_index", "?")
        dist_km = (lap["distance_meters"] / 1000) if lap.get("distance_meters") else None
        pace = MarkdownExporter._fmt_pace(lap.get("avg_pace_seconds_per_km"))
        hr = lap.get("avg_hr")
        max_hr = lap.get("max_hr")
        cadence = lap.get("avg_cadence")
        stride = lap.get("avg_stride_length_cm")
        gain = lap.get("elevation_gain_m")
        loss = lap.get("elevation_loss_m")
        itype = lap.get("intensity_type", "")

        parts = [f"Lap {idx:>2}"]
        parts.append(f"{dist_km:.2f} km" if dist_km is not None else "  —  ")
        parts.append(f"{pace}/km")
        if hr is not None:
            hr_str = f"HR {hr:.0f}"
            if max_hr is not None:
                hr_str += f" (max {max_hr:.0f})"
            parts.append(hr_str)
        if cadence is not None:
            parts.append(f"{cadence:.0f} spm")
        if stride is not None:
            parts.append(f"stride {stride:.0f} cm")
        elev_parts = []
        if gain is not None:
            elev_parts.append(f"+{gain:.0f}m")
        if loss is not None:
            elev_parts.append(f"-{loss:.0f}m")
        if elev_parts:
            parts.append(" / ".join(elev_parts))
        if itype:
            parts.append(f"[{itype}]")
        return "   ".join(parts)

    @staticmethod
    def _fmt_pace(seconds_per_km: float | None) -> str:
        if seconds_per_km is None:
            return "—"
        total = int(round(seconds_per_km))
        m, s = divmod(total, 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_time(seconds: int | None) -> str:
        if seconds is None:
            return "—"
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        minutes = int(seconds // 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


class RunningMarkdownExporter(MarkdownExporter):
    """Per-activity running export with optional per-lap breakdown.

    render() expects a list[dict] — the output of
    DataStore.get_activities_with_laps(RUNNING_TYPES) — rather than the
    full export_all() dict used by the other exporters.
    """

    file_extension = "md"

    def render(self, data: list[dict]) -> str:  # type: ignore[override]
        lines: list[str] = ["# Running activities export", ""]
        for a in data:
            lines.append(self._fmt_activity_line(a))
            for lap in a.get("laps", []):
                lines.append(f"  - {self._fmt_lap_line(lap)}")
            lines.append("")
        return "\n".join(lines)


class SwimmingMarkdownExporter(MarkdownExporter):
    """Per-activity swimming export covering pool and open-water sessions.

    render() expects a list[dict] from
    DataStore.get_activities_with_laps(SWIMMING_TYPES).
    """

    file_extension = "md"

    @staticmethod
    def _fmt_pace_per_100m(seconds_per_km: float | None) -> str:
        if not seconds_per_km:
            return "—"
        total = int(round(seconds_per_km / 10))
        m, s = divmod(total, 60)
        return f"{m}:{s:02d}/100m"

    @staticmethod
    def _fmt_swim_activity_line(a: dict) -> str:
        line = (
            f"- **{a['activity_date']}** — {a['name']} ({a['activity_type']}), "
            f"{MarkdownExporter._fmt_duration(a['duration_seconds'])}"
        )
        if a.get("distance_meters"):
            line += f", {a['distance_meters']:.0f} m"
        if a.get("avg_hr"):
            line += f", avg HR {a['avg_hr']:.0f}"
        if a.get("avg_pace_seconds_per_km"):
            line += f", {SwimmingMarkdownExporter._fmt_pace_per_100m(a['avg_pace_seconds_per_km'])}"
        if a.get("avg_swolf") is not None:
            line += f", SWOLF {a['avg_swolf']:.0f}"
        if a.get("avg_swim_cadence") is not None:
            line += f", {a['avg_swim_cadence']:.0f} spm"
        if a.get("pool_length_m") is not None:
            line += f", {a['pool_length_m']:.0f}m pool"
        if a.get("avg_water_temp_c") is not None:
            line += f", {a['avg_water_temp_c']:.0f}°C"
        return line

    @staticmethod
    def _fmt_swim_lap_line(lap: dict) -> str:
        dist_m = lap.get("distance_meters")
        pace = SwimmingMarkdownExporter._fmt_pace_per_100m(lap.get("avg_pace_seconds_per_km"))
        hr = lap.get("avg_hr")
        max_hr = lap.get("max_hr")
        swolf = lap.get("avg_swolf")
        cadence = lap.get("avg_swim_cadence")
        strokes = lap.get("total_strokes")
        stroke = lap.get("swim_stroke")

        parts = [f"Lap {lap.get('lap_index', '?'):>2}"]
        parts.append(f"{dist_m:.0f} m" if dist_m is not None else "—")
        parts.append(pace)
        if hr is not None:
            hr_str = f"HR {hr:.0f}"
            if max_hr is not None:
                hr_str += f" (max {max_hr:.0f})"
            parts.append(hr_str)
        if swolf is not None:
            parts.append(f"SWOLF {swolf:.0f}")
        if cadence is not None:
            parts.append(f"{cadence:.0f} spm")
        if strokes is not None:
            parts.append(f"{strokes} strokes")
        if stroke:
            parts.append(f"[{stroke}]")
        elif lap.get("intensity_type"):
            parts.append(f"[{lap['intensity_type']}]")
        return "   ".join(parts)

    def render(self, data: list[dict]) -> str:  # type: ignore[override]
        lines: list[str] = ["# Swimming activities export", ""]
        for a in data:
            lines.append(self._fmt_swim_activity_line(a))
            for lap in a.get("laps", []):
                lines.append(f"  - {self._fmt_swim_lap_line(lap)}")
            lines.append("")
        return "\n".join(lines)


class CyclingMarkdownExporter(MarkdownExporter):
    """Per-activity cycling export with optional per-lap breakdown.

    render() expects a list[dict] — the output of
    DataStore.get_activities_with_laps(CYCLING_TYPES).
    Shows speed in km/h instead of pace; omits cadence and stride (no
    pedalling sensor on this profile).
    """

    file_extension = "md"

    @staticmethod
    def _fmt_speed_kmh(seconds_per_km: float | None) -> str:
        if not seconds_per_km:
            return "—"
        return f"{3600 / seconds_per_km:.1f} km/h"

    @staticmethod
    def _fmt_cycling_activity_line(a: dict) -> str:
        line = (
            f"- **{a['activity_date']}** — {a['name']} ({a['activity_type']}), "
            f"{MarkdownExporter._fmt_duration(a['duration_seconds'])}"
        )
        if a.get("distance_meters"):
            line += f", {a['distance_meters'] / 1000:.2f} km"
        if a.get("avg_hr"):
            line += f", avg HR {a['avg_hr']:.0f}"
        if a.get("avg_pace_seconds_per_km"):
            line += f", {CyclingMarkdownExporter._fmt_speed_kmh(a['avg_pace_seconds_per_km'])}"
        elev_parts = []
        if a.get("elevation_gain_m"):
            elev_parts.append(f"+{a['elevation_gain_m']:.0f}m")
        if a.get("elevation_loss_m"):
            elev_parts.append(f"-{a['elevation_loss_m']:.0f}m")
        if elev_parts:
            line += ", " + " / ".join(elev_parts)
        return line

    def render(self, data: list[dict]) -> str:  # type: ignore[override]
        lines: list[str] = ["# Cycling activities export", ""]
        for a in data:
            lines.append(self._fmt_cycling_activity_line(a))
            for lap in a.get("laps", []):
                lines.append(f"  - {self._fmt_lap_line(lap)}")
            lines.append("")
        return "\n".join(lines)


class MetricsMarkdownExporter(MarkdownExporter):
    """Daily metrics export — all sections except individual activities."""

    file_extension = "md"

    def render(self, data: dict[str, Any]) -> str:
        lines: list[str] = ["# Garmin Connect metrics export", ""]
        lines += self._render_daily_stats(data)
        lines += self._render_sleep(data)
        lines += self._render_hrv(data)
        lines += self._render_vo2max(data)
        lines += self._render_training_readiness(data)
        lines += self._render_training_status(data)
        lines += self._render_body_battery(data)
        lines += self._render_spo2(data)
        lines += self._render_respiration(data)
        lines += self._render_floors(data)
        lines += self._render_intensity_minutes(data)
        lines += self._render_hydration(data)
        lines += self._render_personal_records(data)
        lines += self._render_race_predictions(data)
        return "\n".join(lines)
