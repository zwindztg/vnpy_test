# vnpy_test

这个仓库用于保存本地 `vnpy` 学习环境的最小项目骨架。

## 当前环境

- Python 版本: `3.13`
- 核心框架: `vnpy==4.3.0`
- 已安装模块:
  - `vnpy_ctastrategy`
  - `vnpy_ctabacktester`
  - `vnpy_datamanager`
  - `vnpy_sqlite`
  - `vnpy_rqdata`
  - `akshare`

首次运行 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy_test/run_vnpy.py) 时，脚本会自动补齐工程目录下 `.vntrader/vt_setting.json` 的缺省配置：

- macOS 默认字体会改成 `PingFang SC`
- 默认数据库仍然使用 `sqlite`
- 默认数据服务会使用仓库内置的本地学习模式 `localdemo`
- 仓库里的 `strategies/*.py` 会自动同步到工程目录下 `.vntrader/strategies/`

这样首次启动不会因为没有配置 `RQData` 而误以为程序出错。
在 `localdemo` 模式下，`CTA回测` 的“下载数据”按钮会为 `1m`、`1h`、`d` 周期生成一份离线示例K线，用来学习回测流程。

## AkShare 配置

如果你当前只想学习 A 股数据，可以把工程目录下 `.vntrader/vt_setting.json` 中的：

```json
"datafeed.name": "localdemo"
```

改成：

```json
"datafeed.name": "akshare",
"datafeed.adjust": "qfq"
```

当前仓库内置的 `vnpy_akshare` 适配层有这些边界：

- 只接入沪深北 A 股数据
- 支持 `1m`、`1h`、`d`
- `1m` 走 AkShare 的新浪分钟接口，通常只适合近期数据
- `d` 和 `1h` 更适合学习回测流程

后期如果你要切到 `RQData`，只需要把 `datafeed.name` 改回 `rqdata`，策略和数据库结构都不用改。

## 本地启动

```bash
cd /Users/zezhang/Documents/codex/vnpy_test
source .venv/bin/activate
python run_vnpy.py
```

首次启动时，`vnpy` 会在工程目录下 `.vntrader/` 生成配置和日志目录。

## 独立版 CTA 实时监控脚本

如果你想先做“只提醒、不实盘”的最小闭环，也可以直接运行独立脚本：

```bash
cd /Users/zezhang/Documents/codex/vnpy_test
source .venv/bin/activate
python scripts/akshare_realtime_alert.py
```

这个脚本当前的默认行为是：

- 默认监控 `2` 只股票：
  - `601869.SSE`
  - `600000.SSE`
- 默认使用 `1m` 周期
- 每 `20` 秒轮询一次 `pytdx`
- 每只股票可以单独选择监控策略：
  - `基础提醒策略（BasicAlertStrategy）`
  - `A股长仓学习策略（LessonAShareLongOnlyStrategy）`
  - `A股唐奇安突破策略（LessonDonchianAShareStrategy）`
  - `A股短线放量突破策略（LessonVolumeBreakoutAShareStrategy）`
- 输出统一中文文案，并区分“观察型信号”和“风控型信号”
- 增加冷却时间和新K线去重，避免同一信号刷屏
- 非交易时段自动暂停
- 把触发过的信号写入 `logs/akshare_realtime_alerts.csv`，方便收盘后复盘
- 只输出终端中文日志和本地 CSV 记录，不调用任何下单、撤单、持仓逻辑

运行中可以通过 `Ctrl+C` 停止脚本。

脚本和 GUI 共用同一份提醒配置。配置文件默认位于 `.vntrader/config/akshare_realtime_alert.json`；
如果该文件还不存在，系统会先回退到内置默认配置。你在 GUI 中保存过配置后，后续脚本也会读取这份文件。

可以直接修改的核心字段有：

- `interval`：当前提醒周期，支持 `1m`、`5m`、`15m`、`30m`
- `poll_seconds`：轮询间隔秒数
- `adjust`：复权方式，默认 `qfq`
- `cooldown_seconds`：同类提醒最小冷却时间
- `notification_enabled`：是否启用桌面通知
- `alert_history_path`：信号记录 CSV 路径
- `symbols`：监控股票列表，每项包含：
  - `vt_symbol`
  - `strategy_name`
  - `params`
  - `enabled`

