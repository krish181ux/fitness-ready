"""Phase 1: Garmin export -> DuckDB (SPEC §6.2).

    python -m loader.load_garmin raw/api --db wearable-real.duckdb

Idempotent: rows are replaced by primary key, so re-running a load (or loading
an overlapping later export) converges rather than duplicating.
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb

from db import apply_schema, connect
from loader.discovery import classify, flatten, iter_records, json_files
from loader.fieldmap import SPECS, apply_spec, as_date, as_float, as_int

log = logging.getLogger("loader")

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily": (
        "date", "resting_hr", "min_hr", "max_hr", "steps", "intensity_minutes",
        "avg_stress", "body_battery_high", "body_battery_low", "active_calories",
    ),
    "sleep": (
        "date", "sleep_start", "sleep_end", "total_min", "deep_min", "light_min",
        "rem_min", "awake_min", "sleep_score", "avg_spo2", "avg_respiration", "validation",
    ),
    "hrv": (
        "date", "last_night_avg", "last_night_5min_high", "weekly_avg", "status",
        "baseline_low", "baseline_high",
    ),
    "activities": (
        "activity_id", "start_time", "type", "duration_min", "distance_km", "avg_hr",
        "max_hr", "calories", "aerobic_te", "anaerobic_te", "recovery_time_hours",
        "avg_speed_kmh", "elevation_gain_m", "hard_minutes",
        "body_battery_impact", "te_label", "aerobic_te_message", "anaerobic_te_message",
    ),
    "user_metrics": ("date", "metric", "value"),
}

PRIMARY_KEY: dict[str, tuple[str, ...]] = {
    "daily": ("date",),
    "sleep": ("date",),
    "hrv": ("date",),
    "activities": ("activity_id",),
    "user_metrics": ("date", "metric"),
}

# VO2 max / fitness age arrive in a wide shape; user_metrics is long (SPEC §6.2).
USER_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "vo2max": ("vo2MaxPreciseValue", "vo2MaxValue", "vo2Max", "generic.vo2MaxPreciseValue", "generic.vo2MaxValue"),
    # `currentBioAge` is what the real export's fitnessAgeData records call it.
    "fitness_age": ("fitnessAge", "achievableFitnessAge", "fitnessAgeValue", "currentBioAge"),
}
USER_METRIC_DATE_KEYS = ("calendarDate", "asOfDateGmt", "date", "generic.calendarDate")


@dataclass
class LoadStats:
    files: int = 0
    seen: Counter[str] = field(default_factory=Counter)
    written: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    unmapped: dict[str, Counter[str]] = field(default_factory=dict)

    def note_unmapped(self, table: str, keys: Iterable[str]) -> None:
        self.unmapped.setdefault(table, Counter()).update(keys)


# ------------------------------------------------------------ row derivation

def _pick_from_list(record: dict[str, Any], key: str, match: tuple[str, str], value_key: str) -> Any:
    """Read a value out of a list of typed dicts.

    The real export nests several daily metrics as `{"aggregatorList": [{"type":
    "TOTAL", ...}]}`. `flatten()` deliberately does not walk lists, so these are
    pulled out by name here rather than by widening the flattener.
    """
    entries = record.get(key)
    if not isinstance(entries, list):
        return None
    field_name, wanted = match
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get(field_name, "")).upper() == wanted:
            return entry.get(value_key)
    return None


def _derive_daily(row: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    # Stress and body battery are nested in the real export; the flat keys in
    # fieldmap.DAILY cover the shapes that put them at the top level.
    if row.get("avg_stress") is None:
        row["avg_stress"] = as_int(
            _pick_from_list(record, "allDayStress.aggregatorList", ("type", "TOTAL"), "averageStressLevel")
        )
    if row.get("body_battery_high") is None:
        row["body_battery_high"] = as_int(
            _pick_from_list(record, "bodyBattery.bodyBatteryStatList",
                            ("bodyBatteryStatType", "HIGHEST"), "statsValue")
        )
    if row.get("body_battery_low") is None:
        row["body_battery_low"] = as_int(
            _pick_from_list(record, "bodyBattery.bodyBatteryStatList",
                            ("bodyBatteryStatType", "LOWEST"), "statsValue")
        )
    if row.get("intensity_minutes") is None:
        moderate = as_int(record.get("moderateIntensityMinutes")) or 0
        vigorous = as_int(record.get("vigorousIntensityMinutes")) or 0
        if moderate or vigorous:
            # Garmin counts vigorous minutes double towards the weekly goal.
            row["intensity_minutes"] = moderate + 2 * vigorous
    return row


def _derive_sleep(row: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if row.get("total_min") is None:
        stages = [row.get(k) for k in ("deep_min", "light_min", "rem_min")]
        if any(v is not None for v in stages):
            row["total_min"] = sum(v or 0 for v in stages)
    for column in ("total_min", "deep_min", "light_min", "rem_min", "awake_min"):
        if row.get(column) is not None:
            row[column] = int(round(row[column]))
    if row.get("validation") is None:
        row["validation"] = "UNKNOWN"
    return row


# Garmin reports time-in-zone in milliseconds; the seven zones sum to `duration`.
HARD_ZONE_KEYS = ("hrTimeInZone_4", "hrTimeInZone_5", "hrTimeInZone_6")


def _derive_activities(row: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if row.get("hard_minutes") is None:
        zone_ms = [as_float(record.get(key)) for key in HARD_ZONE_KEYS]
        if any(value is not None for value in zone_ms):
            row["hard_minutes"] = round(sum(value or 0.0 for value in zone_ms) / 60_000.0, 1)
    if row.get("avg_speed_kmh") is None:
        distance, duration = row.get("distance_km"), row.get("duration_min")
        if distance and duration:
            row["avg_speed_kmh"] = round(distance / (duration / 60.0), 2)
    return row


SLEEP_MEASURES = ("total_min", "deep_min", "light_min", "rem_min", "sleep_score")

DERIVERS = {"daily": _derive_daily, "sleep": _derive_sleep, "activities": _derive_activities}


def rows_from_record(table: str, record: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    """Map one raw record to zero or more table rows, plus unmapped source keys."""
    flat = flatten(record)
    if table == "user_metrics":
        return _user_metric_rows(flat)

    row, unmapped = apply_spec(flat, SPECS[table])
    derive = DERIVERS.get(table)
    if derive:
        row = derive(row, flat)
    if any(row.get(key) is None for key in PRIMARY_KEY[table]):
        return [], unmapped
    if table == "sleep" and all(row.get(key) is None for key in SLEEP_MEASURES):
        # Garmin emits a record for nights the watch was not worn, with every
        # measurement null. Loaded, it would read as a present, trustworthy night
        # (validation UNKNOWN); skipped, it stays a visible data gap.
        return [], unmapped
    return [row], unmapped


def _user_metric_rows(flat: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    day = next((as_date(flat[k]) for k in USER_METRIC_DATE_KEYS if flat.get(k) is not None), None)
    rows: list[dict[str, Any]] = []
    consumed = set(USER_METRIC_DATE_KEYS)
    for metric, keys in USER_METRIC_KEYS.items():
        consumed.update(keys)
        value = next((as_float(flat[k]) for k in keys if flat.get(k) is not None), None)
        if day is not None and value is not None:
            rows.append({"date": day, "metric": metric, "value": value})
    unmapped = {k for k, v in flat.items() if k not in consumed and v is not None}
    return rows, unmapped


# ----------------------------------------------------------------- writing

def _upsert(conn: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]], loaded_at: datetime) -> int:
    if not rows:
        return 0
    columns = TABLE_COLUMNS[table]
    payload = [
        tuple(row.get(column) for column in columns) + ("garmin", loaded_at)
        for row in rows
    ]
    placeholders = ", ".join("?" * (len(columns) + 2))
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}, source, loaded_at) "
        f"VALUES ({placeholders})",
        payload,
    )
    return len(payload)


def load(root: Path, conn: duckdb.DuckDBPyConnection) -> LoadStats:
    apply_schema(conn)
    loaded_at = datetime.now()
    stats = LoadStats()

    for path in json_files(root):
        table = classify(path, root)
        if table is None:
            continue
        stats.files += 1
        rows: list[dict[str, Any]] = []
        unmapped: set[str] = set()
        seen = 0
        for record in iter_records(path):
            seen += 1
            mapped, missing = rows_from_record(table, record)
            if not mapped:
                stats.skipped[table] += 1
            rows.extend(mapped)
            unmapped |= missing

        # Last write wins within a file: de-duplicate on the primary key so
        # executemany never hits two rows with the same PK in one statement.
        keyed = {tuple(row[k] for k in PRIMARY_KEY[table]): row for row in rows}
        written = _upsert(conn, table, list(keyed.values()), loaded_at)

        stats.seen[table] += seen
        stats.written[table] += written
        stats.note_unmapped(table, unmapped)
        conn.execute(
            "INSERT INTO load_log VALUES (?, ?, ?, ?, ?, ?)",
            [loaded_at, table, str(path.relative_to(root)), seen, written, ",".join(sorted(unmapped))],
        )
        log.info("%-13s %-55s seen=%d written=%d", table, path.name, seen, written)
        if unmapped:
            log.warning("%-13s unmapped fields: %s", table, ", ".join(sorted(unmapped))[:400])

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="raw", type=Path, help="unzipped export root")
    parser.add_argument("--db", default=None, help="DuckDB file (default: $WEARABLE_DB or ./wearable-real.duckdb)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    with connect(args.db) as conn:
        stats = load(args.root, conn)
        print(f"\nloaded {stats.files} files into {args.db or 'wearable.duckdb'}")
        for table in TABLE_COLUMNS:
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            skipped = stats.skipped.get(table, 0)
            note = f"  ({skipped} records skipped: no key or no measurements)" if skipped else ""
            print(f"  {table:<13} {count:>6} rows{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
