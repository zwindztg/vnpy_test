from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_datamanager import DataManagerApp


def main() -> int:
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

