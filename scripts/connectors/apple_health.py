"""
scripts/connectors/apple_health.py — Apple Health XML export parser for Artha.

Parses Apple Health export files (exported from iPhone/iPad Health app):
  iPhone → Settings → Health → Export All Health Data → export.zip

This connector is LOCAL-ONLY and OFFLINE:
  - No Apple Health API access
  - No HealthKit, no network requests
  - User places the export ZIP in any directory and runs:
      python scripts/pipeline.py --source apple_health --file path/to/export.zip

The connector extracts records from the ZIP's export.xml file and yields structured
JSONL records. Output goes to state/health.md.age (encrypted) via the AI CLI.

Record types tracked (subset of Apple Health's 100+ types):
  Weight (BMI, body mass), blood pressure systolic/diastolic, heart rate,
  step count, SpO2, blood glucose, respiratory rate.

Security:
  - Health data is PHI-adjacent; output is always directed to encrypted state.
  - PII guard scans output before AI CLI processing.
  - Import ZIP should be deleted after processing — no persistent copy.
  - No data leaves the local machine.

Memory efficiency:
  - Uses iterative XML parsing (iterparse + elem.clear()) for large exports.
  - export.xml in Apple Health ZIPs can exceed 500MB; this approach
    maintains constant memory regardless of file size.

Connector registry entry (config/connectors.yaml):
  apple_health:
    type: health
    provider: apple
    enabled: false   # opt-in: enable after setting up with --file param
    description: "Apple Health XML export parser — local file, no network"
    auth:
      method: none
    fetch:
      handler: "scripts/connectors/apple_health.py"
      default_max_results: 10000
      default_lookback: "365d"
    output:
      format: jsonl
      source_tag: "apple_health"
    health_check: false   # no network endpoint to ping

Ref: specs/improve.md §9 I-13
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator


# Health Record types to extract.
# This is a conservative subset covering the most clinically useful metrics.
# Types are Apple's HKQuantityTypeIdentifier constants.
TRACKED_TYPES: frozenset[str] = frozenset({
    # Body composition
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBodyMassIndex",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierLeanBodyMass",

    # Cardiovascular
    "HKQuantityTypeIdentifierHeartRate",
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierBloodPressureSystolic",
    "HKQuantityTypeIdentifierBloodPressureDiastolic",

    # Activity
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "HKQuantityTypeIdentifierBasalEnergyBurned",

    # Respiratory / blood
    "HKQuantityTypeIdentifierOxygenSaturation",
    "HKQuantityTypeIdentifierBloodGlucose",
    "HKQuantityTypeIdentifierRespiratoryRate",
    "HKQuantityTypeIdentifierInhalerUsage",

    # Sleep (sleep analysis uses a different type)
    "HKCategoryTypeIdentifierSleepAnalysis",
})


def _iso_to_str(raw: str) -> str:
    """
    Normalise Apple Health date strings to ISO-8601 UTC.

    Apple exports dates in local time with offset:
        "2026-03-14 08:30:00 -0700"

    We preserve the offset as-is (ISO-8601 extended format) because
    relative comparisons need the local context.
    """
    if not raw:
        return raw
    # Replace space separator with T for ISO-8601 compliance.
    # Apple format: "YYYY-MM-DD HH:MM:SS ±HHMM" → "YYYY-MM-DDTHH:MM:SS±HHMM"
    parts = raw.rsplit(" ", 1)
    if len(parts) == 2:
        return f"{parts[0].replace(' ', 'T')}{parts[1]}"
    return raw.replace(" ", "T", 1)


def _parse_since(since: str) -> str:
    """
    Convert a lookback spec to an ISO-like string for comparison.

    since can be:
      - ISO string:   "2026-01-01T00:00:00+00:00"
      - Relative:     "7d", "30d", "365d"
    """
    import re
    m = re.match(r"^(\d+)([dh])$", since.strip().lower())
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        from datetime import timedelta
        delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
        cutoff = datetime.now(timezone.utc) - delta
        return cutoff.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    # Assume already ISO string; return as-is
    return since


def fetch(
    *,
    since: str,
    max_results: int,
    auth_context: Dict[str, Any],
    source_tag: str = "apple_health",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """
    Parse Apple Health export ZIP and yield structured health records.

    Args:
        since:        ISO timestamp or relative spec ("365d"). Records before
                      this timestamp are skipped.
        max_results:  Maximum records to yield (caps large exports).
        auth_context: Unused — this connector requires no authentication.
        source_tag:   Injected into each record as "source" field.
        **kwargs:
            file (str|Path): Path to the Apple Health export ZIP or export.xml.
                             Required. Passed via pipeline --file flag.

    Yields:
        Dicts with keys:
            source, type, type_short, value, unit, date_iso, device, id
    """
    file_path_raw = kwargs.get("file") or auth_context.get("file")
    if not file_path_raw:
        raise ValueError(
            "apple_health connector requires --file path/to/export.zip (or export.xml)"
        )

    file_path = Path(str(file_path_raw))
    if not file_path.exists():
        raise FileNotFoundError(
            f"Apple Health export not found: {file_path}\n"
            "Export from: iPhone → Health app → Profile → Export All Health Data"
        )

    since_str = _parse_since(since)
    count = 0

    if file_path.suffix.lower() == ".zip":
        yield from _parse_from_zip(file_path, since_str, max_results, source_tag)
    elif file_path.suffix.lower() == ".xml":
        yield from _parse_from_xml(file_path, since_str, max_results, source_tag)
    else:
        raise ValueError(
            f"Unsupported file type: {file_path.suffix} — expected .zip or .xml"
        )


def _parse_from_zip(
    zip_path: Path,
    since_str: str,
    max_results: int,
    source_tag: str,
) -> Iterator[Dict[str, Any]]:
    """Extract XML from ZIP and parse iteratively."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Apple Health ZIP contains export.xml at "apple_health_export/export.xml"
            # or sometimes just "export.xml" at the root.
            xml_name = None
            for name in zf.namelist():
                if name.endswith("export.xml"):
                    xml_name = name
                    break

            if not xml_name:
                raise ValueError(
                    f"No export.xml found in ZIP: {zip_path}\n"
                    "The ZIP must be an Apple Health export (contains export.xml)."
                )

            with zf.open(xml_name) as xml_file:
                yield from _iterparse_health_xml(xml_file, since_str, max_results, source_tag)

    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP file: {zip_path} — {exc}") from exc