其中 `params` 会跟随策略变化：

- `BasicAlertStrategy`
  - `breakout_price`
  - `stop_loss_price`
  - `fast_ma_window`
  - `slow_ma_window`
- `LessonAShareLongOnlyStrategy`
  - `fast_window`
  - `slow_window`
- `LessonDonchianAShareStrategy`
  - `entry_window`
  - `exit_window`
- `LessonVolumeBreakoutAShareStrategy`
  - `breakout_window`
  - `exit_window`
  - `volume_window`
  - `volume_ratio`

如果配置文件缺失、格式错误或 `symbols` 为空，脚本会自动回退到脚本内置默认值，不会直接报废。旧版固定字段配置也会自动迁移为 `BasicAlertStrategy`。

## vn.py 内嵌 CTA 实时监控中心

当前仓库还提供了一个已经整合进 `vn.py` 主界面的 CTA 实时监控中心：

- 功能菜单名称：`CTA 实时监控`
- 数据源：实时模式只使用 `pytdx`；成功数据会回写本地 sqlite；只有单次测试才会在远端失败后回退本地数据库
- 默认周期：`1m`
- GUI 当前支持切换 `1m`、`5m`、`15m`、`30m`
- 支持内容：
  - 配置编辑
  - 每只股票单独选择监控策略
  - 同股票保留多个候选配置，但运行时仅允许启用一条
  - 策略参数动态切换
  - 模拟截止时间单次测试
  - 启动/停止监控
  - 实时日志
  - 信号记录表格
  - 股票状态面板
  - 信号记录 CSV
  - macOS 桌面通知

这个 GUI 版本仍然只做监控和信号提示，不会触发任何真实下单、撤单、持仓逻辑。

项目在启动 `run_vnpy.py` 和独立提醒脚本时，会自动在当前 Python 进程里绕过 `HTTP_PROXY / HTTPS_PROXY / ALL_PROXY` 等代理环境变量。  
这只影响项目自身的网络请求，不会修改你的系统代理，也不会影响 Codex 对话本身。

如果你在非交易时段也想测试 GUI，可以直接在“CTA 实时监控中心”窗口里：

1. 设置股票、策略和参数
2. 选择“模拟截止时间”，例如昨天 `15:00:00`
3. 点击“单次测试”

这样系统会用该时间点之前的历史行情做一轮监控信号计算，并把结果直接写到日志区、状态表和记录表里，不需要等到盘中才能验证界面是否可用。

实时监控运行时，如果 `pytdx` 拉取失败，系统会直接报错并停止当前这轮信号计算，不会偷偷切到本地 sqlite。

如果远程分钟线接口暂时不可用，单次测试会按下面顺序自动回退：

- 先尝试 `pytdx`
- `pytdx` 不可用时，单次测试直接回退本地数据库
- 本地分钟回放会先尝试当前周期缓存；如果同周期缺失，再退回本地 `1m` 聚合
- 如果本地也没有当前周期需要的历史数据，则直接提示失败，并提示先补本地 `1m`

- 有同周期分钟线时，继续按分钟级回放
- 不再把 `1m/5m` 之类的分钟监控悄悄回退成 `d` 日线演示

### 分钟线时间戳规则

当前提醒中心对分钟线统一采用“收盘时间戳”语义：

- `pytdx` 返回的分钟线进入提醒中心后，统一按收盘时间解释
- 本地 sqlite 读出的分钟线，以及由本地 `1m` 聚合出的 `5m/15m/30m`，也按同一口径处理
- 例如参考时间是 `10:00` 时，`10:00` 这根 `5m` bar 会被视为“已完成，可参与信号计算”

这样主界面、详情窗口、本地回放和远端分钟线都尽量使用同一套完成 bar 规则，不再因为时间戳理解不同而出现图表和状态不一致。

远端成功获取到的完整分钟线会顺手写回项目本地 sqlite，后续单次测试会优先复用缓存。本地 sqlite 的定位是“离线测试、分钟回放和排错缓存”，不参与实时监控信号源切换。单次测试日志也会明确写出当前是否使用了本地 fallback，以及分钟线抓取摘要，方便区分“真实分钟回放”和“本地演示回放”。

