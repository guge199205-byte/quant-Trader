<!-- TOC: 技术指标（Indicators） -->
# 技术指标（Indicators）

指标列表与计算（MA/MACD/RSI/KDJ/BOLL 等 187 个）。

### 指标

#### 获取指标列表
当用户问"指标列表"、"有哪些指标"、"可用指标"、"搜索指标"、"indicator list" 时：
```bash
python skills/futuapi/scripts/quote/get_indicator_list.py [--search SUB] [--lang 0|1|2] [--mode 0|1] [--json]
```

**参数说明**：
- --search: 按 short_name 子串过滤（大小写不敏感）
- --lang: 过滤语言：0=不过滤（默认）1=MyLang 2=Python
- --mode: 搜索模式：0=Partial 部分匹配（默认）1=Exact 完全匹配并返回 script 源码（必须配合 --search）

**示例**：
```bash
# 列出所有指标
python skills/futuapi/scripts/quote/get_indicator_list.py

# 搜索包含 MA 的指标
python skills/futuapi/scripts/quote/get_indicator_list.py --search MA

# 精确匹配并获取脚本源码
python skills/futuapi/scripts/quote/get_indicator_list.py --search MACD --mode 1 --lang 1
```

#### 获取指标计算结果
当用户问"计算指标"、"指标结果"、"MA计算"、"MACD结果"、"RSI"、"indicator calc" 时：
```bash
python skills/futuapi/scripts/quote/get_indicator_calc_result.py --short-name MA --lang 1 --kl-file <K线JSON路径> [--param 0=5] [--num 30] [--json]
```

**前置步骤**：需先用 `get_kline.py --json` 获取 K 线数据缓存文件，该文件含 code/ktype/data 字段。

**参数说明**：
- --short-name: 指标短名（对应 IndicatorInfo.shortName，如 MA、MACD、RSI）[必填]
- --lang: 语言类型：1=MyLang, 2=Python [必填]
- --kl-file: K 线 JSON 路径（含 code/ktype/data，由 get_kline --json 写出）[必填]
- --param: 入参覆盖，格式 idx=value（index 从 0 起），可多次使用；不传则使用云端默认配置
- --num: 截取前 N 条 K 线参与计算（正整数）；省略表示使用全部 K 线

**工作流示例**：
```bash
# 1. 先获取 K 线数据（输出 JSON 到 Output/）
python skills/futuapi/scripts/quote/get_kline.py HK.00700 --ktype 1d --num 100 --json > Output/test_cache_kl_HK_00700_day_100.json

# 2. 计算 MA(5) 指标
python skills/futuapi/scripts/quote/get_indicator_calc_result.py --short-name MA --lang 1 --kl-file Output/test_cache_kl_HK_00700_day_100.json --param 0=5

# 3. 计算 MACD 指标（使用默认参数）
python skills/futuapi/scripts/quote/get_indicator_calc_result.py --short-name MACD --lang 1 --kl-file Output/test_cache_kl_HK_00700_day_100.json
```

---

---

**相关技能路由：** 相关：K线数据 → quote-commands.md；指标云端计算见正文。