def _parse_from_xml(
    xml_path: Path,
    since_str: str,
    max_results: int,
    source_tag: str,
) -> Iterator[Dict[str, Any]]:
    """Parse a bare export.xml file."""
    with open(xml_path, "rb") as xml_file:
        yield from _iterparse_health_xml(xml_file, since_str, max_results, source_tag)


def _iterparse_health_xml(
    xml_file: Any,
    since_str: str,
    max_results: int,
    source_tag: str,
) -> Iterator[Dict[str, Any]]:
    """
    Memory-efficient iterative XML parser for Apple Health export.xml.

    Uses iterparse + elem.clear() to maintain constant memory regardless of
    export file size. Apple Health exports can exceed 500MB.
    """
    count = 0
    for event, elem in ET.iterparse(xml_file, events=("end",)):
        if count >= max_results:
            elem.clear()
            break

        if elem.tag == "Record":
            record_type = elem.get("type", "")
            if record_type not in TRACKED_TYPES:
                elem.clear()
                continue

            start_date = elem.get("startDate", "")
            if start_date < since_str[:10]:  # date prefix comparison (YYYY-MM-DD)
                elem.clear()
                continue

            raw_value = elem.get("value", "")
            try:
                numeric_value: float | str | None = float(raw_value) if raw_value else None
            except (ValueError, TypeError):
                # Category types (e.g. SleepAnalysis) have string values
                numeric_value = raw_value if raw_value else None

            if numeric_value is None:
                elem.clear()
                continue

            # Derive a short human-readable type name for briefing display
            type_short = (
                record_type
                .replace("HKQuantityTypeIdentifier", "")
                .replace("HKCategoryTypeIdentifier", "")
            )

            yield {
                "source": source_tag,
                "type": record_type,
                "type_short": type_short,
                "value": numeric_value,
                "unit": elem.get("unit", ""),
                "date_iso": _iso_to_str(start_date),
                "device": elem.get("sourceName", "unknown"),
                "id": f"ah-{record_type}-{start_date}",
            }
            count += 1

        elem.clear()  # Free memory — critical for large exports


def health_check(auth_context: Dict[str, Any]) -> bool:
    """
    No-op health check — Apple Health connector requires no network access.

    Returns True always; export file presence is validated in fetch().
    """
    return True
