# tdx_ai_api.py 使用说明

> 通达信 **TdxAiData** 数据接口的统一引入入口。
> 采用「方案 B：薄封装」——运行时指向通达信安装自带的tdxaidata目录，**不拷贝任何 DLL / 配置文件**，通达信一更新即自动用上最新版。

---

## 1. 这是什么

`tdx_ai_api.py` 是一个**纯代码、无二进制**的引导模块，职责只有三件：

1. 定位通达信自带的 TdxAiData 目录（含 `tqServer.py` + `TdxAiData.dll` + 依赖 DLL + `TdxAiData.ini`）；
2. 把该目录接入当前解释器（`sys.path` / DLL 搜索路径 / `TDX_AI_DATA_LIB`）；
3. 导出可直接使用的 `tqs` 类。

导出项：

| 名称 | 说明 |
|---|---|
| `tqs` | 数据接口类，所有行情/财务/板块/订阅等方法都挂在它上面 |
| `TDX_AI_DATA_DIR` | 实际使用的tdxaidata目录路径（默认 `C:\new_tdx_test\TdxAiData`） |
| `PROJECT_CWD` | **导入本模块时**的启动目录，用于规避 `os.chdir` 副作用（见第 8 节） |

---

## 2. 前提条件

| 条件 | 说明 |
|---|---|
| ✅ 已安装通达信 | tdxaidata目录由通达信安装时附带，含原生 DLL 与 `tqServer.py` |
| ✅ **64 位 Python** | `TdxAiData.dll` 是 x64，32 位 Python 会加载失败 |
| ✅ 已配置 token | 写在**tdxaidata目录**的 `TdxAiData.ini` 里（不是项目目录！见第 8 节） |
| ✅ 依赖库 | 需要 `pandas`（返回值多为 DataFrame）：`pip install pandas` |

`TdxAiData.ini` 结构（token 请向通达信会员中心获取，**勿外泄**）：

```ini
[Token]
token=TDX-你的Key

[Server]
; 主机地址一般随安装已配好，无需改动
```

---

## 3. 快速开始

在 `tdx_ai_api.py` 所在目录（本项目为 `E:\PythonProject\pythonProject2026d\260912tdxaidatademo`）：

```python
from tdx_ai_api import tqs

# 取一段日 K 线
data = tqs.get_market_data(
    field_list=["Open", "High", "Low", "Close", "Volume"],
    stock_list=["600000.SH"],
    period="1d",
    start_time="2025-01-01",
    end_time="2025-06-30",
)
print(data["Close"])     # DataFrame：列=股票代码，索引=日期
```

想一次性验证所有接口是否正常，直接跑自检脚本：

```powershell
python E:\PythonProject\pythonProject2026d\260912tdxaidatademo\tdx_aidata_api_demo.py
```

---

## 4. 在其它项目里使用的三种方式

`tdx_ai_api.py` 只是普通文件，别的项目要能 `import` 它，必须让它「在导入路径上」。三选一：

| 方式 | 做法 | 适合场景 |
|---|---|---|
| **① 复制文件**（默认推荐） | 把 `tdx_ai_api.py`（建议连同 `tdxquant-docs-<日期>` 文档夹，见第 10 节）拷进目标项目根目录，然后 `from tdx_ai_api import tqs` | 项目不多，最省事 |
| **② 全局配一次** | 放到固定目录 + `.pth` 或 `PYTHONPATH`，所有项目免复制直接 import | 项目多，想「真·直接 import」 |
| **③ 做成 pip 包** | 加 `pyproject.toml`，各环境 `pip install -e .` | 团队 / 多虚拟环境 / CI |

**方式 ② 具体操作**（一次配置，永久生效）：

```powershell
# 1) 建共享目录并放入文件
mkdir C:\pyshared ; copy E:\PythonProject\pythonProject2026d\260912tdxaidatademo\tdx_ai_api.py C:\pyshared\

# 2) 查当前 Python 的 site-packages 路径
python -c "import site; print(site.getsitepackages()[0])"

# 3) 在上一步的 site-packages 目录下新建文件 tdx_ai_api.pth，内容仅一行：
#    C:\pyshared
```

之后**任何项目**都能直接 `from tdx_ai_api import tqs`，无需再复制。

> 换机器 / 通达信装在别处：不用改代码，设环境变量 `TDX_AI_DATA_DIR` 指向实际目录即可（见第 7 节）。

---

## 5. 常用接口示例

以下签名均来自 `tdx_aidata_api_demo.py`，已实测能返回真实数据。

