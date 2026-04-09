import json
import platform
import shutil
from datetime import date, timedelta
from pathlib import Path


A_SHARE_EXCHANGES: tuple[str, ...] = (".SSE", ".SZSE", ".BSE")
A_SHARE_BACKTEST_DEFAULTS: dict[str, object] = {
    "class_name": "LessonAShareLongOnlyStrategy",
    "vt_symbol": "000001.SZSE",
    "interval": "d",
    "rate": 0.0005,
    "slippage": 0.01,
    "size": 1,
    "pricetick": 0.01,
    "capital": 100000,
}
FUTURES_STYLE_BACKTEST_DEFAULTS: dict[str, float] = {
    "rate": 0.000025,
    "slippage": 0.2,
    "size": 300,
    "pricetick": 0.2,
    "capital": 1000000,
}


def patch_qt_stylesheet(qapp) -> None:
    """Apply small macOS-specific fixes for qdarkstyle combo boxes."""
    if platform.system() != "Darwin":
        return

    qapp.setStyleSheet(
        qapp.styleSheet()
        + """
QComboBox {
    min-width: 6em;
    color: #D8E1EA;
}
QComboBox QAbstractItemView {
    min-width: 6em;
    color: #D8E1EA;
    background-color: #19232D;
    selection-color: #FFFFFF;
}
"""
    )


def load_json_dict(path: Path) -> dict:
    """Read a JSON object from disk and fall back to an empty dict."""
    if not path.exists():
        return {}

    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if isinstance(current, dict):
        return current
    return {}


def write_json_dict(path: Path, data: dict) -> None:
    """Write a JSON object to disk using readable formatting."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_a_share_symbol(vt_symbol: str) -> bool:
    """Return whether the local symbol points to a mainland A-share exchange."""
    return vt_symbol.endswith(A_SHARE_EXCHANGES)


def is_same_number(value: object, expected: float) -> bool:
    """Compare persisted numeric values without caring about int/float strings."""
    try:
        return abs(float(value) - expected) < 1e-9
    except (TypeError, ValueError):
        return False


def should_reset_backtester_settings(current: dict) -> bool:
    """Reset CTA backtester defaults when they still look like futures presets."""
    if not current:
        return True

    vt_symbol = str(current.get("vt_symbol", "")).strip()
    if vt_symbol in {"", "IF88.CFFEX"}:
        return True

    if is_a_share_symbol(vt_symbol):
        return all(
            is_same_number(current.get(key), expected)
            for key, expected in FUTURES_STYLE_BACKTEST_DEFAULTS.items()
        )

    return False


def ensure_vnpy_settings() -> None:
    """Create a sane first-run vnpy config without overwriting user choices."""
    trader_dir = Path.home() / ".vntrader"
    trader_dir.mkdir(parents=True, exist_ok=True)

    setting_path = trader_dir / "vt_setting.json"
    current = load_json_dict(setting_path)

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
        "datafeed.adjust": "qfq",
    }

    changed = False
    for key, value in defaults.items():
        if key not in current:
            current[key] = value
            changed = True

    if changed or not setting_path.exists():
        write_json_dict(setting_path, current)


def ensure_backtester_settings() -> None:
    """Seed CTA backtesting defaults that are friendlier to A-share cash study."""
    trader_dir = Path.home() / ".vntrader"
    trader_dir.mkdir(parents=True, exist_ok=True)

    setting_path = trader_dir / "cta_backtester_setting.json"
    current = load_json_dict(setting_path)

    defaults = dict(A_SHARE_BACKTEST_DEFAULTS)
    defaults["start"] = (date.today() - timedelta(days=365)).isoformat()

    changed = False

    if should_reset_backtester_settings(current):
        current = defaults
        changed = True
    else:
        for key, value in defaults.items():
            if key not in current:
                current[key] = value
                changed = True

    if changed or not setting_path.exists():
        write_json_dict(setting_path, current)


def sync_local_strategies() -> None:
    """Copy repo strategies into vnpy discovery folders."""
    project_strategy_dir = Path(__file__).resolve().parent / "strategies"
    if not project_strategy_dir.exists():
        return

    # vn.py appends TRADER_DIR to sys.path. With the default startup flow this
    # resolves to the user's home directory, while runtime data stays under
    # ~/.vntrader. Sync to both locations so CTA strategy discovery works.
    target_dirs = [
        Path.home() / "strategies",
        Path.home() / ".vntrader" / "strategies",
    ]

    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)

        for source in project_strategy_dir.glob("*.py"):
            target = target_dir / source.name
            shutil.copy2(source, target)


def sync_local_packages() -> None:
    """Copy repo-local vnpy extension packages into vnpy import paths."""
    project_dir = Path(__file__).resolve().parent
    package_names = ["vnpy_localdemo", "vnpy_akshare"]
    target_dirs = [Path.home(), Path.home() / ".vntrader"]

    for package_name in package_names:
        source_dir = project_dir / package_name
        if not source_dir.exists():
            continue

        for target_base in target_dirs:
            target_dir = target_base / package_name
            shutil.copytree(
                source_dir,
                target_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )


def main() -> int:
    ensure_vnpy_settings()
    ensure_backtester_settings()
    sync_local_strategies()
    sync_local_packages()

    from vnpy.trader.engine import MainEngine
    from vnpy.trader.ui import MainWindow, create_qapp
    from vnpy_ctabacktester import CtaBacktesterApp
    from vnpy_ctastrategy import CtaStrategyApp
    from vnpy_datamanager import DataManagerApp

    qapp = create_qapp("vnpy_test")
    patch_qt_stylesheet(qapp)

    main_engine = MainEngine()
    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, main_engine.event_engine)
    main_window.showMaximized()

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
