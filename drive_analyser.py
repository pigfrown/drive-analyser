from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".heic",
    ".svg",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
}


@dataclass
class ScanSummary:
    earliest_access: datetime | None
    latest_access: datetime | None
    category_sizes: dict[str, int]


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{size} B"


def classify_extension(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return "images"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "other"


def scan_filesystem_usage(mount_path: str) -> ScanSummary:
    earliest_ts: float | None = None
    latest_ts: float | None = None
    category_sizes = {"images": 0, "video": 0, "other": 0}

    for root, _, files in os.walk(mount_path):
        for file_name in files:
            file_path = Path(root) / file_name
            try:
                stats = file_path.stat()
            except (PermissionError, FileNotFoundError, OSError):
                continue

            if earliest_ts is None or stats.st_atime < earliest_ts:
                earliest_ts = stats.st_atime
            if latest_ts is None or stats.st_atime > latest_ts:
                latest_ts = stats.st_atime

            category = classify_extension(file_path)
            category_sizes[category] += stats.st_size

    earliest = datetime.fromtimestamp(earliest_ts, timezone.utc) if earliest_ts else None
    latest = datetime.fromtimestamp(latest_ts, timezone.utc) if latest_ts else None
    return ScanSummary(earliest_access=earliest, latest_access=latest, category_sizes=category_sizes)


def run_smartctl(device: str) -> dict[str, Any]:
    command = ["smartctl", "-a", "-j", device]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        stderr = result.stderr.strip() or "smartctl failed without stderr output"
        raise RuntimeError(stderr)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unable to parse smartctl JSON output") from exc


def extract_ssd_wear_info(smartctl_data: dict[str, Any]) -> dict[str, Any]:
    wear_details: dict[str, Any] = {}

    if "nvme_smart_health_information_log" in smartctl_data:
        nvme_log = smartctl_data["nvme_smart_health_information_log"]
        for key in (
            "percentage_used",
            "available_spare",
            "available_spare_threshold",
            "media_errors",
            "critical_warning",
        ):
            if key in nvme_log:
                wear_details[key] = nvme_log[key]

    ata_table = smartctl_data.get("ata_smart_attributes", {}).get("table", [])
    for attribute in ata_table:
        name = str(attribute.get("name", "")).lower()
        if any(marker in name for marker in ("wear", "lifetime", "used", "erase", "remaining")):
            wear_details[attribute.get("name", "unknown_attribute")] = {
                "value": attribute.get("value"),
                "raw": attribute.get("raw", {}).get("value"),
                "thresh": attribute.get("thresh"),
            }

    return wear_details


def derive_health_status(smartctl_data: dict[str, Any], ssd_wear: dict[str, Any]) -> tuple[str, list[str]]:
    issues: list[str] = []

    passed = smartctl_data.get("smart_status", {}).get("passed")
    if passed is False:
        issues.append("SMART overall-health test failed")

    nvme = smartctl_data.get("nvme_smart_health_information_log", {})
    if isinstance(nvme.get("critical_warning"), int) and nvme["critical_warning"] > 0:
        issues.append(f"NVMe critical warning flag is set ({nvme['critical_warning']})")

    if isinstance(nvme.get("percentage_used"), int):
        percentage_used = nvme["percentage_used"]
        if percentage_used >= 95:
            issues.append(f"NVMe percentage_used is high ({percentage_used}%)")

    ata_attributes = smartctl_data.get("ata_smart_attributes", {}).get("table", [])
    for attribute in ata_attributes:
        name = str(attribute.get("name", "")).lower()
        raw_value = attribute.get("raw", {}).get("value")
        if raw_value is None:
            continue
        if "reallocated_sector" in name and int(raw_value) > 0:
            issues.append(f"Reallocated sectors detected ({raw_value})")
        if "current_pending_sector" in name and int(raw_value) > 0:
            issues.append(f"Current pending sectors detected ({raw_value})")

    if "percentage_used" in ssd_wear and isinstance(ssd_wear["percentage_used"], int):
        if ssd_wear["percentage_used"] >= 95:
            issues.append("SSD wear level indicates near end-of-life")

    if not issues:
        return "Healthy", []
    if len(issues) <= 2:
        return "Warning", issues
    return "Critical", issues


def build_report(device: str, mount_path: str) -> dict[str, Any]:
    smartctl_data = run_smartctl(device)
    wear_details = extract_ssd_wear_info(smartctl_data)
    health, issues = derive_health_status(smartctl_data, wear_details)
    scan_summary = scan_filesystem_usage(mount_path)
    total, used, free = shutil.disk_usage(mount_path)

    return {
        "device": device,
        "mount_path": mount_path,
        "smartctl_details": smartctl_data,
        "ssd_wear": wear_details,
        "health": health,
        "health_issues": issues,
        "earliest_access": scan_summary.earliest_access.isoformat() if scan_summary.earliest_access else "N/A",
        "latest_access": scan_summary.latest_access.isoformat() if scan_summary.latest_access else "N/A",
        "usage": {
            "used_percent": round((used / total) * 100, 2) if total else 0.0,
            "total": format_bytes(total),
            "used": format_bytes(used),
            "free": format_bytes(free),
        },
        "categories": {
            key: {"bytes": value, "formatted": format_bytes(value)}
            for key, value in scan_summary.category_sizes.items()
        },
    }


def render_html(report: dict[str, Any] | None = None, error: str | None = None) -> str:
    error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    report_html = ""

    if report:
        issues = "".join(f"<li>{html.escape(issue)}</li>" for issue in report["health_issues"])
        issues_html = f"<ul>{issues}</ul>" if issues else "<p>No critical SMART signals detected.</p>"

        report_html = f"""
        <div class='card'>
          <h2>General Health</h2>
          <p class='health-{report['health'].lower()}'>{report['health']}</p>
          {issues_html}
        </div>
        <div class='card'><h2>Filesystem Activity</h2><table>
          <tr><th>Earliest Access Time</th><td>{html.escape(report['earliest_access'])}</td></tr>
          <tr><th>Latest Access Time</th><td>{html.escape(report['latest_access'])}</td></tr>
        </table></div>
        <div class='card'><h2>Disk Usage</h2><table>
          <tr><th>Used %</th><td>{report['usage']['used_percent']}%</td></tr>
          <tr><th>Total</th><td>{report['usage']['total']}</td></tr>
          <tr><th>Used</th><td>{report['usage']['used']}</td></tr>
          <tr><th>Free</th><td>{report['usage']['free']}</td></tr>
        </table></div>
        <div class='card'><h2>Content Breakdown</h2><table>
          <tr><th>Category</th><th>Size</th></tr>
          <tr><td>Images</td><td>{report['categories']['images']['formatted']}</td></tr>
          <tr><td>Video</td><td>{report['categories']['video']['formatted']}</td></tr>
          <tr><td>Other</td><td>{report['categories']['other']['formatted']}</td></tr>
        </table></div>
        <div class='card'><h2>SSD / NVMe Wear Information</h2>
          <pre>{html.escape(json.dumps(report['ssd_wear'], indent=2))}</pre></div>
        <div class='card'><h2>SMARTCTL Details</h2>
          <pre>{html.escape(json.dumps(report['smartctl_details'], indent=2))}</pre></div>
        """

    return f"""
    <!doctype html>
    <html><head><title>Drive Analyser</title>
    <style>
      body {{ font-family: Arial,sans-serif; margin:2rem; background:#f5f7fb; }}
      .card {{ background:white; border-radius:12px; box-shadow:0 3px 10px rgba(0,0,0,.08); padding:1.25rem; margin-bottom:1rem; }}
      input {{ padding:.5rem; margin-right:.5rem; min-width:260px; }}
      button {{ padding:.55rem 1rem; }} .error {{ color:#b00020; font-weight:700; }}
      pre {{ max-height:320px; overflow:auto; background:#1f2937; color:#e5e7eb; padding:1rem; border-radius:8px; }}
      table {{ width:100%; border-collapse:collapse; }}
      td,th {{ border-bottom:1px solid #e5e7eb; padding:.45rem; text-align:left; }}
      .health-healthy {{ color:#0f766e; font-weight:700; }} .health-warning {{ color:#ca8a04; font-weight:700; }} .health-critical {{ color:#b91c1c; font-weight:700; }}
    </style></head><body>
      <h1>Drive Analyser Report</h1>
      <div class='card'>
        <form method='post'>
          <label>Device <input name='device' type='text' placeholder='/dev/sda' required></label>
          <label>Mount Path <input name='mount_path' type='text' placeholder='/' required></label>
          <button type='submit'>Generate Report</button>
        </form>
        {error_html}
      </div>
      {report_html}
    </body></html>
    """