### 本地分钟缓存排查

当前仓库提供了两个和本地分钟缓存相关的脚本：

- 只读体检：`python scripts/audit_local_minute_cache.py --interval 1m`
- 补本地 `1m`：`python scripts/repair_local_bar_cache.py --vt-symbol 601869.SSE --fill-1m`

建议流程是：

1. 先用体检脚本查看 `1m` 是否存在重复、倒序、明显缺口或价格量级异常
2. 如果本地 `1m` 缺失或明显异常，再用 `--fill-1m` 补本地基础分钟缓存
3. 离线分钟回放会先尝试复用同周期缓存；如果同周期缺失，再从本地 `1m` 聚合

### 成交量显示口径

当前项目对成交量先保持一个明确但保守的约定：

- 内部存储：先保留 `pytdx` 返回的原始 `vol` 数值，不在写入 sqlite 时做换算
- 图表显示：继续按当前“手 / 万手 / 亿手”的格式化规则展示

如果后续确认 `pytdx vol` 的真实单位和图表文案需要调整，再统一改展示层文案，不先在数据层做隐式换算。

## 策略学习示例

仓库已经包含一个本地 CTA 策略示例：

- `strategies/LessonDoubleMaStrategy`
- `strategies/LessonAShareLongOnlyStrategy`
- `strategies/LessonDonchianAShareStrategy`
- `strategies/LessonVolumeBreakoutAShareStrategy`

这是一个最小的双均线交叉策略，适合拿来理解 `vnpy` CTA 策略的基本结构：

- `on_init`: 初始化指标和历史数据
- `on_tick`: 把 Tick 更新交给 `BarGenerator`
- `on_bar`: 计算快慢均线并生成买卖信号
- `on_trade`: 成交后刷新界面变量

`LessonAShareLongOnlyStrategy` 是专门给 A 股现货学习准备的长仓版示例：

- 只会开多和平多，不会做空
- 默认下单数量是 `100` 股，符合 A 股一手
- 更适合配合 `601869.SSE` 的 `d` 周期做入门回测

启动 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy_test/run_vnpy.py) 时，仓库里的 `strategies/*.py` 会自动同步到项目运行目录 `.vntrader/strategies/`。
`CTA策略` 和 `CTA回测` 识别到的，是这份运行时目录里的策略文件。

## 回测这个示例策略

1. 启动界面：`python run_vnpy.py`
2. 打开 `CTA回测`
3. 选择策略类 `LessonAShareLongOnlyStrategy`
4. 选择已经入库的合约和周期
5. 先用默认参数跑通，再修改：
   - `fast_window=5`
   - `slow_window=20`
   - `fixed_size=100`

如果本地还没有历史数据，可以先用 `数据管理` 导入，或者配置 `AkShare` / `RQData` 后再下载。

## 重新安装依赖

如果你以后要在新机器重建环境，可以使用：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt --index-url=https://pypi.doubanio.com/simple
```

## 运行测试

当前仓库自带测试默认使用 `unittest`，不依赖 `pytest`。

```bash
.venv/bin/python -m unittest discover -s tests -q
```

## 数据源切换

当前仓库支持三种学习路径：

- `localdemo`: 离线示例数据，适合先跑通界面和回测流程
- `akshare`: 真实 A 股数据，适合股票学习
- `rqdata`: 更完整的正式数据服务，适合后续进阶

### RQData 配置

如果你要使用 `vnpy_rqdata` 下载历史数据，请把仓库中的 `config/vt_setting.example.json` 内容同步到：

```bash
.vntrader/vt_setting.json
```

然后填入你自己的 `RQData` 用户名和密码。

如果你想切换到 `RQData`，把 `.vntrader/vt_setting.json` 中的：

```json
"datafeed.name": "akshare"
```

改成：

```json
"datafeed.name": "rqdata"
```

并补全 `datafeed.username`、`datafeed.password`。

## 下一步建议

如果你准备系统学量化编程，建议按这个顺序往下走：

1. 先跑通 `LessonDoubleMaStrategy` 回测，理解事件驱动和策略生命周期
2. 再把策略改成带止损止盈版本
3. 然后学习如何接入你实际要交易的接口和行情数据
