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

## 本地启动

```bash
cd /Users/zezhang/Documents/codex/vnpy
source .venv/bin/activate
python run_vnpy.py
```

首次启动时，`vnpy` 会在 `~/.vntrader/` 下生成配置和日志目录。

## 重新安装依赖

如果你以后要在新机器重建环境，可以使用：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt --index-url=https://pypi.doubanio.com/simple
```

## RQData 配置

如果你要使用 `vnpy_rqdata` 下载历史数据，请把仓库中的 `config/vt_setting.example.json` 内容同步到：

```bash
~/.vntrader/vt_setting.json
```

然后填入你自己的 `RQData` 用户名和密码。