```python
from tdx_ai_api import tqs

stock = "600000.SH"
index = "000300.SH"

# 1) 交易日历 -> list[str]
dates = tqs.get_trading_dates("SH", "2025-01-01", "2025-01-31")

# 2) K 线 -> dict[字段, DataFrame]
data = tqs.get_market_data(
    field_list=["Open", "High", "Low", "Close", "Volume"],
    stock_list=[stock, "000001.SZ"],
    period="1d", start_time="2025-01-01", end_time="2025-06-30",
)

# 3) 除权除息 -> DataFrame
div = tqs.get_divid_factors(stock, "2020-01-01", "2025-12-31")

# 4) 实时快照：传【单个代码】，不是列表！
snap = tqs.get_market_snapshot(stock)

# 5) 基础 / 扩展信息
info = tqs.get_stock_info(stock)
more = tqs.get_more_info(stock)

# 6) 证券列表 / 板块列表
stocks = tqs.get_stock_list("上证主板", 0)
sectors = tqs.get_sector_list(0)

# 7) 板块成分股：传【板块代码】，传名称会返回空！
members = tqs.get_stock_list_in_sector("880201.SH")

# 8) 个股所属板块
rel = tqs.get_relation(stock)

# 9) 财务数据：stock_list + field_list
fin = tqs.get_financial_data(
    stock_list=[stock],
    field_list=["总资产", "净资产", "营业收入"],
    start_time="2024-01-01", end_time="2025-12-31",
)

# 10) 涨跌停 / 日线统计
zdt = tqs.get_zdt_data([stock])
exday = tqs.get_exday_data(stock, 5)

# 11) 指数成分股：第二参数是 list_type（不是 sort_type）
hs300 = tqs.get_zzgz_stocklist(index, 0)

# 12) 可转债信息：非可转债返回空属正常
kzz = tqs.get_kzz_info(stock)

# 13) 分笔 / 分时：先取最近交易日再查
tick = tqs.get_tick_data(stock, dates[-1], 0, 10)
minute = tqs.get_minute_data(stock, dates[-1])
```

**实时订阅**（必须传 `callback`，且主进程要保持运行，否则收不到推送）：

```python
import json, time

def on_quote(raw: str):
    q = json.loads(raw)                       # 回调收到的是 JSON 字符串
    if q.get("ErrorId") not in (0, "0", None):
        return                                # 非 0 表示这一包无有效数据
    for rs in q.get("ResultSets", []):
        cols = rs.get("ColDes", [])           # 列名
        for row in rs.get("Content", []):     # 数据行
            item = dict(zip(cols, row))
            print(item.get("code"), item.get("price"), item.get("volume"))

tqs.subscribe(stock_list=["600000.SH"], callback=on_quote)
print("已订阅：", tqs.get_subscribe_hq_stock_list())

time.sleep(30)                                # 保持运行以接收推送（示例）

tqs.unsubscribe(["600000.SH"])
```

---

## 6. 返回值结构速查

| 接口 | 返回类型 | 备注 |
|---|---|---|
| `get_market_data` | `dict[字段, DataFrame]` | 如 `data["Close"]`；DataFrame 列=代码，索引=日期 |
| `get_trading_dates` | `list[str]` | 交易日字符串列表 |
| `get_divid_factors` | `DataFrame` | 索引=除权除息日 |
| `get_market_snapshot` | `dict` | 单个代码的实时快照字段 |
| `get_stock_list_in_sector` / `get_zzgz_stocklist` | `list[str]` | 成分股代码列表 |
| `get_tick_data` / `get_minute_data` | `dict[字段, list]` | 含 `Price`/`Volume`/`Time`/`TotalNum` 等 |
| `subscribe` 回调 | JSON 字符串 | 需 `json.loads`，见第 5 节 |

> 完整接口与字段以官方文档为准：项目内的 `tdxquant-docs-<日期>` 文档夹（见第 10 节，先查 `API索引.md`），或tdxaidata目录内的《TdxQuant 接口说明文档.pdf》。

---

## 7. 环境变量

| 变量 | 默认值 | 作用 |
|---|---|---|
| `TDX_AI_DATA_DIR` | `C:\new_tdx_test\TdxAiData` | 中央 bundle 目录；通达信换位置时改这里，**无需改代码** |
| `TDX_AI_DATA_LIB` | 自动=`<目录>\TdxAiData.dll` | 原生库绝对路径，一般无需手动设 |

PowerShell 临时设置示例：

```powershell
$env:TDX_AI_DATA_DIR = "D:\path\to\TdxAiData"
python your_script.py
```

---

## 8. 必须知道的坑

