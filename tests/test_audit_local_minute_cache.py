from __future__ import annotations

import unittest
from datetime import datetime

import pandas as pd

from scripts.audit_local_minute_cache import analyze_bar_frame, format_report


class AuditLocalMinuteCacheTest(unittest.TestCase):
    """验证本地分钟缓存体检脚本的只读分析逻辑。"""

    def test_analyze_bar_frame_detects_duplicates_reversed_gaps_and_anomalies(self) -> None:
        frame = pd.DataFrame(
            [
                {"rowid": 1, "datetime": "2026-04-16 09:31:00", "open_price": 10.0, "high_price": 10.2, "low_price": 9.8, "close_price": 10.1, "volume": 100.0},
                {"rowid": 2, "datetime": "2026-04-16 09:33:00", "open_price": 10.1, "high_price": 10.3, "low_price": 9.9, "close_price": 10.2, "volume": 120.0},
                {"rowid": 3, "datetime": "2026-04-16 09:32:00", "open_price": 10.2, "high_price": 10.4, "low_price": 10.0, "close_price": 10.3, "volume": 130.0},
                {"rowid": 4, "datetime": "2026-04-16 09:33:00", "open_price": 10.1, "high_price": 10.3, "low_price": 9.9, "close_price": 10.2, "volume": 125.0},
                {"rowid": 5, "datetime": "2026-04-16 09:36:00", "open_price": 3900.0, "high_price": 3905.0, "low_price": 3890.0, "close_price": 3901.0, "volume": 99999999.0},
            ]
        )
        frame["datetime"] = pd.to_datetime(frame["datetime"])

        report = analyze_bar_frame("601869.SSE", "1m", frame)

        self.assertEqual(5, report.row_count)
        self.assertGreaterEqual(report.duplicate_count, 2)
        self.assertEqual(1, report.reversed_count)
        self.assertEqual(1, report.gap_count)
        self.assertEqual(1, report.price_anomaly_count)
        self.assertEqual(1, report.volume_anomaly_count)
        self.assertFalse(report.healthy)

    def test_format_report_contains_stable_sections(self) -> None:
        frame = pd.DataFrame(
            [
                {"rowid": 1, "datetime": datetime(2026, 4, 16, 9, 31), "open_price": 10.0, "high_price": 10.2, "low_price": 9.8, "close_price": 10.1, "volume": 100.0},
                {"rowid": 2, "datetime": datetime(2026, 4, 16, 9, 32), "open_price": 10.1, "high_price": 10.3, "low_price": 9.9, "close_price": 10.2, "volume": 120.0},
            ]
        )

        report = analyze_bar_frame("601869.SSE", "1m", frame)
        output = format_report(report)

        self.assertIn("[601869.SSE][1m]", output)
        self.assertIn("时间范围:", output)
        self.assertIn("重复时间:", output)
        self.assertIn("价格异常:", output)
        self.assertIn("成交量异常:", output)


if __name__ == "__main__":
    unittest.main()
