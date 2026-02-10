from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drive_analyser import classify_extension, derive_health_status, extract_ssd_wear_info


def test_classify_extension_categories() -> None:
    assert classify_extension(Path("photo.jpg")) == "images"
    assert classify_extension(Path("movie.mkv")) == "video"
    assert classify_extension(Path("archive.zip")) == "other"


def test_extract_wear_info_from_nvme_and_ata() -> None:
    smartctl_data = {
        "nvme_smart_health_information_log": {
            "percentage_used": 12,
            "available_spare": 98,
            "critical_warning": 0,
        },
        "ata_smart_attributes": {
            "table": [
                {"name": "Wear_Leveling_Count", "value": 99, "raw": {"value": 3}, "thresh": 10},
                {"name": "Power_On_Hours", "value": 90, "raw": {"value": 1200}, "thresh": 0},
            ]
        },
    }

    wear = extract_ssd_wear_info(smartctl_data)

    assert wear["percentage_used"] == 12
    assert "Wear_Leveling_Count" in wear
    assert "Power_On_Hours" not in wear


def test_derive_health_status_critical() -> None:
    smartctl_data = {
        "smart_status": {"passed": False},
        "nvme_smart_health_information_log": {"critical_warning": 1, "percentage_used": 96},
        "ata_smart_attributes": {"table": []},
    }

    health, issues = derive_health_status(smartctl_data, {"percentage_used": 96})

    assert health == "Critical"
    assert len(issues) >= 3
