import json
import platform
import shutil
from pathlib import Path


def ensure_vnpy_settings() -> None:
    """Create a sane first-run vnpy config without overwriting user choices."""
    trader_dir = Path.home() / ".vntrader"
    trader_dir.mkdir(parents=True, exist_ok=True)

    setting_path = trader_dir / "vt_setting.json"
    if setting_path.exists():
        try:
            current = json.loads(setting_path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except json.JSONDecodeError:
            current = {}
    else:
        current = {}

    system = platform.system()
    if system == "Darwin":
        default_font = "PingFang SC"
    elif system == "Windows":
        default_font = "Microsoft YaHei"
    else:
        default_font = "Noto Sans CJK SC"

    defaults = {
        "font.family": default_font,
        "font.size": 12,
        "database.name": "sqlite",
        "database.database": "database.db",
        "datafeed.name": "localdemo",
        "datafeed.username": "",
        "datafeed.password": "",
    }

    changed = False
    for key, value in defaults.items():
        if key not in current:
            current[key] = value
            changed = True

    if changed or not setting_path.exists():
        setting_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def sync_local_strategies() -> None:
    """Copy repo strategies into ~/.vntrader/strategies for vnpy discovery."""
    project_strategy_dir = Path(__file__).resolve().parent / "strategies"
    if not project_strategy_dir.exists():
        return

    trader_strategy_dir = Path.home() / ".vntrader" / "strategies"
    trader_strategy_dir.mkdir(parents=True, exist_ok=True)

    for source in project_strategy_dir.glob("*.py"):
        target = trader_strategy_dir / source.name
        shutil.copy2(source, target)


def main() -> int:
    ensure_vnpy_settings()
    sync_local_strategies()

    from vnpy.trader.engine import MainEngine
    from vnpy.trader.ui import MainWindow, create_qapp
    from vnpy_ctabacktester import CtaBacktesterApp
    from vnpy_ctastrategy import CtaStrategyApp
    from vnpy_datamanager import DataManagerApp

    qapp = create_qapp("vnpy_test")

    main_engine = MainEngine()
    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, main_engine.event_engine)
    main_window.showMaximized()

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
