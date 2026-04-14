# Daily Work Log

## 2026-04-14 18:22:33 +08:00

- 提交号：`722e920`
- 提交信息：`refactor: 工程内隔离vntrader目录，停止写入用户根目录 / isolate vntrader in project and stop writing to home directory`
- 详细说明：
  - 更新 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy/run_vnpy.py)，将 `TRADER_DIR` 固定为工程目录下 `.vntrader`，不再回落到 `~/.vntrader`。
  - 调整策略与扩展包同步逻辑，仅同步到工程目录下 `.vntrader`，不再写入 `~/strategies`、`~/vnpy_akshare`、`~/vnpy_localdemo` 等用户根目录路径。
  - 更新 [README.md](/Users/zezhang/Documents/codex/vnpy/README.md) 的目录说明，统一为工程内 `.vntrader` 方案，避免后续使用时混淆。

## 2026-04-14 00:05:45 +08:00

- 提交号：`29b913c`
- 提交信息：`feat: 新增A股短线放量突破学习策略 / add a-share volume breakout study strategy`
- 详细说明：
  - 新增 [lesson_volume_breakout_a_share_strategy.py](/Users/zezhang/Documents/codex/vnpy/strategies/lesson_volume_breakout_a_share_strategy.py)，提供 A 股只做多的短线放量突破学习策略：
    - 用短周期区间突破作为进场基础。
    - 用成交量相对近期均量的倍数作为放量过滤条件。
    - 用更短周期低点作为失败离场条件，帮助学习短线“该强不强就走”的思路。
  - 更新 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy/run_vnpy.py)，把新策略接入 CTA 回测学习界面：
    - 增加新策略的双语显示名称。
    - 增加新策略参数的中文标签说明，方便直接在界面里调参测试。
    - 保留并启用 macOS 下功能窗口自动前置逻辑，避免 `CTA回测` 窗口打开后不前置的问题。

## 2026-04-13 17:57:24 +08:00

- 提交号：`eced07a`
- 提交信息：`feat: improve a-share strategy learning workflow`
- 详细说明：
  - 新增 [lesson_donchian_a_share_strategy.py](/Users/zezhang/Documents/codex/vnpy_test/strategies/lesson_donchian_a_share_strategy.py)，提供 A 股只做多的唐奇安突破学习策略，并补充完整中文注释。
  - 更新 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy_test/run_vnpy.py)，增强 A 股策略学习体验：
    - 策略下拉框支持“中文名（英文类名）”双语显示。
    - `SH/SZ/BJ` 股票后缀自动规范为 `SSE/SZSE/BSE`。
    - 回测参数弹窗显示更友好的中文参数标签。
    - 在 `策略统计指标计算完成` 后自动输出本次回测配置摘要，便于确认实际使用的策略参数。
  - 新增 [restart_vnpy_gui.sh](/Users/zezhang/Documents/codex/vnpy_test/scripts/restart_vnpy_gui.sh)，统一执行“先关闭旧 GUI，再启动新 GUI”的流程。
  - 更新 [AGENTS.md](/Users/zezhang/Documents/codex/vnpy_test/AGENTS.md)，把“每次修改完代码后先 kill 再重启 GUI”的协作约定写入项目规则。

## 2026-04-13 16:56:10 +08:00

- 提交号：`f148727`
- 提交信息：`feat: polish a-share backtesting defaults and ui`
- 详细说明：
  - 把默认学习标的从 `000001.SZSE` 调整为 `601869.SSE`。
  - 增加旧默认配置迁移逻辑，让此前保存过旧默认值的本地回测配置可以自动切到新标的。
  - 修复 [lesson_a_share_long_only_strategy.py](/Users/zezhang/Documents/codex/vnpy_test/strategies/lesson_a_share_long_only_strategy.py) 中 `ArrayManager` 默认窗口过大导致短区间回测始终不出信号的问题。
  - 为 A 股回测界面增加更清晰的中文参数说明，改善学习和试验参数时的可读性。

## 2026-04-10 10:55:37 +08:00

- 提交号：`a01c208`
- 提交信息：`feat: auto refresh backtest data cache`
- 详细说明：
  - 在 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy_test/run_vnpy.py) 中加入回测前自动补数逻辑。
  - 回测时会先检查本地数据库是否覆盖所选股票、周期和日期区间，不足时自动通过 `AkShare` 下载并写入本地数据库。
  - 手动“下载数据”时，改成先删除当前股票和当前周期的旧缓存，再写入新数据，避免缓存重复和库体积不断膨胀。
  - 启动时增加轻量数据库清理逻辑，优先清理旧的分钟线和 Tick 数据，同时保留日线数据用于学习回测。
  - 配置目录路径规则与 `vn.py` 内部保持一致，兼容当前目录 `.vntrader` 和用户目录 `~/.vntrader` 两种模式。

## 2026-04-09 15:47:37 +08:00

- 提交号：`2b8f43f`
- 提交信息：`docs: annotate lesson double ma strategy in chinese`
- 详细说明：
  - 为 [lesson_double_ma_strategy.py](/Users/zezhang/Documents/codex/vnpy_test/strategies/lesson_double_ma_strategy.py) 补充完整中文教学注释。
  - 重点解释了双均线策略中的金叉、死叉、多空开平逻辑，以及 `on_init`、`on_bar`、`on_trade` 等关键回调的含义。

## 2026-04-09 12:49:30 +08:00

- 提交号：`c125923`
- 提交信息：`docs: add project guidance and strategy comments`
- 详细说明：
  - 新增 [AGENTS.md](/Users/zezhang/Documents/codex/vnpy_test/AGENTS.md)，把项目默认使用中文、A 股现货学习场景、默认回测参数和协作约束写入仓库规则。
  - 为 [lesson_a_share_long_only_strategy.py](/Users/zezhang/Documents/codex/vnpy_test/strategies/lesson_a_share_long_only_strategy.py) 补充中文教学注释，帮助后续学习理解。

## 2026-04-09 12:34:34 +08:00

- 提交号：`c5ec92a`
- 提交信息：`feat: improve a-share backtesting workflow`
- 详细说明：
  - 新增 [lesson_a_share_long_only_strategy.py](/Users/zezhang/Documents/codex/vnpy_test/strategies/lesson_a_share_long_only_strategy.py)，提供 A 股只做多的学习版均线策略。
  - 更新 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy_test/run_vnpy.py)，把 CTA 回测默认值调整为更适合 A 股现货学习的参数。
  - 修复 macOS 下 `CTA回测` 的 `K线周期` 下拉框显示异常问题。
  - 改善策略同步和启动流程，让本地策略能更稳定地被 `vn.py` 发现。

## 2026-04-09 00:29:20 +08:00

- 提交号：`2c165a2`
- 提交信息：`feat: add akshare a-share datafeed`
- 详细说明：
  - 新增 [datafeed.py](/Users/zezhang/Documents/codex/vnpy_test/vnpy_akshare/datafeed.py)，接入 `AkShare` 作为 A 股历史数据源。
  - 支持 `SSE`、`SZSE`、`BSE` 三类交易所后缀，并支持 `1m`、`1h`、`d` 周期查询。
  - 为后续 A 股真实数据下载、数据管理和 CTA 回测打通基础链路。

## 2026-04-08 23:51:30 +08:00

- 提交号：`4875c94`
- 提交信息：`feat: generate offline demo bars for backtesting`
- 详细说明：
  - 为本地学习流程提供离线演示数据能力，支持在没有真实行情数据时先打通 `vn.py` 回测流程。
  - 为后续引入真实 A 股数据前的环境验证和功能学习提供基础样例。
