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

        activities = data.get("activities", [])
        lines.append(f"## Activities ({len(activities)})")
        for a in activities:
            lines.append(
                f"- **{a['activity_date']}** — {a['name']} ({a['activity_type']}), "
                f"{self._fmt_duration(a['duration_seconds'])}"
                + (f", {a['distance_meters'] / 1000:.2f} km" if a.get("distance_meters") else "")
                + (f", avg HR {a['avg_hr']}" if a.get("avg_hr") else "")
            )
        lines.append("")

        stats = data.get("daily_stats", [])
        lines.append(f"## Daily stats ({len(stats)} days)")
        for s in stats:
            lines.append(
                f"- **{s['stat_date']}** — steps: {s.get('steps')}, "
                f"resting HR: {s.get('resting_hr')}, stress avg: {s.get('stress_avg')}"
            )
        lines.append("")

        sleep = data.get("sleep", [])
        lines.append(f"## Sleep ({len(sleep)} nights)")
        for s in sleep:
            total_h = (s.get("total_sleep_seconds") or 0) / 3600
            lines.append(f"- **{s['sleep_date']}** — {total_h:.1f}h total, score: {s.get('sleep_score')}")
        lines.append("")

        hrv = data.get("hrv", [])
        lines.append(f"## HRV ({len(hrv)} nights)")
        for h in hrv:
            lines.append(
                f"- **{h['hrv_date']}** — avg: {h.get('last_night_avg')}, status: {h.get('status')}"
            )
        lines.append("")

        vo2 = data.get("vo2max", [])
        if vo2:
            lines.append(f"## VO2max ({len(vo2)} days)")
            for v in vo2:
                cyc = f", cycling {v['vo2max_cycling']}" if v.get("vo2max_cycling") else ""
                lines.append(f"- **{v['vo2max_date']}** — running {v.get('vo2max_running')}{cyc}")
            lines.append("")

        readiness = data.get("training_readiness", [])
        if readiness:
            lines.append(f"## Training readiness ({len(readiness)} days)")
            for r in readiness:
                lines.append(
                    f"- **{r['readiness_date']}** — score {r.get('score')}/100 ({r.get('level')}), "
                    f"sleep {r.get('sleep_score')}, recovery {r.get('recovery_time_minutes')} min, "
                    f"HRV 7d avg {r.get('hrv_weekly_avg')}"
                )
            lines.append("")

        status = data.get("training_status", [])
        if status:
            lines.append(f"## Training status ({len(status)} days)")
            _STATUS = {2: "Overstretching", 3: "Recovery", 4: "Maintaining",
                       5: "Improving", 6: "Peaking", 7: "Tapering"}
            for s in status:
                code = s.get("training_status")
                label = _STATUS.get(code, str(code)) if code is not None else "—"
                lines.append(f"- **{s['status_date']}** — {label}")
            lines.append("")

        bb = data.get("body_battery", [])
        if bb:
            lines.append(f"## Body battery ({len(bb)} days)")
            for b in bb:
                lines.append(
                    f"- **{b['bb_date']}** — charged {b.get('charged')}, drained {b.get('drained')}"
                )
            lines.append("")

        spo2 = data.get("spo2", [])
        if spo2:
            lines.append(f"## SpO2 ({len(spo2)} days)")
            for s in spo2:
                lines.append(
                    f"- **{s['spo2_date']}** — avg {s.get('avg_spo2')}, "
                    f"sleep avg {s.get('avg_sleep_spo2')}, lowest {s.get('lowest_spo2')}"
                )
            lines.append("")

        resp = data.get("respiration", [])
        if resp:
            lines.append(f"## Respiration ({len(resp)} days)")
            for r in resp:
                lines.append(
                    f"- **{r['respiration_date']}** — sleep avg {r.get('avg_sleep')} brpm, "
                    f"waking avg {r.get('avg_waking')} brpm, range {r.get('lowest_value')}–{r.get('highest_value')}"
                )
            lines.append("")

        floors = data.get("floors", [])
        if floors:
            lines.append(f"## Floors ({len(floors)} days)")
            for f in floors:
                lines.append(
                    f"- **{f['floors_date']}** — ascended {f.get('floors_ascended')}, "
                    f"descended {f.get('floors_descended')}"
                )
            lines.append("")

        intensity = data.get("intensity_minutes", [])
        if intensity:
            lines.append(f"## Intensity minutes ({len(intensity)} days)")
            for i in intensity:
                lines.append(
                    f"- **{i['intensity_date']}** — moderate {i.get('weekly_moderate')} min, "
                    f"vigorous {i.get('weekly_vigorous')} min (weekly total {i.get('weekly_total')}, "
                    f"goal {i.get('week_goal')})"
                )
            lines.append("")

        hydration = data.get("hydration", [])
        if hydration:
            lines.append(f"## Hydration ({len(hydration)} days)")
            for h in hydration:
                lines.append(
                    f"- **{h['hydration_date']}** — intake {h.get('value_ml'):.0f} ml, "
                    f"goal {h.get('goal_ml'):.0f} ml, sweat loss {h.get('sweat_loss_ml'):.0f} ml"
                    if h.get("value_ml") is not None else
                    f"- **{h['hydration_date']}** — no data"
                )
            lines.append("")

        prs = data.get("personal_records", [])
        if prs:
            lines.append(f"## Personal records ({len(prs)})")
            for p in prs:
                lines.append(
                    f"- **{p.get('activity_type', '?')}** — {p.get('value'):.1f} "
                    f"(set {p.get('pr_date', '?')})"
                    if p.get("value") is not None else
                    f"- **{p.get('activity_type', '?')}** — set {p.get('pr_date', '?')}"
                )
            lines.append("")

        preds = data.get("race_predictions", [])
        if preds:
            lines.append(f"## Race predictions ({len(preds)} snapshots)")
            for p in preds:
                lines.append(
                    f"- **{p['prediction_date']}** — "
                    f"5K {self._fmt_time(p.get('time_5k_seconds'))}, "
                    f"10K {self._fmt_time(p.get('time_10k_seconds'))}, "
                    f"HM {self._fmt_time(p.get('time_half_marathon_seconds'))}, "
                    f"M {self._fmt_time(p.get('time_marathon_seconds'))}"
                )

        return "\n".join(lines)

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
