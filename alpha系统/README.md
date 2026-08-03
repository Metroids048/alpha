# BRAIN Alpha Simulater — 说明文档

> **版本**: 2026-07
> **平台**: Windows 10+ (PyQt5 桌面应用)
> **用途**: WorldQuant BRAIN 平台 Alpha 表达式模拟、调参、提交一体化工具

---

## 目录

1. [概述](#1-概述)
2. [环境与依赖](#2-环境与依赖)
3. [项目结构](#3-项目结构)
4. [快速开始](#4-快速开始)
5. [核心架构](#5-核心架构)
6. [功能详解](#6-功能详解)
7. [Traverse 语法规则](#7-traverse-语法规则)
8. [Tune 语法规则](#8-tune-语法规则)
9. [配置文件说明](#9-配置文件说明)
10. [快捷键](#10-快捷键)
11. [常见问题](#11-常见问题)

---

## 1. 概述

BRAIN Alpha Simulater 是一款基于 PyQt5 的桌面 GUI 工具，专为 WorldQuant BRAIN 平台的 Alpha 研究者设计。它将 Alpha 表达式编写、模拟运行、参数调优、相关性检查、提交等全流程集成在一个多标签页界面中，大幅提升 Alpha 研发效率。

### 核心特性

| 特性 | 说明 |
|------|------|
| **多标签模拟** | 支持同时打开多个模拟标签页，每个标签独立运行 |
| **并发模拟** | 最多 8 个模拟并发运行，自动队列管理 |
| **Auto-Tune 调参** | 自动搜索最优 Decay / Universe / Neutralization / Expression 参数 |
| **本地相关性计算** | 基于 PnL 曲线的本地 Prod Correlation 估算，无需等待平台计算 |
| **PC Range 预估** | 利用相关性传递性预估 Prod Corr 范围 |
| **PnL 可视化** | 内嵌 Matplotlib 图表，支持 IS/OS 分段、子区域 PnL、图例点击切换 |
| **语法高亮 & 补全** | FASTEXPR 语法高亮 + 运算符自动补全 |
| **VSCode 联动** | 一键在 VSCode 中编辑表达式，实时同步回 GUI |
| **批量操作** | 批量下载已提交 Alpha、批量归档/恢复 PPA Tags 和 Osmosis |
| **系统托盘** | 最小化到托盘，后台运行不干扰 |
| **中英双语** | 支持 English / 中文 界面切换，标题栏 ⚙ 菜单一键切换 |
| **无边框窗口** | 自定义标题栏，支持拖拽移动、边缘缩放、双击最大化 |

---

## 2. 环境与依赖

### 运行环境

- **操作系统**: Windows 10 / 11
- **Python**: 3.8+
- **网络**: 需能访问 `https://api.worldquantbrain.com`

### Python 依赖

```
PyQt5
matplotlib (Qt5Agg 后端, 含中文字体支持)
numpy
requests
pytz (Signals/Pyramids 季度时间计算)
ctypes (标准库, 用于无边框窗口边缘缩放)
```

### 安装

```bash
pip install PyQt5 matplotlib numpy requests pytz
```

> **也可直接运行**: 目录下已有打包好的 `brain_simulater.exe`，无需 Python 环境即可运行。

---

## 3. 项目结构

```
simulater/
├── brain_simulater.py        # 主程序 (≈12700 行)
├── brain_simulater.exe       # 打包好的可执行文件 (免 Python 运行)
├── common_config.py          # 全局配置 (区域/Universe/Neutralization/阈值/翻译等)
├── pc_range.py               # PC Range 预估算法 (独立可调试)
├── operators.json            # BRAIN 运算符定义 (从平台下载)
├── brain.ico                 # 应用图标
├── brain_credentials.json    # 登录凭据 (自动生成)
├── alphas_db.json            # 本地 Alpha 数据库 (已提交 Alpha 缓存)
├── pc_cache.json             # Prod Correlation 缓存
├── signals_cache.json        # Signals 计数缓存
├── pnl_csv_submitted/        # 已提交 Alpha 的 PnL CSV (按 region 子目录)
│   ├── USA/
│   ├── GLB/
│   └── ...
├── pnl_csv_unsubmitted/      # 未提交 Alpha 的 PnL CSV
├── write_desc/               # Claude Code write_desc skill
├── osmosis_archive_*.csv     # Osmosis 归档快照
├── ppa_tags_archive_*.csv    # PPA Tags 归档快照
└── __pycache__/
```

---

## 4. 快速开始

### 4.1 启动

```bash
python brain_simulater.py
```

### 4.2 登录

1. 在顶部 **Authentication** 栏输入 BRAIN 平台的 Email 和 Password
2. 点击 **Login** 按钮
3. 登录成功后显示 User ID、今日模拟次数、速度、Signals 数等信息

> **自动登录**: 若 `brain_credentials.json` 中保存了凭据，或 `common_config.py` 中设置了 `DEFAULT_EMAIL` / `DEFAULT_PASSWORD`，启动时自动填充。

### 4.3 运行第一个模拟

1. 在 **Alpha Expression** 文本框中输入 FASTEXPR 表达式，如 `rank(close)`
2. 在 **Simulation Settings** 中选择 Region、Universe、Delay 等参数
3. 点击 **Simulate** 按钮或按 `Ctrl+Enter`
4. 等待模拟完成，右侧面板显示结果

---

## 5. 核心架构

### 5.1 类层次结构

```
QMainWindow (FramelessWindowHint 无边框)
└── MainWindow                    # 主窗口：标题栏 + 登录栏 + 用户栏 + 标签页 + 状态栏
    ├── CustomTitleBar            # 自定义标题栏 (⚙ 设置/语言 + 最小化/最大化/关闭)
    ├── nativeEvent (WM_NCHITTEST)# Windows 原生消息处理，实现边缘缩放
    └── QTabWidget (DraggableTabBar)
        └── SimulateTab[]         # 每个模拟标签页
            ├── 左面板: 表达式 + 设置
            └── 右面板: 结果 (PnL图 + 指标 + 相关性 + 属性)

QThread Workers:
├── SimulationWorker              # 单次模拟
├── AutoTuneWorker                # Universe/Neutral 自动调参
├── DecayAutoTuneWorker           # Decay 自动调参
├── ExpressionAutoTuneWorker      # Expression 占位符自动调参
├── CorrelationWorker             # 相关性查询 (self/ppc/prod/checks)
├── DownloadSubmittedAlphasWorker # 批量下载已提交 Alpha
└── LocalCorrelationWorker        # 本地相关性计算

辅助组件:
├── BrainClient                   # BRAIN API 同步客户端
├── PnlCanvas (FigureCanvas)      # PnL 图表 (Matplotlib)
├── AlphaExprHighlighter          # 语法高亮
├── CompletionPopup (QListView)   # 运算符补全弹窗
├── DraggableTabBar (QTabBar)     # 可拖拽排序的标签栏
├── FlowLayout (QLayout)          # 自动换行标签布局
├── ListDialog (QWidget)          # 添加到列表对话框
└── _PinnedMetricsWindow          # 置顶指标截图窗口

国际化 (i18n):
├── T(text)                       # 翻译函数 (查 TRANSLATIONS 字典)
├── SYSTEM_LANGUAGE               # 当前语言 ('English' / 'Chinese')
└── TRANSLATIONS                  # 中英对照字典 (common_config.py)

配置动态化:
├── _load_config_value(name, fallback)  # 运行时读取 common_config.py
└── _save_config_value(name, value)     # 运行时写回 common_config.py (如 Local Corr 切换)
```

### 5.2 数据流

```
用户输入表达式 + 设置
        │
        ▼
  SimulateTab._on_simulate()
        │
        ▼
  SimulationWorker (QThread)
        │
        ▼
  BrainClient.create_simulation()
   ├── POST /simulations → 获取 Location
   ├── GET  Location (轮询) → 等待完成
   ├── GET  /alphas/{id} → Alpha 详情
   ├── GET  /alphas/{id}/recordsets/pnl → PnL 数据
   └── GET  /alphas/{id}/recordsets/yearly-stats → 年度统计
        │
        ▼
  SimulateTab._display_metrics() / _display_yearly() / PnlCanvas.plot_pnl()
```

### 5.3 并发与队列

- **最大并发权重**: 默认 8，GLB 模拟消耗 2 权重，其他消耗 1
- **模拟队列**: 超出并发上限的模拟自动排队，有槽位时自动启动
- **Auto-Tune**: 内部使用 `threading.Semaphore` 控制并发，最多同时运行 `max_concurrent` 个模拟

---

## 6. 功能详解

### 6.1 Alpha Expression 编辑器

| 功能 | 操作 |
|------|------|
| 语法高亮 | 自动识别运算符、关键字、常量、数据字段并着色 |
| 自动补全 | 输入时弹出匹配的运算符列表，Tab/Enter 确认 |
| 全屏编辑 | 点击 ⛶ 按钮展开表达式编辑器 |
| VSCode 编辑 | 点击 📝 按钮，在 VSCode 中打开临时文件编辑，自动同步回 GUI |
| 复制/导入 | Copy 按钮复制表达式到剪贴板，Import 按钮从剪贴板导入 |
| 展开/折叠 | 点击 "Alpha Expression" 标题栏折叠/展开编辑区 |

### 6.2 Simulation Settings

| 参数 | 说明 | 支持多值遍历 |
|------|------|:---:|
| **Region** | 市场区域 (USA, GLB, EUR, ASI, CHN, JPN 等) | ✅ |
| **Universe** | 股票池 (TOP3000, TOP2000 等，随 Region 变化) | ✅ |
| **Delay** | 数据延迟 (0 或 1，随 Region 变化) | — |
| **Decay** | 衰减值，支持多值输入 (如 `0,5,10` 或 `0:100:10`) | ✅ |
| **Neutralization** | 中性化方法 (SUBINDUSTRY, MARKET, NONE 等) | ✅ |
| **Truncation** | 截断值，支持多值输入 | ✅ |
| **Pasteurization** | ON / OFF | — |
| **NaN Handling** | ON / OFF | — |
| **Max Trade** | ON / OFF | — |
| **Max Pos** | ON / OFF | — |
| **Language** | FASTEXPR (默认) | — |
| **Lookback** | 回看天数 | — |

> **多值遍历**: 在 Decay / Truncation 输入框中输入多个值（逗号分隔或范围格式），点击 **Traverse** 按钮依次模拟每个值。

**范围格式示例**:
- `0:100:10` → [0, 10, 20, ..., 100]
- `0:100:10:[20,50]` → 排除 20 和 50
- `1,2,3` → [1, 2, 3]

### 6.3 模拟操作

| 操作 | 按钮/快捷键 | 说明 |
|------|------------|------|
| **Simulate** | Simulate / `Ctrl+Enter` | 运行当前表达式（自动检测多值/占位符并触发 Traverse） |
| **Fill** | Fill | 自动填充下一个可用槽位并运行（批量克隆当前标签页） |
| **Cancel** | Cancel | 取消当前模拟 |
| **Tune** | Tune | 打开 Auto-Tune（检测占位符类型自动选择模式） |
| **Refetch** | Refetch | 重新获取当前 Alpha 的数据 |
| **Reverse** | Reverse | 反转 Alpha (取负 PnL) |

> **Simulate 的自动检测逻辑**: 点击 Simulate 时，程序会按以下优先级自动检测：
> 1. 若 Decay 输入框含多个值 → 自动触发 Decay Traverse
> 2. 若 Truncation 输入框含多个值 → 自动触发 Truncation Traverse
> 3. 若两者都含多个值 → 先 Traverse Decay，每个新标签页再自动 Traverse Truncation（笛卡尔积）
> 4. 若表达式含 `<...>` 占位符 → 自动触发 Expression Traverse
> 5. 若设置项含 `?` 占位符 → 提示必须使用 Tune
> 6. 否则 → 正常单次模拟

### 6.4 Auto-Tune 自动调参

Auto-Tune 是核心调参功能，支持多种参数维度的自动搜索。它通过在表达式或设置中使用**占位符**来标记需要搜索的参数，然后并发运行所有候选值，选出评分最高的结果。

**与 Traverse 的区别**:
- **Traverse**: 为每个候选值打开独立的标签页，所有结果都保留，用户自行比较
- **Tune**: 在后台并发运行所有候选值，自动选出最优结果应用到当前标签页

详细语法规则见 [第 7 节](#7-traverse-语法规则) 和 [第 8 节](#8-tune-语法规则)。

### 6.5 结果展示

#### 6.5.1 Key Metrics (关键指标)

| 指标 | 说明 |
|------|------|
| **Alpha ID** | 唯一标识，可点击复制 |
| **Fitness** | 适应度 |
| **Margin** | 利润 |
| **Turnover** | 换手率 |
| **Returns** | 收益率 |
| **Sharpe** | 夏普比率 |
| **Drawdown** | 最大回撤 |
| **Margin/Drawdown** | 利润回撤比 |
| **Score** | Tune 评分 |

> 点击 📌 按钮可将 Key Metrics 截图置顶显示（始终在最前的小窗口）。

#### 6.5.2 PnL 图表

- **IS PnL** (青色) / **OS PnL** (绿色): 已提交的 Alpha 自动分段显示
- **Risk Neutralized PnL** (绿色): 风险中性化 PnL
- **Investability Constrained PnL** (黄绿色): 可投资性约束 PnL
- **GLB Sub-region PnL** (AMER/APAC/EMEA 虚线): 全球区域子 PnL
- **图例点击**: 点击图例文字可切换对应线条的显示/隐藏
- **工具栏**: Matplotlib NavigationToolbar，支持缩放、平移、保存

#### 6.5.3 Yearly Stats (年度统计)

表格显示每年的 Fitness、Sharpe、Turnover、Returns、Drawdown 等指标。

#### 6.5.4 Correlation (相关性)

| 类型 | 按钮文案 (本地模式) | 说明 | 阈值 |
|------|------|------|------|
| **Self Corr** | Local SC | 与自己已提交 Alpha 的相关性 | < 0.7 |
| **PPC Corr** | Local PPC | Power Pool Correlation | < 0.5 |
| **Prod Corr** | Prod Corr | Production Correlation (平台计算) | — |
| **Checks** | Checks | 提交前检查项 | — |
| **Inter Corr** | Inter Corr | 与指定 Alpha ID 的交叉相关性 | — |
| **PC Range** | PC Range | 本地预估 Prod Corr 范围 | — |

> **本地模式 vs API 模式**: 当 ⚙ → Use Local Corr 勾选时（默认开启），Self Corr 和 PPC 通过本地缓存的 PnL 数据计算（按钮显示 "Local SC" / "Local PPC"），速度快且无需等待平台；取消勾选则走平台 API（按钮显示 "Self Corr" / "PPC"）。Strict Platform Parity 开启时，本地 SELF 池会排除近 30 天新提交的 peer，使结果与平台批量刷新快照一致。

> **PC Range 预估**: 利用 PnL 曲线相关性的传递性，通过已知的 inter-correlation 和 peer 的 prod-correlation 估算目标 Alpha 的 prod-correlation 范围。公式：
> ```
> corr(A,prod) ∈ [p1·p2 − √((1−p1²)(1−p2²)),  p1·p2 + √((1−p1²)(1−p2²))]
> ```

#### 6.5.5 Properties (属性)

- **Name**: Alpha 名称
- **Color**: 标记颜色
- **Tags**: 标签列表
- **Category**: 分类 (REGULAR / POWER_POOL 等)
- **Regular Desc**: 表达式描述
- **Write Desc**: 调用 Claude CLI 自动生成描述
- **Submit**: 提交 Alpha 到生产环境
- **Add to List**: 将 Alpha 添加到列表

### 6.6 批量操作 (Funcs 菜单)

顶部用户栏中的 **Funcs** 下拉按钮提供批量操作：

| 操作 | 说明 |
|------|------|
| **Download Operators** | 从平台下载最新运算符定义到 `operators.json` |
| **Used Operators** | 显示已提交 Alpha 中使用的运算符及使用次数 |
| **Unused Operators** | 显示已下载但未在已提交 Alpha 中使用的运算符 |
| **Used Datafields** | 显示已提交 Alpha 中使用的数据字段 |
| **Download Submitted Alphas** | 批量下载所有已提交 Alpha 的详情和 PnL 数据到本地 |
| **Archive PPA Tags** | 将当前 PPA Tags 归档到 CSV 文件 |
| **Empty PPA Tags** | 清空所有 PPA Tags (可按 Region/Delay 过滤) |
| **Restore PPA Tags** | 从归档 CSV 恢复 PPA Tags |
| **Archive Osmosis** | 归档当前 Osmosis 设置 |
| **Empty Osmosis** | 清空所有 Osmosis (可按 Region/Delay 过滤) |
| **Restore Osmosis** | 从归档 CSV 恢复 Osmosis |
| **Copy All AIDs** | 复制所有已提交 Alpha 的 ID |

### 6.7 标签页管理

| 操作 | 说明 |
|------|------|
| **新建标签** | 点击 `+` 按钮或 `Ctrl+T` |
| **关闭标签** | 点击标签上的 `×` 或 `Ctrl+W` |
| **切换标签** | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| **跳转未查看** | `Ctrl+J` 跳转到下一个已完成未查看的标签 |
| **拖拽排序** | 拖拽标签可重新排序 |
| **克隆标签** | 右键菜单 → Clone，复制当前标签的设置到新标签 |
| **取消其他** | 右键菜单 → Cancel Others，取消除当前标签外的所有运行中模拟 |
| **移动标签** | `Ctrl+Shift+←/→` 移动标签位置 |
| **标签列表** | `Ctrl+L` 弹出标签列表快速跳转 |
| **关闭空闲标签** | 状态栏 Close Idle Tabs 按钮或右键菜单 |

### 6.8 顶部用户栏

登录后显示的用户信息栏，包含：

| 元素 | 说明 |
|------|------|
| **User ID** | 当前登录用户 ID |
| **Today Simulated** | 今日模拟次数（每 10 分钟自动刷新，或点击 ⟳ 手动刷新） |
| **Speed** | 模拟速度统计 |
| **Signals** | 本季度提交的 Signals 数（按 US/Eastern 时区计算季度，点击 ⟳ 刷新） |
| **Pyramids** | 本季度完成的金字塔数（按 US/Eastern 时区计算季度，点击 ⟳ 刷新） |
| **Fetch Alpha** | 输入 Alpha ID 直接获取并展示其详情 |
| **Funcs** | 批量操作下拉菜单 (见 6.6) |

### 6.9 自定义标题栏 (Custom TitleBar)

窗口采用无边框设计，顶部为自定义标题栏：

| 元素 | 说明 |
|------|------|
| **图标 + 标题** | 左侧显示应用图标和窗口标题 |
| **⚙ 设置按钮** | 弹出设置菜单（含 Language / Use Local Corr / Strict Platform Parity） |
| **Language** | 切换界面语言 English / 中文，实时刷新所有可见文本 |
| **Use Local Corr** | 勾选后用本地缓存的 PnL 计算 Self Corr / PPC（按钮显示为 "Local SC"/"Local PPC"），否则走平台 API |
| **Strict Platform Parity** | 仅在 Use Local Corr 开启时可用，排除近 30 天提交的 peer 以对齐平台快照 |
| **─ 最小化** | 最小化窗口（到托盘） |
| **□ / ❐ 最大化/还原** | 切换最大化/窗口模式 |
| **× 关闭** | 退出程序 |
| **拖拽** | 在标题栏空白处按住左键拖动可移动窗口 |
| **双击** | 双击标题栏切换最大化/还原 |
| **边缘缩放** | 鼠标移到窗口四边/四角可拖拽缩放窗口大小 |

> **语言切换**: 点击 ⚙ → Language → English / 中文。切换后调用 `_switch_language()` 刷新窗口标题、标签、按钮、菜单等所有可见文本。默认语言由 `common_config.py` 中的 `SYSTEM_LANGUAGE` 决定。

> **本地相关模式**: 点击 ⚙ → Use Local Corr 勾选/取消。该设置会通过 `_save_config_value()` 持久化到 `common_config.py`，重启后保留。开启时 Self Corr / PPC 按钮文案变为 "Local SC" / "Local PPC"，计算基于本地缓存的 PnL；关闭时调用平台 API。Strict Platform Parity 子选项用于排除近 30 天新提交的 peer，使本地 SELF 池与平台批量刷新的快照保持一致（PPC 池不受影响）。

### 6.10 系统托盘

- 最小化窗口时自动隐藏到系统托盘
- 双击托盘图标恢复窗口
- 右键托盘菜单: Show / Quit

---

## 7. Traverse 语法规则

Traverse 是"遍历"功能：为每个候选值**打开独立标签页**并运行模拟，所有结果都保留供用户比较。触发方式有两种：**手动点击 Traverse 按钮**，或**在 Simulate 时自动检测多值输入**。

### 7.1 Decay Traverse

**触发方式**:
- Decay 输入框中输入多个值，然后点击 Simulate（自动触发）
- 点击 Decay 旁的 Traverse 按钮

**输入格式**:

| 格式 | 示例 | 展开结果 | 说明 |
|------|------|----------|------|
| 逗号分隔 | `0,5,10,21` | [0, 5, 10, 21] | 逐个列出 |
| 范围 | `0:100:10` | [0, 10, 20, ..., 100] | `start:end:step` |
| 范围+排除 | `0:100:10:[20,50]` | [0, 10, 30, 40, 60, ..., 100] | 排除指定值 |
| 单值 | `10` | 使用默认网格 | 单值时 Traverse 使用 `COARSE_DECAYS` |

**默认网格** (`COARSE_DECAYS`):
```
[0, 5, 10, 15, 21, 42, 63, 126, 252, 512]
```

**行为**:
- 若当前标签页已完成模拟：保留当前结果，为剩余值各开新标签页
- 若当前标签页空闲：第一个值用当前标签页，其余开新标签页
- 已模拟过的值不会重复模拟（通过 `_simulated_decay` 集合跟踪）

### 7.2 Truncation Traverse

**触发方式**: 同 Decay Traverse

**输入格式**: 同 Decay，支持逗号分隔、范围、范围+排除

**默认网格** (`DEFAULT_TRUNCS`):
```
[0.001, 0.005, 0.01, 0.03, 0.05, 0.1]
```

### 7.3 Universe Traverse

**触发方式**: 点击 Universe 旁的 Traverse 按钮

**行为**: 遍历当前 Region 下所有可用 Universe（跳过 `skip_universes` 中的项），每个 Universe 开一个新标签页。

**示例** (Region = USA):
```
TOP3000, TOP2000, TOP1000, TOP500, TOP200, TOPSP500, ILLIQUID_MINVOL1M
```

### 7.4 Neutralization Traverse

**触发方式**: 点击 Neutralization 旁的 Traverse 按钮

**行为**: 遍历当前 Region 下所有 Neutralization（**跳过 NONE 和 SLOW_AND_FAST**），每个开一个新标签页。

**示例** (Region = USA):
```
REVERSION_AND_MOMENTUM, CROWDING, FAST, SLOW, STATISTICAL, SUBINDUSTRY, INDUSTRY, SECTOR, MARKET
```

### 7.5 Expression Traverse (表达式占位符遍历)

**触发方式**: 在表达式中使用 `<...>` 占位符，然后点击 Simulate（自动触发）

这是 Traverse 最灵活的用法，支持在表达式的任意位置插入占位符。

#### 7.5.1 基本语法

| 占位符格式 | 说明 | 示例表达式 | 展开结果 |
|-----------|------|-----------|----------|
| `<值1,值2,值3>` | 逗号分隔枚举 | `ts_delay(close, <1,5,10>)` | 3 个表达式：`ts_delay(close, 1)`, `ts_delay(close, 5)`, `ts_delay(close, 10)` |
| `<单值>` | 单个值（仅1个结果） | `rank(<close>)` | 1 个表达式：`rank(close)` |
| `<start:end:step>` | 数值范围 | `ts_decay_linear(close, <0:10:2>)` | 6 个表达式：step=0,2,4,6,8,10 |
| `<start:end:step:[排除]>` | 范围+排除 | `ts_sum(close, <0:20:5:[10]>)` | 3 个表达式：step=0,5,15,20（排除10） |

#### 7.5.2 多占位符 — 笛卡尔积

表达式中可以包含**多个** `<...>` 占位符，它们会生成**笛卡尔积**：

```
ts_corr(<close,open>, <volume>, <5,10,21>)
```

展开为 2 × 1 × 3 = **6** 个表达式：
```
ts_corr(close, volume, 5)
ts_corr(close, volume, 10)
ts_corr(close, volume, 21)
ts_corr(open, volume, 5)
ts_corr(open, volume, 10)
ts_corr(open, volume, 21)
```

#### 7.5.3 范围格式详解

```
<start:end:step>
<start:end:step:[exclude1,exclude2,...]>
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `start` | 浮点数 | 起始值（含） |
| `end` | 浮点数 | 终止值（含） |
| `step` | 浮点数 | 步长（0 则只返回 start） |
| `[excludes]` | 可选 | 方括号内逗号分隔的排除值列表 |

**规则**:
- 整数值自动转为 int 显示（`5.0` → `5`）
- step > 0 时从 start 递增到 end
- step < 0 时从 start 递减到 end
- 排除值支持整数和浮点数

**示例**:

| 输入 | 结果 |
|------|------|
| `<0:100:10>` | [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100] |
| `<0:100:10:[20,50]>` | [0, 10, 30, 40, 60, 70, 80, 90, 100] |
| `<5:5:0>` | [5] (step=0 只返回起始值) |
| `<10:0:-2>` | [10, 8, 6, 4, 2, 0] (递减) |

### 7.6 Decay + Truncation 联合 Traverse

当 Decay 和 Truncation 输入框**同时**包含多个值时，Simulate 会自动触发**笛卡尔积遍历**：

1. 先 Traverse Decay（每个 Decay 值开一个新标签页）
2. 每个新标签页的 Truncation 输入框仍保留多值列表
3. 新标签页调用 `_on_simulate()` 时自动检测到多值 Truncation → 再次 Traverse

**示例**:
- Decay = `0,10,21`
- Truncation = `0.01,0.05`
- 结果：3 × 2 = **6** 个标签页

### 7.7 Traverse 行为总结

| 场景 | 行为 |
|------|------|
| 当前标签页**空闲** | 第一个值用当前标签页模拟，其余值各开新标签页 |
| 当前标签页**已完成** | 保留当前结果，所有值各开新标签页 |
| 已模拟过的值 | 跳过，不重复模拟（显示 "All xxx already simulated"） |
| 新标签页启动 | 延迟 100ms 后自动调用 `_on_simulate()` |

---

## 8. Tune 语法规则

Tune 是"自动调参"功能：并发运行所有候选值，**自动选出评分最高的结果**应用到当前标签页。与 Traverse 不同，Tune 不会为每个候选值保留标签页，而是只保留最优结果。

### 8.1 Tune 占位符体系总览

Tune 使用两套占位符，分别作用于**设置项**和**表达式**：

| 位置 | 占位符 | 作用 | 搜索空间 |
|------|--------|------|----------|
| Decay 输入框 | `?` | 搜索最优 Decay | `COARSE_DECAYS` |
| Truncation 输入框 | `?` | 搜索最优 Truncation | `DEFAULT_TRUNCS` |
| Universe 下拉框 | `?` | 搜索最优 Universe | 当前 Region 所有 Universe |
| Neutralization 下拉框 | `?` | 搜索最优 Neutralization | 当前 Region 所有 Neutralization（不含 NONE） |
| 表达式 | `<?>` | 搜索最优数值替换 | `DEFAULT_VALUES` |
| 表达式 | `<?g>` | 搜索最优 Glossary 替换 | `WHITE_LIST` |

### 8.2 设置项占位符语法

在 Decay / Truncation 输入框或 Universe / Neutralization 下拉框中使用 `?` 占位符：

#### 8.2.1 基本占位符 `?`

| 位置 | 输入 | 搜索空间 |
|------|------|----------|
| Decay | `?` | `[0, 5, 10, 15, 21, 42, 63, 126, 252, 512]` |
| Truncation | `?` | `[0.001, 0.005, 0.01, 0.03, 0.05, 0.1]` |
| Universe | `?` | 当前 Region 所有可用 Universe |
| Neutralization | `?` | 当前 Region 所有 Neutralization（不含 NONE） |

**示例**: Decay 输入框填 `?`，点击 Tune → 自动遍历所有 COARSE_DECAYS 值，选出最优。

#### 8.2.2 自定义搜索值 `?=值1,值2,...`

在 `?` 后用 `=` 指定自定义搜索列表：

| 位置 | 输入 | 搜索空间 |
|------|------|----------|
| Decay | `?=0,5,10` | [0, 5, 10] |
| Truncation | `?=0.01,0.05` | [0.01, 0.05] |
| Universe | `?=TOP3000,TOP500` | ["TOP3000", "TOP500"] |
| Neutralization | `?=SUBINDUSTRY,MARKET` | ["SUBINDUSTRY", "MARKET"] |

自定义值同样支持范围格式：

| 输入 | 搜索空间 |
|------|----------|
| `?=0:100:10` | [0, 10, 20, ..., 100] |
| `?=0:100:10:[20,50]` | [0, 10, 30, 40, 60, ..., 100] |

#### 8.2.3 带前缀的占位符 `值?N` (用于 Sequential Tune)

格式: `已有值?序号` 或 `已有值?序号=自定义值`

| 输入 | 含义 |
|------|------|
| `10?1` | 当前 Decay=10，第1轮 Tune 搜索所有 COARSE_DECAYS（排除10） |
| `10?1=0,5,21` | 当前 Decay=10，第1轮 Tune 搜索 [0, 5, 21]（排除10） |
| `0.05?2` | 当前 Truncation=0.05，第2轮 Tune 搜索 DEFAULT_TRUNCS（排除0.05） |

> **前缀值**会被设为当前标签页的初始值，并从搜索列表中排除（避免重复模拟）。

### 8.3 表达式占位符语法

在 Alpha 表达式中使用 `<?...>` 形式的占位符：

#### 8.3.1 数值占位符 `<?>`

搜索空间: `DEFAULT_VALUES = [2, 5, 10, 15, 21, 42, 63, 126, 252]`

```
ts_decay_linear(close, <?>)
```

Tune 时自动将 `<?>` 替换为 DEFAULT_VALUES 中的每个值，选出最优。

#### 8.3.2 Glossary 占位符 `<?g>`

搜索空间: `WHITE_LIST = [subindustry, industry, sector, market, country, exchange, currency]`

> 若当前 Region 不支持 COUNTRY 中性化，则自动排除 `country`。

```
group_neutralize(rank(close), <?g>)
```

Tune 时自动将 `<?g>` 替换为 WHITE_LIST 中的每个值。

#### 8.3.3 自定义搜索值 `<?=值1,值2,...>` 和 `<?g=值1,值2,...>`

```
ts_delay(close, <?=3,7,14,28>)
group_neutralize(rank(close), <?g=sector,industry,subindustry>)
```

自定义值同样支持范围格式：

```
ts_decay_linear(close, <?=0:50:10>)
```

#### 8.3.4 编号占位符 `<?N>` / `<?gN>` / `<?=值,...>` (用于 Sequential Tune)

为占位符指定序号，控制 Tune 的执行顺序：

```
group_neutralize(ts_decay_linear(<?1>, <?2>), <?g3>)
```

- `<?1>`: 第1轮，搜索 DEFAULT_VALUES
- `<?2>`: 第2轮（第1轮最优确定后），搜索 DEFAULT_VALUES
- `<?g3>`: 第3轮，搜索 WHITE_LIST

带自定义值：

```
ts_corr(<?1=close,open,volume>, <?2=5,10,21>)
```

#### 8.3.5 表达式范围占位符 `<start:end:step>`

在 Tune 中也支持范围格式（与 Traverse 相同语法）：

```
ts_decay_linear(close, <0:50:10>)
```

搜索空间: [0, 10, 20, 30, 40, 50]

> **注意**: 范围占位符 `<0:50:10>` 在 Tune 中被视为**无编号**占位符（order=0），参与笛卡尔积模式。

### 8.4 两种 Tune 模式

Tune 有两种互斥的执行模式，由占位符是否带编号决定：

#### 8.4.1 笛卡尔积模式 (Cartesian) — 所有占位符无编号

**条件**: 所有占位符都是 `?` / `<?>` / `<?g>` / `<start:end:step>` （无数字编号）

**行为**: 所有占位符的候选值做**笛卡尔积**，一次性并发运行所有组合，选出全局最优。

**示例**:

```
表达式: group_neutralize(ts_decay_linear(close, <?>), <?g>)
Decay: ?
```

3 个占位符的搜索空间：
- `<?>`: [2, 5, 10, 15, 21, 42, 63, 126, 252] → 9 个值
- `<?g>`: [subindustry, industry, sector, market, country, exchange, currency] → 7 个值
- Decay `?`: [0, 5, 10, 15, 21, 42, 63, 126, 252, 512] → 10 个值

笛卡尔积: 9 × 7 × 10 = **630** 个组合（并发运行，受槽位限制）

#### 8.4.2 顺序模式 (Sequential) — 所有占位符有编号

**条件**: 所有占位符都带编号 `?1` / `?2` / `<?1>` / `<?g2>` 等

**行为**: 按**编号从小到大**依次执行，每轮只调一个（或一组同编号的）占位符，前一轮的最优结果作为后一轮的固定值。

**示例**:

```
表达式: group_neutralize(ts_decay_linear(close, <?1>), <?g2>)
Decay: ?3
```

执行顺序：
1. **第1轮** (`?1`): 固定其他参数，遍历 `<?>` 的 DEFAULT_VALUES → 选出最优值（如 21）
2. **第2轮** (`?2`): 表达式变为 `group_neutralize(ts_decay_linear(close, 21), <?g>)`，遍历 WHITE_LIST → 选出最优值（如 sector）
3. **第3轮** (`?3`): 表达式变为 `group_neutralize(ts_decay_linear(close, 21), sector)`，遍历 COARSE_DECAYS → 选出最优值（如 10）

最终结果: `group_neutralize(ts_decay_linear(close, 21), sector)`, Decay=10

> **优势**: 顺序模式大幅减少总模拟次数。上例笛卡尔积需 9×7×10=630 次，顺序模式只需 9+7+10=**26** 次。

#### 8.4.3 混合编号禁止

**不能**同时使用编号占位符和无编号占位符，否则报错：

```
❌ 错误: <?1> 和 <?> 混用
❌ 错误: ?1 (Decay) 和 ? (Universe) 混用
✅ 正确: 全部用编号 → <?1>, <?g2>, ?3
✅ 正确: 全部无编号 → <?>, <?g>, ?
```

### 8.5 同编号笛卡尔积

在顺序模式中，**同一编号**的多个占位符会做笛卡尔积：

```
表达式: ts_corr(<?1>, <?1>, <?2>)
```

- **第1轮** (编号1): `<?>` × `<?>` 的笛卡尔积（如 close×close, close×open, open×close, open×open）
- **第2轮** (编号2): 固定第1轮最优的两个值，遍历 `<?>`

### 8.6 Tune 按钮的自动模式选择

点击 **Tune** 按钮时，程序自动检测占位符类型并选择模式：

| 检测条件 | 自动行为 |
|----------|----------|
| 表达式含 `<?N>` / `<?gN>` / `<start:end:step>` | → Sequential Tune |
| 表达式含 `<?>` / `<?g>` | → Cartesian Tune |
| 设置项含 `?` / `?N` / `值?N` | → 对应模式 Tune |
| Tune Expand 复选框勾选 | → 强制进入 Expand 模式 |
| **无任何占位符** | → 默认 Decay Tune（自动设置 Decay=`?`） |

### 8.7 Tune Expand (链式调参)

Tune Expand 是一种特殊的 Sequential Tune，通过勾选 **Tune Expand** 复选框触发，或当检测到编号占位符时自动触发。

**执行流程**:
1. 解析所有占位符，按编号分组
2. 第1组：并发运行所有组合，选出最优 → 将最优值应用到当前标签页
3. 第2组：基于第1组最优结果，并发运行所有组合，选出最优 → 应用
4. ... 依次类推
5. 全部完成后，将全局最优标签页移到最左侧

**标签页命名规则**: `T{批次ID} G{组序号} {参数标签}`

示例: `T3 G1 d=21` 表示第3批调参、第1组、Decay=21

### 8.8 评分函数

Tune 使用评分函数对每个模拟结果打分，选出最高分。

**默认评分** (`common_config.py` 中的 `default_get_tune_score()`):

```python
score = |fitness| × 10 + |margin|

# 检查项惩罚: 若下列检查为 FAIL 则扣 1000 分
if CONCENTRATED_WEIGHT == "FAIL":      score -= 1000
if LOW_SUB_UNIVERSE_SHARPE == "FAIL":  score -= 1000
```

**评分逻辑说明**:
- `fitness` 是主要打分依据（权重 ×10）
- `margin` 作为次要加分
- `CONCENTRATED_WEIGHT`（集中权重）和 `LOW_SUB_UNIVERSE_SHARPE`（子股票池夏普过低）检查失败会大幅扣分
- 检查项从 `alpha_data["is"]["checks"]` 列表中提取

**硬编码兜底** (当 `common_config.py` 无法加载时):

```python
score = |fitness| × 10 + |margin|
```

**临时自定义**: 点击 Tune 面板中的 **Edit Score** 按钮，输入 Python 代码。需定义 `get_tune_score(alpha_data)` 函数并返回数值。`alpha_data` 结构：

```python
alpha_data = {
    "is": {
        "fitness": 0.123,
        "margin": 456.78,
        "sharpe": 1.5,
        "turnover": 0.3,
        "returns": 0.05,
        "drawdown": -200,
        "checks": [   # 检查项列表 (含 name, result, value, limit 等)
            {"name": "CONCENTRATED_WEIGHT", "result": "PASS", ...},
            {"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL", ...},
            ...
        ],
        ...
    },
    ...
}
```

**永久自定义**: 编辑 `common_config.py` 中的 `default_get_tune_score()` 函数。该函数通过 `_load_config_value()` 动态加载，即使打包成 exe 也能读取同目录的 `common_config.py`。

### 8.9 Tune 与 Traverse 的完整对比

| 维度 | Traverse | Tune |
|------|----------|------|
| **触发方式** | Simulate 自动检测多值 / Traverse 按钮 | Tune 按钮 / Tune Expand |
| **占位符** | `<值1,值2>` / `<start:end:step>` | `?` / `<?>` / `<?g>` / `?N` / `<?N>` |
| **结果保留** | 每个候选值一个标签页，全部保留 | 只保留最优结果 |
| **执行方式** | 依次开标签页模拟 | 后台并发模拟 |
| **评分** | 无（用户自行比较） | 自动评分选最优 |
| **模式** | 仅笛卡尔积 | 笛卡尔积 / 顺序 |
| **适用场景** | 想看所有结果、手动比较 | 快速找到最优参数 |

### 8.10 完整示例

#### 示例 1: 简单 Decay Tune

```
表达式: rank(close)
Decay: ?
```

点击 Tune → 遍历 [0, 5, 10, 15, 21, 42, 63, 126, 252, 512]，选出最优 Decay。

#### 示例 2: Expression + Decay 笛卡尔积 Tune

```
表达式: ts_decay_linear(close, <?>)
Decay: ?
```

点击 Tune → `<?>` × `?` 笛卡尔积，9 × 10 = 90 个组合。

#### 示例 3: 顺序 Tune (推荐)

```
表达式: group_neutralize(ts_decay_linear(close, <?1>), <?g2>)
Decay: ?3
```

点击 Tune → 3 轮顺序执行：先调 Expression，再调 Glossary，最后调 Decay。总模拟 9+7+10=26 次。

#### 示例 4: 自定义搜索值

```
表达式: ts_delay(<?1=close,open,volume>, <?2=3,7,14,28>)
Neutralization: ?3=SUBINDUSTRY,INDUSTRY,MARKET
```

点击 Tune → 3 轮：先调数据字段(3值)，再调延迟(4值)，最后调中性化(3值)。总模拟 3+4+3=10 次。

#### 示例 5: 带前缀的设置项

```
表达式: rank(close)
Decay: 10?1
```

点击 Tune → 当前 Decay=10，第1轮搜索 COARSE_DECAYS（排除10），选出最优。

#### 示例 6: Traverse 多占位符

```
表达式: ts_corr(<close,open>, <volume,returns>, <5,10,21>)
```

点击 Simulate → 自动展开为 2 × 2 × 3 = 12 个标签页。

#### 示例 7: Decay + Truncation 联合 Traverse

```
Decay: 0,10,21
Truncation: 0.01,0.05
```

点击 Simulate → 3 × 2 = 6 个标签页（笛卡尔积）。

---

## 9. 配置文件说明

### 9.1 common_config.py

全局配置文件，定义区域、Universe、Neutralization 选项、阈值和翻译字典。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `SYSTEM_LANGUAGE` | 界面语言 | `'English'` (可改为 `'Chinese'`) |
| `use_local_corr` | 是否使用本地 PnL 计算 Self Corr / PPC | `True` |
| `strict_platform_parity` | 严格平台一致性（排除近 30 天新提交的 peer） | `True` |
| `COARSE_DECAYS` | Decay 粗搜网格 | `[0, 5, 10, 15, 21, 42, 63, 126, 252, 512]` |
| `DEFAULT_VALUES` | Expression `<?>` 搜索网格 | `[2, 5, 10, 15, 21, 42, 63, 126, 252]` |
| `DEFAULT_TRUNCS` | Truncation 搜索网格 | `[0.001, 0.005, 0.01, 0.03, 0.05, 0.1]` |
| `get_check_dict(IS)` | 从 checks 列表构建 name→result 映射 | — |
| `default_get_tune_score()` | 默认评分函数 | `|fitness|×10 + |margin| − 惩罚` |
| `REGION_ARR` | 区域列表 | `['USA', 'GLB', 'EUR', ...]` |
| `DELAY_DICT` | 各区域可用 Delay | `{USA: [1,0], GLB: [1], ...}` |
| `UNIVERSE_DICT` | 各区域可用 Universe | `{USA: ['TOP3000', ...], ...}` |
| `NEUTRALIZATION_DICT` | 各区域可用 Neutralization | `{USA: [...], ...}` |
| `PC_THRESHOLD` | Self Corr 阈值 | `0.7` |
| `PPC_THRESHOLD` | PPC 阈值 | `0.5` |
| `MAX_ZERO_LEN` | 最大零长度容忍 | `6` |
| `MIN_SIMULATE_LEN` | 最小模拟数据长度 | `499` |
| `GLB_SUB_REGION_SHARPE_THRESHOLD` | GLB 子区域 Sharpe 阈值 | `1` |
| `settings_size_cfg` | 设置面板各控件宽度配置 | — |
| `WHITE_LIST` | Neutralization 白名单 | `['subindustry', 'industry', ...]` |
| `TRANSLATIONS` | 中英翻译字典 (700+ 条目) | 见 `common_config.py` |

> **动态加载与持久化**: `common_config.py` 中的配置通过 `_load_config_value()` 动态读取，即使打包成 exe 也能从同目录读取，修改后重启应用即生效。运行时通过 ⚙ 菜单修改的设置（如 Use Local Corr、Strict Platform Parity）会通过 `_save_config_value()` 自动写回 `common_config.py`，无需手动编辑。

### 9.2 brain_credentials.json

自动生成的登录凭据文件：

```json
{
  "email": "your@email.com",
  "password": "your_password"
}
```

> 优先级: `DEFAULT_EMAIL/PASSWORD` (代码中设置) > `brain_credentials.json` > 手动输入

### 9.3 operators.json

从 BRAIN 平台下载的运算符定义，包含名称、作用域 (scope) 等信息。用于语法高亮和自动补全。

### 9.4 alphas_db.json

本地缓存的已提交 Alpha 数据库，用于本地相关性计算。结构：

```json
{
  "alpha_id": {
    "region": "USA",
    "classification": "REGULAR:REGULAR",
    "dateSubmitted": "...",
    ...
  }
}
```

### 9.5 pc_cache.json

Prod Correlation 缓存，避免重复计算：

```json
{
  "alpha_id": {"max": 0.45, "min": 0.12}
}
```

---

## 10. 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Enter` | 运行模拟 (Simulate) |
| `Ctrl+T` | 新建标签页 |
| `Ctrl+W` | 关闭当前标签页 |
| `Ctrl+Tab` | 切换到下一个标签 |
| `Ctrl+Shift+Tab` | 切换到上一个标签 |
| `Ctrl+J` | 跳转到下一个未查看的已完成标签 |
| `Ctrl+L` | 打开标签列表弹窗 |
| `Ctrl+Shift+←` | 向左移动当前标签 |
| `Ctrl+Shift+→` | 向右移动当前标签 |
| `Esc` | 取消当前模拟 / 关闭弹窗 |

---

## 11. 常见问题

### Q: 登录失败怎么办？

1. 检查网络连接，确保能访问 `https://api.worldquantbrain.com`
2. 确认 Email 和 Password 正确
3. 如果提示 "Biometric auth required"，需要在浏览器中完成生物识别验证后重试

### Q: 模拟一直卡在 "Polling..." 怎么办？

1. 检查网络连接
2. 点击 Cancel 取消当前模拟
3. 可能是平台负载较高，稍后重试

### Q: Auto-Tune 结果不理想？

1. 尝试修改评分函数 (Edit Score 按钮)，例如加入 Sharpe 或 Turnover 权重
2. 调整 `COARSE_DECAYS` 或 `DEFAULT_VALUES` 搜索网格
3. 使用 Tune Expand 进行链式调参 (Universe → Neutral → Decay)
4. 检查是否因 `CONCENTRATED_WEIGHT` 或 `LOW_SUB_UNIVERSE_SHARPE` 检查失败导致扣分

### Q: PC Range 预估不准确？

1. 确保已下载足够的已提交 Alpha PnL 数据 (Funcs → Download Submitted Alphas)
2. PC Range 是基于传递性的估算，仅供参考
3. 最终以平台计算的 Prod Corr 为准

### Q: 如何自定义评分函数？

1. **临时修改**: 点击 Tune 面板中的 **Edit Score** 按钮，输入定义 `get_tune_score(alpha_data)` 的 Python 代码
2. **永久修改**: 编辑 `common_config.py` 中的 `default_get_tune_score()` 函数

### Q: PnL CSV 数据存储在哪里？

- 已提交 Alpha: `pnl_csv_submitted/{region}/` 目录
- 未提交 Alpha: `pnl_csv_unsubmitted/` 目录
- 旧版路径 `pnl_csv/` 会在启动时自动迁移到新路径

### Q: 如何在 VSCode 中编辑表达式？

1. 点击表达式编辑器旁的 📝 按钮
2. VSCode 会打开临时文件 `expression.txt`
3. 在 VSCode 中编辑并保存，GUI 自动同步更新
4. 支持多标签页独立编辑

### Q: GLB 模拟为什么消耗 2 个槽位？

GLB (全球) 模拟需要在 AMER、APAC、EMEA 三个子区域分别计算，计算量约为单区域的 2 倍，因此权重设为 2。

### Q: 如何切换界面语言？

1. **运行时切换**: 点击标题栏 ⚙ 按钮 → Language → English / 中文
2. **默认语言**: 编辑 `common_config.py` 中的 `SYSTEM_LANGUAGE = 'Chinese'`
3. 切换后所有可见文本（按钮、标签、菜单、提示框）实时刷新

### Q: 为什么 PnL 图表中文乱码？

程序已配置 Matplotlib 中文字体回退链（Microsoft YaHei → SimHei → SimSun），若仍乱码，请确保系统安装了中文字体。

### Q: 如何不用 Python 直接运行？

目录下已有 `brain_simulater.exe`，双击即可运行。`common_config.py` 仍可放在同目录修改配置。

---

## 附录: BrainClient API 接口

| 方法 | HTTP | 说明 |
|------|------|------|
| `authenticate(email, password)` | POST /authentication | 登录认证 |
| `is_authenticated()` | GET /authentication | 检查登录状态 |
| `create_simulation(expression, settings)` | POST /simulations | 创建模拟 |
| `get_self_correlation(alpha_id)` | GET /alphas/{id}/correlations/self | 获取 Self Corr |
| `get_ppc_correlation(alpha_id)` | GET /alphas/{id}/correlations/power-pool | 获取 PPC Corr |
| `get_prod_correlation(alpha_id)` | GET /alphas/{id}/correlations/prod | 获取 Prod Corr |
| `get_tags()` | GET /tags | 获取标签列表 |
| `add_alpha_to_tag(tag_id, name, alpha_id)` | PATCH /tags/{id} | 添加 Alpha 到列表 |
| `create_tag(name, alpha_id)` | POST /tags | 创建新列表 |
| `get_today_simulated_count()` | GET /users/self/activities/simulations | 获取今日模拟次数 |
| `get_user_id()` | GET /users/self | 获取用户 ID |
| `_get_pnl(alpha_id)` | GET /alphas/{id}/recordsets/pnl | 获取 PnL 数据 |
| `_get_yearly_stats(alpha_id)` | GET /alphas/{id}/recordsets/yearly-stats | 获取年度统计 |
| `get_platform_options()` | OPTIONS /simulations | 获取平台设置选项 |
