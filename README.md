# drive-analyser-

A lightweight web application that generates a drive health report for HDD/SSD/NVMe devices.

## What the report includes

- Full `smartctl` details for the selected device.
- SSD/NVMe wear indicators when available.
- A heuristic health status (`Healthy`, `Warning`, `Critical`) derived from SMART signals.
- Earliest and latest file access timestamps discovered during mount-path scanning.
- Disk usage percentage and total/used/free values.
- Content usage breakdown by categories: `images`, `video`, `other`.

## Requirements

- Python 3.11+
- `smartmontools` installed (`smartctl` command available)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000` and provide:

- Device path (for example: `/dev/sda`, `/dev/nvme0n1`)
- Mount path to scan (for example: `/`, `/mnt/data`)

## Run tests

```bash
pip install pytest
pytest
```
