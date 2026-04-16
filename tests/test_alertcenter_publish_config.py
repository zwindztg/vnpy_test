from __future__ import annotations

import unittest
from pathlib import Path

from vnpy_alertcenter.core import (
    SOURCE_CTA_PUBLISHED,
    SOURCE_MANUAL,
    AppConfig,
    SymbolConfig,
    find_enabled_symbol_conflicts,
    parse_symbol_configs,
    publish_symbol_config,
    update_symbol_enabled_state,
)


class PublishSymbolConfigTest(unittest.TestCase):
    """验证 CTA 回测发布到实时监控配置的落地规则。"""

    def make_config(self, *symbols: SymbolConfig) -> AppConfig:
        """构造测试用监控配置。"""
        return AppConfig(
            interval="15m",
            poll_seconds=20,
            adjust="qfq",
            cooldown_seconds=300,
            alert_history_path=Path("logs/test.csv"),
            notification_enabled=True,
            symbol_configs=tuple(symbols),
        )

    def test_publish_new_symbol_appends_to_empty_slot(self) -> None:
        config = self.make_config(
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonAShareLongOnlyStrategy",
                params={"fast_window": 5, "slow_window": 20},
                enabled=True,
                source_state=SOURCE_MANUAL,
            )
        )

        published = publish_symbol_config(
            config,
            SymbolConfig(
                vt_symbol="600000.SSE",
                strategy_name="LessonDonchianAShareStrategy",
                params={"entry_window": 20, "exit_window": 10},
            ),
            interval="5m",
            target_index=1,
        )

        self.assertEqual("5m", published.interval)
        self.assertEqual(2, len(published.symbol_configs))
        self.assertEqual("600000.SSE", published.symbol_configs[1].vt_symbol)
        self.assertEqual(SOURCE_CTA_PUBLISHED, published.symbol_configs[1].source_state)
        self.assertTrue(published.symbol_configs[1].enabled)

    def test_publish_overwrites_selected_slot(self) -> None:
        config = self.make_config(
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonAShareLongOnlyStrategy",
                params={"fast_window": 5, "slow_window": 20},
                enabled=True,
                source_state=SOURCE_MANUAL,
            ),
            SymbolConfig(
                vt_symbol="600000.SSE",
                strategy_name="LessonDonchianAShareStrategy",
                params={"entry_window": 20, "exit_window": 10},
                enabled=False,
                source_state=SOURCE_MANUAL,
            ),
        )

        published = publish_symbol_config(
            config,
            SymbolConfig(
                vt_symbol="600000.SSE",
                strategy_name="LessonVolumeBreakoutAShareStrategy",
                params={"breakout_window": 5, "exit_window": 3, "volume_window": 5, "volume_ratio": 1.5},
            ),
            interval="5m",
            target_index=1,
        )

        self.assertEqual("LessonVolumeBreakoutAShareStrategy", published.symbol_configs[1].strategy_name)
        self.assertEqual(SOURCE_CTA_PUBLISHED, published.symbol_configs[1].source_state)
        self.assertTrue(published.symbol_configs[1].enabled)

    def test_publish_same_symbol_to_other_slot_is_allowed(self) -> None:
        config = self.make_config(
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonAShareLongOnlyStrategy",
                params={"fast_window": 5, "slow_window": 20},
                enabled=True,
                source_state=SOURCE_MANUAL,
            )
        )

        published = publish_symbol_config(
            config,
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonDonchianAShareStrategy",
                params={"entry_window": 20, "exit_window": 10},
            ),
            interval="5m",
            target_index=1,
        )

        self.assertEqual(2, len(published.symbol_configs))
        self.assertEqual("601869.SSE", published.symbol_configs[1].vt_symbol)
        self.assertEqual("LessonDonchianAShareStrategy", published.symbol_configs[1].strategy_name)

    def test_parse_symbol_configs_keeps_same_symbol_candidates(self) -> None:
        configs = parse_symbol_configs(
            [
                {
                    "vt_symbol": "601869.SSE",
                    "strategy_name": "LessonAShareLongOnlyStrategy",
                    "params": {"fast_window": 5, "slow_window": 20},
                },
                {
                    "vt_symbol": "601869.SSE",
                    "strategy_name": "LessonDonchianAShareStrategy",
                    "params": {"entry_window": 20, "exit_window": 10},
                },
            ]
        )

        self.assertEqual(2, len(configs))
        self.assertEqual("LessonAShareLongOnlyStrategy", configs[0].strategy_name)
        self.assertEqual("LessonDonchianAShareStrategy", configs[1].strategy_name)

    def test_find_enabled_symbol_conflicts_reports_duplicate_enabled_rows(self) -> None:
        config = self.make_config(
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonAShareLongOnlyStrategy",
                params={"fast_window": 5, "slow_window": 20},
                enabled=True,
                source_state=SOURCE_MANUAL,
            ),
            SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name="LessonDonchianAShareStrategy",
                params={"entry_window": 20, "exit_window": 10},
                enabled=True,
                source_state=SOURCE_MANUAL,
            ),
            SymbolConfig(
                vt_symbol="600000.SSE",
                strategy_name="LessonDonchianAShareStrategy",
                params={"entry_window": 20, "exit_window": 10},
                enabled=False,
                source_state=SOURCE_MANUAL,
            ),
        )

        self.assertEqual({"601869.SSE": (1, 2)}, find_enabled_symbol_conflicts(config))

    def test_update_symbol_enabled_state_only_changes_enabled_flag(self) -> None:
        first = SymbolConfig(
            vt_symbol="601869.SSE",
            strategy_name="LessonAShareLongOnlyStrategy",
            params={"fast_window": 5, "slow_window": 20},
            enabled=True,
            source_state=SOURCE_CTA_PUBLISHED,
            config_id="cfg-1",
        )
        second = SymbolConfig(
            vt_symbol="600000.SSE",
            strategy_name="LessonDonchianAShareStrategy",
            params={"entry_window": 20, "exit_window": 10},
            enabled=False,
            source_state=SOURCE_MANUAL,
            config_id="cfg-2",
        )
        config = self.make_config(first, second)

        updated = update_symbol_enabled_state(config, config_id="cfg-1", enabled=False)

        self.assertFalse(updated.symbol_configs[0].enabled)
        self.assertEqual(first.vt_symbol, updated.symbol_configs[0].vt_symbol)
        self.assertEqual(first.strategy_name, updated.symbol_configs[0].strategy_name)
        self.assertEqual(first.params, updated.symbol_configs[0].params)
        self.assertEqual(first.source_state, updated.symbol_configs[0].source_state)
        self.assertEqual(first.config_id, updated.symbol_configs[0].config_id)
        self.assertEqual(second, updated.symbol_configs[1])


if __name__ == "__main__":
    unittest.main()
