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

首次运行 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy/run_vnpy.py) 时，脚本会自动补齐 `~/.vntrader/vt_setting.json` 的缺省配置：

- macOS 默认字体会改成 `PingFang SC`
- 默认数据库仍然使用 `sqlite`
- 默认数据服务会使用仓库内置的本地学习模式 `localdemo`
- 仓库里的 `strategies/*.py` 会自动同步到 `~/strategies/` 和 `~/.vntrader/strategies/`

这样首次启动不会因为没有配置 `RQData` 而误以为程序出错。
在 `localdemo` 模式下，`CTA回测` 的“下载数据”按钮会为 `1m`、`1h`、`d` 周期生成一份离线示例K线，用来学习回测流程。

## AkShare 配置

如果你当前只想学习 A 股数据，可以把 [vt_setting.json](/Users/zezhang/.vntrader/vt_setting.json) 中的：

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
cd /Users/zezhang/Documents/codex/vnpy
source .venv/bin/activate
python run_vnpy.py
```

首次启动时，`vnpy` 会在 `~/.vntrader/` 下生成配置和日志目录。

## 策略学习示例

仓库已经包含一个本地 CTA 策略示例：

- `strategies/LessonDoubleMaStrategy`

这是一个最小的双均线交叉策略，适合拿来理解 `vnpy` CTA 策略的基本结构：

- `on_init`: 初始化指标和历史数据
- `on_tick`: 把 Tick 更新交给 `BarGenerator`
- `on_bar`: 计算快慢均线并生成买卖信号
- `on_trade`: 成交后刷新界面变量

`vnpy_ctastrategy` 会自动扫描当前工作目录下的 `strategies/`，所以这个策略在你启动 [run_vnpy.py](/Users/zezhang/Documents/codex/vnpy/run_vnpy.py) 后，应该能在 CTA 策略模块里直接看到。

## 回测这个示例策略

1. 启动界面：`python run_vnpy.py`
2. 打开 `CTA回测`
3. 选择策略类 `LessonDoubleMaStrategy`
4. 选择已经入库的合约和周期
5. 先用默认参数跑通，再修改：
   - `fast_window=10`
   - `slow_window=20`
   - `fixed_size=1`

如果本地还没有历史数据，可以先用 `数据管理` 导入，或者配置 `AkShare` / `RQData` 后再下载。

## 重新安装依赖

如果你以后要在新机器重建环境，可以使用：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt --index-url=https://pypi.doubanio.com/simple
```

## 数据源切换

当前仓库支持三种学习路径：

- `localdemo`: 离线示例数据，适合先跑通界面和回测流程
- `akshare`: 真实 A 股数据，适合股票学习
- `rqdata`: 更完整的正式数据服务，适合后续进阶

### RQData 配置

如果你要使用 `vnpy_rqdata` 下载历史数据，请把仓库中的 `config/vt_setting.example.json` 内容同步到：

```bash
~/.vntrader/vt_setting.json
```

然后填入你自己的 `RQData` 用户名和密码。

如果你想切换到 `RQData`，把 `~/.vntrader/vt_setting.json` 中的：

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