1. **工作目录会被切走**：首次调用任意 `tqs` 接口时，底层会 `os.chdir` 到 DLL 目录读取 `TdxAiData.ini`，之后**进程工作目录停在tdxaidata目录**。
   → 你项目里读写自己的文件请**一律用绝对路径**，或用导出的 `PROJECT_CWD` 拼：
   ```python
   from tdx_ai_api import PROJECT_CWD
   import os
   path = os.path.join(PROJECT_CWD, "output", "result.csv")
   ```

2. **配置文件只认tdxaidata目录那份**：把 `TdxAiData.ini` 放到项目/脚本目录**不会生效**（DLL 从自己所在目录读）。token 只在 `C:\new_tdx_test\TdxAiData\TdxAiData.ini` 维护一份。

3. **必须 64 位 Python**：位数不匹配会加载 DLL 失败。

4. **几个易错签名**（写错会返回空或报错）：
   - `get_market_snapshot` 传**单个代码**，不是列表；
   - `get_stock_list_in_sector` 传**板块代码**（如 `880201.SH`），传名称返回空；
   - `get_zzgz_stocklist` 第二参数是 `list_type`，不是 `sort_type`；
   - `subscribe` **必须**传 `callback`。

5. **通达信更新后**：因指向「活目录」，更新会自动生效；但若接口签名有变化，调用处可能需相应调整——更新后建议先跑一次 `tdx_aidata_api_demo.py` 自检。

---

## 9. 常见问题排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 接口大面积返回 `Token Insufficient`（常伴错误码 13） | **token 配错 / 写错 / 过期**（不是限流、不是代码问题） | 到通达信会员中心重新获取正确 token，写入**tdxaidata目录**的 `TdxAiData.ini` 的 `[Token] token=`，重跑即恢复 |
| 抛 `TypeError` 或返回参数错误码（2/3/5/6） | 调用签名写错 | 对照第 5 节 / 第 8 节第 4 点修正参数 |
| `TdxAiData 动态库加载失败` | Python 位数不符 / DLL 或依赖缺失 | 换 64 位 Python；确认tdxaidata目录 DLL 齐全 |
| `FileNotFoundError: 未找到 TdxAiData 目录` | 通达信未装 / 路径变了 | 装通达信，或设 `TDX_AI_DATA_DIR` 指向实际目录 |
| 个别接口返回空但无报错 | 正常业务结果 | 如 `get_kzz_info(600000.SH)`——浦发银行不是可转债，返回空是对的 |

> **判断 token 是否有效**：看 `get_market_data` 能否返回真实数据；能返回即 token 正常。

---

## 10. 配套文档（tdxquant-docs-<日期>）

项目内的 `tdxquant-docs-<日期>` 文件夹（当前为 `tdxquant-docs-20260911`）是**通达信官方文档**，涵盖 TdxAiData 接口与 TdxQuant 的完整说明，是本模块的权威参考：

| 内容 | 位置 |
|---|---|
| 总入口 / 目录 | `README.md` |
| **接口速查** | `API索引.md`（按函数名快速定位） |
| 通用函数 / 行情 / 财务 / 板块 / 交易 等分类 | `02-通用函数` ~ `09-交易函数` 子目录，每个函数一个 `.md` |
| 常量枚举 | `15-常量枚举.md` |
| 回测与模拟交易 | `16-回测及模拟交易.md` |
| Lambda 专用函数 | `13-Lambda专用函数/` |
| 场景化示例 | `17-场景化示例/` |
| 常见问题 | `19-常见问题.md` |

**用法建议**：
- 查某个接口的**准确签名 / 字段 / 返回结构**时，先翻 `API索引.md`，再进对应分类的 `.md`；
- 本 README 第 5、6 节只是常用子集，**完整接口以该文档夹为准**；
- 文件夹名的日期后缀会随文档更新变化，引用时用 `tdxquant-docs-<日期>` 泛指即可。

> **新项目请一并复制这个文档夹**：它与 `tdx_ai_api.py` 配套，方便随时查阅接口细节。

---

## 11. 文件清单

| 文件 / 文件夹 | 作用 |
|---|---|
| `tdx_ai_api.py` | 引入模块（复制到别的项目 / 全局配置后即可 `from tdx_ai_api import tqs`） |
| `tdx_aidata_api_demo.py` | 18 步接口自检示例，依赖 `tdx_ai_api.py` |
| `tdx_ai_api_readme.md` | 本说明文档 |
| `tdxquant-docs-<日期>/` | 通达信官方文档（TdxAiData 接口 + TdxQuant），新项目建议一并复制 |
