import json
import time

from gateway.service import read_runtime_state


def test_runtime_state_marks_recent_bar_fresh(tmp_path):
    path = tmp_path / "state.json"
    now_ms = int(time.time() * 1000)
    path.write_text(
        json.dumps(
            {
                "last_processed_bar": now_ms,
                "active_side": -1,
                "active_entry": 100,
                "active_sl": 125,
                "active_tp_at_entry": 80,
            }
        ),
        encoding="utf-8",
    )
    result = read_runtime_state(path, stale_seconds=150)
    assert result["health"] == "fresh"
    assert result["managed_side"] == "SHORT"
    assert result["active_entry"] == 100


def test_runtime_state_missing_is_unknown(tmp_path):
    result = read_runtime_state(tmp_path / "missing.json", stale_seconds=150)
    assert result["exists"] is False
    assert result["health"] == "unknown"
