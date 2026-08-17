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

        return "\n".join(lines)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        minutes = int(seconds // 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
