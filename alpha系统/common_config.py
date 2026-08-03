SYSTEM_LANGUAGE = 'English'

use_local_corr = True

# When True (requires use_local_corr=True), exclude peers submitted within
# the last 30 days from the self/ppc pool, mirroring the platform's batch-
# refresh lag.  Newly-submitted alphas don't appear in other alphas' self-corr
# snapshots until the platform recomputes them.
strict_platform_parity = True

# Decay search grid: coarse first, then fine around the best
COARSE_DECAYS = [0, 5, 10, 15, 21, 42, 63, 126, 252, 512]

# Default numeric search grid for <?>
DEFAULT_VALUES = [2, 5, 10, 15, 21, 42, 63, 126, 252]

DEFAULT_TRUNCS = [0.001, 0.005, 0.01, 0.03, 0.05, 0.1]


def default_get_tune_score(alpha_data):
    IS = alpha_data.get("is", {})
    fitness = abs(IS.get("fitness", 0))
    margin = abs(IS.get("margin", 0))

    score = fitness * 10 + margin 

    checks = IS['checks']
    for check_info in checks:
        check_name = check_info['name']
        check_result = check_info['result']

        if check_name == 'CONCENTRATED_WEIGHT':
            if check_result == 'FAIL':
                score -= 1000
        elif check_name == 'LOW_SUB_UNIVERSE_SHARPE':
            if check_result == 'FAIL':
                score -= 1000

    return score


settings_size_cfg = {
    'Region' : 3,
    'Universe' : 5,
    'Delay' : 2,
    'Decay' : 5,
    'Neutral' : 5,
    'Truncation' : 5,
    'Pasteur' : 2,
    'NaN' : 2,
    'Max Trade' : 2,
    'Max Pos' : 2,
    'Language' : 4,
    'Lookback' : 2,
}





WHITE_LIST = [
    'subindustry',
    'industry',
    'sector',
    'market',
    'country',
    'exchange',
    'currency',
]


REGION_ARR = ['USA', 'GLB', 'EUR', 'ASI', 'CHN', 'JPN', 'AMR', 'IND', 'MEA', 'TWN', 'HKG', 'KOR']


MAX_TRADE_ARR = ['ON', 'OFF']
NAN_HANDLING_ARR = ['ON', 'OFF']
DELAY_DICT = {
    'USA': [1, 0],
    'GLB': [1],
    'EUR': [1, 0],
    'ASI': [1],
    'CHN': [1, 0],
    'JPN': [1, 0],
    'AMR': [1, 0],
    'IND': [1],
    'MEA': [1],
    'TWN': [1],
    'HKG': [1],
    'KOR': [1],
}


UNIVERSE_DICT = {
    'USA': ['TOP3000', 'TOP2000', 'TOP1000', 'TOP500', 'TOP200', 'TOPSP500', 'ILLIQUID_MINVOL1M'],
    'GLB': ['TOP3000', 'MINVOL1M', 'MINVOL10M', 'TOPDIV3000'],
    'EUR': ['TOP2500', 'TOP1200', 'TOP800', 'TOP400', 'ILLIQUID_MINVOL1M', 'TOPCS1600'],
    'ASI': ['MINVOL1M', 'MINVOL10M', 'ILLIQUID_MINVOL1M', 'TOP500'],
    'CHN': ['TOP2000U'],
    'JPN': ['TOP1600', 'TOP1200'],
    'AMR': ['TOP600'],
    'KOR': ['TOP600'],
    'HKG': ['TOP800', 'TOP500'],
    'IND': ['TOP500'],
    'MEA': ['TOP400', 'TOP300'],
    'TWN': ['TOP500', 'TOP100'],
}


BASE_NEUTRALIZATION_ARR = [
    "SUBINDUSTRY", 
    "INDUSTRY",
    "SECTOR",
    "MARKET",
    "NONE",
]


COMMON_NEUTRALIZATION_ARR = [
    "REVERSION_AND_MOMENTUM",
    "CROWDING",
    "FAST",
    "SLOW",
    "SLOW_AND_FAST",
    "STATISTICAL",
]
COMMON_NEUTRALIZATION_ARR += BASE_NEUTRALIZATION_ARR


NEUTRALIZATION_DICT = {
    'USA': COMMON_NEUTRALIZATION_ARR,
    'CHN': COMMON_NEUTRALIZATION_ARR,
    'GLB': COMMON_NEUTRALIZATION_ARR + ['COUNTRY'],
    'EUR': COMMON_NEUTRALIZATION_ARR + ['COUNTRY'],
    'ASI': COMMON_NEUTRALIZATION_ARR + ['COUNTRY'],
    'JPN': COMMON_NEUTRALIZATION_ARR,
    'AMR': BASE_NEUTRALIZATION_ARR + ['COUNTRY', 'STATISTICAL'],
    'MEA': BASE_NEUTRALIZATION_ARR + ['COUNTRY'],
    'IND': COMMON_NEUTRALIZATION_ARR,
    'TWN': COMMON_NEUTRALIZATION_ARR,
    'HKG': COMMON_NEUTRALIZATION_ARR,
    'KOR': COMMON_NEUTRALIZATION_ARR,
}


PC_THRESHOLD = 0.7
PPC_THRESHOLD = 0.5
MAX_ZERO_LEN = 6
MIN_SIMULATE_LEN = 499
GLB_SUB_REGION_SHARPE_THRESHOLD = 1
ASI_RU_RATIO = 0.9
ASI_JPN_SHARPE_LIMIT = 1
CHN_RU_RATIO = 0.4
IND_RU_LIMIT = 1




# ──────────────────────────────────────────────
#  i18n Translation
# ──────────────────────────────────────────────
TRANSLATIONS = {
    # ── Main Window ──
    "BRAIN Alpha Simulater": "BRAIN Alpha 回测器",
    "Authentication": "认证",
    "Email:": "邮箱:",
    "Password:": "密码:",
    "Login": "登录",
    "Not logged in": "未登录",
    "Logging in...": "登录中...",
    "User ID: ": "用户ID: ",
    "User ID: -": "用户ID: -",
    "Today Simulated: ": "今日回测: ",
    "Today Simulated: -": "今日回测: -",
    "Today Simulated: ?": "今日回测: ?",
    "Speed: ": "速度: ",
    "Speed: -": "速度: -",
    "Signals: ": "信号: ",
    "Signals: -": "信号: -",
    "Signals: ?": "信号: ?",
    "Pyramids: ": "金字塔: ",
    "Pyramids: -": "金字塔: -",
    "Pyramids: ?": "金字塔: ?",
    "Fetch Alpha": "获取Alpha",
    "Funcs": "功能",
    "Ready": "就绪",
    "Show": "显示",
    "Quit": "退出",
    "Minimized to tray. Click to restore.": "已最小化到托盘，点击恢复。",
    "Authenticated successfully": "认证成功",
    "Biometric required": "需要生物认证",
    "Login failed": "登录失败",
    "Logged in ": "已登录 ",

    # ── Funcs Menu ──
    "Download Operators": "下载算子",
    "Used Operators": "已用算子",
    "Unused Operators": "未用算子",
    "Used Datafields": "已用数据字段",
    "Download Submitted Alphas": "下载已提交Alpha",
    "Archive PPA Tags": "归档PPA标签",
    "Empty PPA Tags": "清空PPA标签",
    "Restore PPA Tags": "恢复PPA标签",
    "Archive Osmosis": "归档Osmosis",
    "Empty Osmosis": "清空Osmosis",
    "Restore Osmosis": "恢复Osmosis",
    "Copy All AIDs": "复制所有AID",

    # ── Tab Corner Buttons ──
    "Show all tabs": "显示所有标签页",
    "Move all running tabs to the right": "将运行中标签页移到右侧",
    "Close all IDLE tabs": "关闭所有空闲标签页",

    # ── Status Bar ──
    "Tabs: ": "标签: ",
    "Unviewed: ": "未查看: ",
    "Queued: ": "队列中: ",
    "Running: ": "运行中: ",
    "Max Weight:": "最大权重:",
    "Cancel All": "全部取消",
    "Check: ": "检查: ",
    "No IDLE tabs to close": "无空闲标签页可关闭",
    "No alpha IDs in view mode": "视图模式中无Alpha ID",
    "Closed ": "已关闭 ",
    " IDLE tab(s)": " 个空闲标签页",

    # ── SimulateTab ──
    "Alpha Expression": "Alpha表达式",
    "Simulation Settings": "回测设置",
    "Copy": "复制",
    "Import": "导入",
    "Copy All": "全部复制",
    "Import All": "全部导入",
    "Copied!": "已复制!",
    "Imported!": "已导入!",
    "Simulate": "回测",
    "Fill": "填充",
    "Tune": "调参",
    "Edit Score": "编辑评分",
    "Cancel": "取消",
    "Dequeue": "出队",
    "Clone": "克隆",
    "Submit": "提交",
    "Submitting...": "提交中...",

    # ── Settings Labels ──
    "Region": "区域",
    "Universe": "股票池",
    "Delay": "延迟",
    "Decay": "衰减",
    "Neutral": "中性化",
    "Truncation": "截断",
    "Pasteur": "巴氏",
    "NaN": "NaN",
    "Max Trade": "最大交易",
    "Max Pos": "最大持仓",
    "Language": "语言",
    "Lookback": "回溯",

    # ── Metrics ──
    "Performance Metrics": "绩效指标",
    "Yearly Statistics": "年度统计",
    "Sharpe": "夏普",
    "Turnover": "换手率",
    "Fitness": "适应度",
    "Returns": "收益率",
    "Drawdown": "回撤",
    "Margin": "边际",
    "Metric": "指标",
    "Value": "值",
    "Year": "年份",
    "Stats": "统计",
    "Risk Neutralized": "风险中性化",
    "Investability Constrained": "可投资性约束",
    "AMER": "美洲",
    "APAC": "亚太",
    "EMEA": "欧非中东",

    # ── Correlation ──
    "All Corr": "全部相关",
    "Self Corr": "自相关",
    "Self Pool": "自相关池",
    "PPC": "PPC",
    "PPC Pool": "PPC池",
    "Prod Corr": "生产相关",
    "Cached PC": "缓存PC",
    "PC Range": "PC范围",
    "Inter Corr": "互相关",
    "Corr": "相关",

    # ── Checks ──
    "PASS": "通过",
    "WARNING": "警告",
    "FAIL": "失败",
    "PENDING": "待定",

    # ── Properties ──
    "Name": "名称",
    "Tags": "标签",
    "Desc": "描述",
    "Color": "颜色",
    "None": "无",
    "AI Write Desc": "AI写描述",
    "Writing...": "写入中...",
    "Update": "更新",
    "Properties": "属性",
    "Classifications": "分类",

    # ── Status Messages ──
    "Queued — waiting for slot...": "排队中 — 等待槽位...",
    "Starting simulation...": "开始回测...",
    "Auto-tuning decay...": "自动调参衰减...",
    "Auto-tuning expression...": "自动调参表达式...",
    "Auto-tuning ": "自动调参 ",
    "Cancelled (queued)": "已取消(排队中)",
    "Cancelled": "已取消",
    "Refetching...": "重新获取...",
    "Description generated — click Update to save": "描述已生成 — 点击更新保存",
    "Simulation failed": "回测失败",
    "Fetching alpha ": "获取Alpha ",
    "Fetched Alpha: ": "已获取Alpha: ",
    "Refetched Alpha: ": "重新获取Alpha: ",
    "Refetch error: ": "重新获取错误: ",
    "Sim: ": "回测: ",
    "Error: ": "错误: ",

    # ── QMessageBox Titles ──
    "Input Error": "输入错误",
    "Not Authenticated": "未认证",
    "Tune Error": "调参错误",
    "Tune Required": "需要调参",
    "No Alpha": "无Alpha",
    "Corr Error": "相关错误",
    "AI Write Desc Error": "AI写描述错误",
    "Error": "错误",
    "Code Error": "代码错误",
    "Format Error": "格式错误",
    "Tab Busy": "标签页忙碌",
    "Login Failed": "登录失败",
    "Biometric Auth": "生物认证",
    "Simulation Error": "回测错误",
    "No Options": "无选项",
    "No Data": "无数据",
    "Copied": "已复制",
    "Success": "成功",
    "Fetch Error": "获取错误",
    "Confirm Restore PPA Tags": "确认恢复PPA标签",
    "Confirm Empty PPA Tags": "确认清空PPA标签",
    "Confirm Empty Osmosis": "确认清空Osmosis",
    "Confirm Restore Osmosis": "确认恢复Osmosis",
    "Close IDLE Tabs": "关闭空闲标签页",

    # ── QMessageBox Messages ──
    "Please enter an alpha expression.": "请输入Alpha表达式。",
    "Please login first.": "请先登录。",
    "Run a simulation first to get an Alpha ID.": "请先运行回测获取Alpha ID。",
    "Clipboard is empty.": "剪贴板为空。",
    "Clipboard does not contain valid JSON.": "剪贴板不包含有效JSON。",
    "Please enter email and password.": "请输入邮箱和密码。",
    "No alpha ID available.": "无可用Alpha ID。",
    "Please enter an Alpha ID.": "请输入Alpha ID。",
    "Please enter an Alpha ID for Inter Corr.": "请输入交叉相关的Alpha ID。",
    "No PnL data available.": "无PnL数据。",
    "No Risk Neutralized PnL data.": "无风险中性化PnL数据。",
    "No Investability Constrained PnL data.": "无可投资性约束PnL数据。",
    "No {section_key} PnL data.": "无{section_key} PnL数据。",
    "Run a simulation first to get PnL data.": "请先运行回测获取PnL数据。",
    "Properties updated.": "属性已更新。",
    "Cannot close a tab while simulation is running.": "回测运行中无法关闭标签页。",
    "Cannot determine region for PC Range": "无法确定PC范围区域",
    "No PnL data available for PC Range estimation": "无PnL数据用于PC范围估计",
    "PnL data too short for PC Range estimation": "PnL数据太短无法估计PC范围",
    "Not available": "不可用",
    "Invalid format": "格式无效",
    "No valid universes to tune.": "无有效股票池可调参。",
    "No valid neutralizations to tune.": "无有效中性化方式可调参。",
    "Code must define a get_tune_score function.": "代码必须定义get_tune_score函数。",
    "get_tune_score must return a number.": "get_tune_score必须返回数字。",
    "Biometric authentication required.\nPlease complete verification in the browser window,\nthen click Login again.": "需要生物认证。\n请在浏览器窗口完成验证，\n然后再次点击登录。",

    # ── Tab Context Menu ──
    "Close Current Tab": "关闭当前标签页",
    "Reload": "重新加载",
    "Show Reverse": "显示反转",
    "Move to Leftmost": "移到最左侧",
    "Cancel Other Simulations": "取消其他回测",

    # ── ListDialog ──
    "Add to List": "添加到列表",
    "List:": "列表:",
    "Alpha: ": "Alpha: ",
    "Select existing list or type new name to create": "选择已有列表或输入新名称创建",
    "Loading lists...": "加载列表...",
    "Loaded ": "已加载 ",
    " lists": " 个列表",
    "Adding to ": "添加到 ",
    "Adding to list ": "添加到列表 ",
    "Add": "添加",
    "Already in list ": "已在列表中 ",
    "Added to list ": "已添加到列表 ",
    "Created list ": "已创建列表 ",
    " and added alpha": " 并添加了Alpha",
    "List ": "列表 ",
    " has no ID": " 无ID",
    "Created list but got no ID back": "已创建列表但未获取到ID",

    # ── Full Screen ──
    "Alpha Expression - Full Screen": "Alpha表达式 - 全屏",

    # ── Edit Score Dialog ──
    "Edit get_tune_score": "编辑get_tune_score",
    "Apply": "应用",
    "Reset to Default": "恢复默认",

    # ── Placeholders ──
    "comma separated": "逗号分隔",

    # ── Misc Buttons/Labels ──
    "Expand": "展开",
    "Auto Fill": "自动填充",
    "Show Count": "显示计数",
    "Hide Count": "隐藏计数",
    "OK": "确定",
    "Restore": "恢复",
    "Restore (Esc)": "恢复(Esc)",
    "Copy PnL": "复制PnL",
    "Archive file:": "归档文件:",
    "Region/delay filter (empty = restore all):": "区域/延迟过滤(空=恢复全部):",
    "No operators loaded. Download Operators first.": "未加载算子，请先下载算子。",
    "No alphas_db.json found. Run Download first.": "未找到alphas_db.json，请先下载。",
    "Download failed or no alphas found": "下载失败或未找到Alpha",
    "Downloading submitted alphas...": "正在下载已提交Alpha...",
    "Failed to read alphas_db.json: ": "读取alphas_db.json失败: ",
    "Failed to read CSV: ": "读取CSV失败: ",

    # ── PC Range ──
    "min: ": "最小: ",
    # "min: --": "最小: --",
    "correlated alphas": "个相关Alpha",
    "known value, no correlated estimates": "已知值，无相关估计",
    "PC Range ": "PC范围 ",

    # ── Submit Worker ──
    "Rate limited, waiting 60s...": "速率受限，等待60秒...",
    "Server error, retrying in 5s...": "服务器错误，5秒后重试...",
    "Already submitted": "已提交",
    "Submission limit exceeded": "提交次数超限",
    "Submit success!": "提交成功!",
    "Done": "完成",
    "Max retries exceeded": "超过最大重试次数",
    "Submit failed: ": "提交失败: ",
    "Unexpected status: ": "意外状态: ",
    "Waiting ": "等待 ",
    "Error: ": "错误: ",

    # ── Archive/Restore ──
    "Emptying PPA tags ": "清空PPA标签 ",
    "Restoring PPA tags ": "恢复PPA标签 ",
    "Emptying osmosis ": "清空Osmosis ",
    "Restoring osmosis ": "恢复Osmosis ",
    " for ": " 用于 ",
    " alphas": " 个Alpha",
    " pairs": " 对",
    "No PPA alphas found for ": "未找到PPA Alpha用于 ",
    "No PPA records found for ": "未找到PPA记录用于 ",
    "No osmosis alphas found for ": "未找到Osmosis Alpha用于 ",
    "No osmosis records found for ": "未找到Osmosis记录用于 ",
    " in ": " 在 ",
    "Will restore ": "将恢复 ",
    "Remove ": "移除 ",
    " alpha-tag pairs": " 个Alpha-标签对",
    " from PowerPoolSelected lists?\nThis cannot be undone.": " 从PowerPoolSelected列表?\n此操作不可撤销。",
    "Clear osmosisPoints for ": "清空Osmosis积分用于 ",
    "?\nThis cannot be undone.": "?\n此操作不可撤销。",
    "Close ": "关闭 ",
    " IDLE tab(s)?": " 个空闲标签页?",
    "Restore ": "恢复 ",
    " PPA tag pairs ": " 个PPA标签对 ",
    " osmosisPoints for ": " 个Osmosis积分用于 ",
    " alphas?": " 个Alpha?",
    "Signals ": "信号 ",
    ", syncing alphas...": "，同步Alpha...",

    # ── Operators/Datafields Dialog ──
    "Used Operators — ": "已用算子 — ",
    "Unused Operators — ": "未用算子 — ",
    "Used Datafields — ": "已用数据字段 — ",
    "Operators per Alpha: ": "每Alpha算子数: ",
    "Operators used: ": "已用算子数: ",
    "Fields per Alpha: ": "每Alpha字段数: ",
    "Fields used: ": "已用字段数: ",
    " alphas, ": " 个Alpha, ",
    " operators": " 个算子",
    " unused": " 个未使用",
    " datafields": " 个数据字段",

    # ── Tooltips ──
    "Copy expression to clipboard": "复制表达式到剪贴板",
    "Import expression from clipboard": "从剪贴板导入表达式",
    "Full screen": "全屏",
    "Edit in VSCode": "在VSCode中编辑",
    "Copy current settings to clipboard": "复制当前设置到剪贴板",
    "Import settings from clipboard": "从剪贴板导入设置",
    "Toggle edit mode to type custom values (e.g. ?1 for sequential tune)": "切换编辑模式输入自定义值(如 ?1 用于顺序调参)",
    "Traverse all universes for this region": "遍历此区域所有股票池",
    "Toggle edit mode to type custom values": "切换编辑模式输入自定义值",
    "Traverse all neutralizations for this region": "遍历此区域所有中性化方式",
    "Fill to N slots (GLB=2 slots/tab, others=1 slot/tab)": "填充至N个槽位(GLB=2槽/标签, 其他=1槽/标签)",
    "Auto-tune: use ?/?N in settings or <?N>/<?gN>/<prefix?gN> in expression. ?N = sequential; ? = cartesian. <?g> = glossary; <sector?g> = glossary with default.": "自动调参: 设置中用?/?N，表达式中用<?N>/<?gN>/<prefix?gN>。?N=顺序; ?=笛卡尔积。<?g>=词汇调参; <sector?g>=带默认值的词汇调参。",
    "Customize get_tune_score function": "自定义get_tune_score函数",
    "When checked, tune will open all variants in separate tabs, keep them open, and move the best result to the leftmost tab": "勾选后，调参将在单独标签页中打开所有变体，保持打开，并将最佳结果移到最左侧标签页",
    "Auto-fill running simulations to 8 when progress < 50%": "进度<50%时自动填充运行中回测至8个",
    "Duplicate this tab with current expression & settings": "复制此标签页(含当前表达式和设置)",
    "Copy expression + settings to clipboard": "复制表达式+设置到剪贴板",
    "Import expression + settings from clipboard": "从剪贴板导入表达式+设置",
    "Open simulation URL": "打开回测URL",
    "Copy error message": "复制错误信息",
    "Pin Key Metrics to desktop": "固定关键指标到桌面",
    "Open in browser": "在浏览器中打开",
    "Show Self Corr & PPC": "显示自相关和PPC",
    "Show Self Corr & PPC for ": "显示自相关和PPC用于 ",
    "Copy PnL data to clipboard": "复制PnL数据到剪贴板",
    "Auto-generate description using /write_desc": "使用/write_desc自动生成描述",
    "Submit alpha for production": "提交Alpha到生产环境",
    "Refresh today's simulation count": "刷新今日回测数",
    "Refresh this quarter's submission count": "刷新本季度提交数",
    "Refresh this quarter's completed pyramids count": "刷新本季度完成金字塔数",
    "Maximum running weight (GLB=2, others=1)": "最大运行权重(GLB=2, 其他=1)",
    "Traverse decay values from input (e.g. 1,2,3) or default [0,10,15,21,42,63,126,252,512]": "遍历输入的衰减值(如1,2,3)或默认[0,10,15,21,42,63,126,252,512]",
    "Traverse truncation values from input (e.g. 0.01,0.05,0.1) or default [0.001,0.005,0.01,0.03,0.05,0.1]": "遍历输入的截断值(如0.01,0.05,0.1)或默认[0.001,0.005,0.01,0.03,0.05,0.1]",
    "Decay value. Single number (e.g. 10), comma-separated (e.g. 1,2,3) for traversal, ? for auto-tune, ?1 for sequential tune, 10?1 to fix at 10 before tune group 1.": "衰减值。单个数字(如10)，逗号分隔(如1,2,3)用于遍历，?用于自动调参，?1用于顺序调参，10?1用于在调参组1前固定为10。",
    "Truncation value. Single number (e.g. 0.08), comma-separated (e.g. 0.01,0.05,0.1) for traversal, ? for auto-tune, ?2 for sequential tune, 0.08?2 to fix at 0.08 before tune group 2.": "截断值。单个数字(如0.08)，逗号分隔(如0.01,0.05,0.1)用于遍历，?用于自动调参，?2用于顺序调参，0.08?2用于在调参组2前固定为0.08。",

    # ── Metrics Table ──
    "Alpha ID": "Alpha ID",
    "Status": "状态",
    "Date Created": "创建日期",
    "Long Count": "多头数量",
    "Short Count": "空头数量",
    "Beta": "Beta",
    "Total Orders": "总订单数",
    "Weight Correlation": "权重相关性",
    "Operator Count": "算子数量",
    "IS PnL": "IS PnL",
    "-- IS Metrics --": "-- IS指标 --",
    "-- OS Metrics --": "-- OS指标 --",
    "-- AMER Metrics --": "-- 美洲指标 --",
    "-- APAC Metrics --": "-- 亚太指标 --",
    "-- EMEA Metrics --": "-- 欧非中东指标 --",
    "-- Submitted --": "-- 已提交 --",
    "Operators per Alpha": "每Alpha算子数",
    "Operators used": "已用算子数",
    "Fields per Alpha": "每Alpha字段数",
    "Fields used": "已用字段数",

    # ── Check Labels ──
    "Sharpe": "夏普",
    "Fitness": "适应度",
    "Turnover": "换手率",
    "Concentrated weight": "集中权重",
    "Sub-universe Sharpe": "子股票池夏普",
    "Reversion component": "反转成分",
    "Self correlation": "自相关",
    "Data diversity": "数据多样性",
    "Prod correlation": "生产相关",
    "Regular submission": "常规提交",
    "Alpha submissions quota": "Alpha提交配额",
    "Power Pool correlation": "PowerPool相关性",
    "Weight concentration": "权重集中度",
    "After Cost High Turnover": "成本后高换手率",
    "Orthogonal High Turnover": "正交高换手率",
    "Pyramid theme": "金字塔主题",
    "Daily Osmosis Rank": "每日Osmosis排名",
    "Data overuse": "数据过度使用",
    "Production correlation": "生产相关性",
    "Self-correlation": "自相关",

    # ── Pinned Window ──
    "Key Metrics": "关键指标",

    # ── PnL Chart ──
    "PnL Curve": "PnL曲线",
    "Cumulative PnL": "累计PnL",
    "IS PnL": "IS PnL",
    "OS PnL": "OS PnL",
    "PnL": "PnL",
    "Risk Neutralized PnL": "风险中性化PnL",
    "Investability Constrained PnL": "可投资性约束PnL",
    " PnL": " PnL",

    # ── Misc ──
    "All ": "所有 ",
    " already simulated": " 已回测",
    "Not available (retry ": "不可用(重试 ",
    "Reversed Alpha: ": "反转Alpha: ",
    " of ": " 的 ",
    " is ": " 是 ",
    " cutoff of ": " 截止值 ",
    ".": "。",
    "...": "...",
    "Delay must be a number, e.g. USA/D0": "延迟必须是数字，如USA/D0",
    "Use format: REGION or REGION/Dx  (e.g. USA or USA/D0)": "使用格式: REGION 或 REGION/Dx (如 USA 或 USA/D0)",

    # ── Additional MainWindow translations ──
    "Today Simulations": "今日回测",
    "Count unchanged: ": "计数未变: ",
    "Signals": "信号",
    "Pyramids": "金字塔",
    "Download done: ": "下载完成: ",
    " total, ": " 总计, ",
    " new, ": " 新增, ",
    " failed": " 失败",
    "Failed to download operators: ": "下载算子失败: ",
    "Downloaded ": "已下载 ",
    " operators to operators.json": " 个算子到operators.json",
    "No PowerPoolSelected alphas found.": "未找到PowerPoolSelected Alpha。",
    "No osmosis alphas found.": "未找到Osmosis Alpha。",
    "Remove alphas from PowerPoolSelected lists.\nLeave empty for ALL, or specify region/delay (e.g. USA or USA/D0):": "从PowerPoolSelected列表移除Alpha。\n留空表示全部，或指定区域/延迟(如USA或USA/D0):",
    "Leave empty to clear ALL, or specify region/delay (e.g. USA or USA/D0):": "留空清空全部，或指定区域/延迟(如USA或USA/D0):",
    "PPA tags cleared: ": "PPA标签已清空: ",
    " success, ": " 成功, ",
    "PPA tags restored: ": "PPA标签已恢复: ",
    "Osmosis cleared: ": "Osmosis已清空: ",
    "Osmosis restored: ": "Osmosis已恢复: ",
    "No ppa_tags_archive_*.csv found. Run Archive PPA Tags first.": "未找到ppa_tags_archive_*.csv，请先归档PPA标签。",
    "No osmosis_archive_*.csv found. Run Archive Osmosis first.": "未找到osmosis_archive_*.csv，请先归档Osmosis。",
    "Archived ": "已归档 ",
    " alphas to ": " 个Alpha到 ",
    " groups": " 个分组",
    "Loading reversed yearly stats...": "加载反转年度统计...",
    "Simulate ": "回测 ",

    # ── Additional entries discovered after agent edits ──
    " PnL data.": " PnL数据。",
    " alpha ID(s)": " 个Alpha ID",
    " alpha-tag pairs ": " 个Alpha-标签对 ",
    " alphas ": " 个Alpha ",
    " from ": " 来自 ",
    " is set to ": " 设置为 ",
    " — ": " — ",
    "! ": "! ",
    ". Please click Tune to auto-select the best value.": "。请点击调参以自动选择最佳值。",
    "2 year Sharpe": "2年夏普",
    "Classification High Turnover": "分类高换手率",
    "Copied ": "已复制 ",
    "Copy Alpha ID": "复制Alpha ID",
    "Expression contains tune placeholder. Please click Tune to auto-select the best value.": "表达式包含调参占位符。请点击调参以自动选择最佳值。",
    "Failed to update properties": "更新属性失败",
    "High Turnover: After cost Sharpe": "高换手: 扣费后夏普",
    "High Turnover: High Turnover returns ratio": "高换手: 高换手收益率比",
    "High Turnover: Orthogonal RAM neutralization": "高换手: 正交RAM中性化",
    "High Turnover: Pnl realization": "高换手: PnL实现",
    "High Turnover: Turnover": "高换手: 换手率",
    "Investable High Turnover: Max Position Sharpe": "可投资高换手: 最大仓位夏普",
    "Investable High Turnover: Max Position turnover": "可投资高换手: 最大仓位换手率",
    "Investable High Turnover: Max Trade Sharpe": "可投资高换手: 最大交易夏普",
    "Investable High Turnover: Max Trade turnover": "可投资高换手: 最大交易换手率",
    "Liquid High Turnover: TOP200 Sharpe": "流动性高换手: TOP200夏普",
    "Liquid High Turnover: TOP500 and TOP200 Sharpe ratio": "流动性高换手: TOP500与TOP200夏普",
    "Metrics": "指标",
    "No ": "无 ",
    "Osmosis allocation": "Osmosis分配",
    "Power pool correlation": "PowerPool相关性",
    "Restore osmosisPoints for ": "恢复Osmosis积分用于 ",
    "Theme": "主题",
    "\nThis cannot be undone.": "\n此操作不可撤销。",
    "── IS Metrics ──": "── IS指标 ──",
    "── OS Metrics ──": "── OS指标 ──",
    "── Submitted ──": "── 已提交 ──",
    "USA  or  USA/D0  or  (empty for all)": "USA  或  USA/D0  或  (空=全部)",

    # ── Additional SimulateTab Strings ──
    "Cannot Fill": "无法填充",
    "Current region is GLB (2 slots/tab). Adding another GLB would reach ": "当前区域为GLB(2槽/标签)。添加另一个GLB将达到 ",
    " slots, exceeding target of ": " 个槽，超过目标 ",
    "Define a get_tune_score(alpha_data) → float function.\n": "定义get_tune_score(alpha_data) → float函数。\n",
    "alpha_data contains 'is' dict with keys like fitness, sharpe, turnover, etc.\n": "alpha_data包含'is'字典，键如fitness, sharpe, turnover等。\n",
    "Leave as default to reset.": "保留默认值以重置。",
    "Add alpha to list ": "添加Alpha到列表 ",
    "Fetch error: ": "获取错误: ",
    "No alpha ID to refetch": "无Alpha ID可重新获取",
    "Failed to copy PnL data": "复制PnL数据失败",
    "raw PnL data": "原始PnL数据",
    "records": "条记录",
    "to clipboard": "到剪贴板",
    "Failed to update properties": "更新属性失败",
    " is set to ": " 被设置为 ",
    ". Please click Tune to auto-select the best value.": "。请点击调参自动选择最佳值。",
    "Expression contains tune placeholder. Please click Tune to auto-select the best value.": "表达式包含调参占位符。请点击调参自动选择最佳值。",
    "No ": "无 ",
    " PnL data.": " PnL数据。",
    "No yearly data": "无年度数据",
    "! ": "! ",
    " — ": " — ",
    "High Turnover: Turnover": "高换手率: 换手率",
    "High Turnover: High Turnover returns ratio": "高换手率: 高换手收益率比",
    "High Turnover: Pnl realization": "高换手率: PnL实现",
    "Liquid High Turnover: TOP500 and TOP200 Sharpe ratio": "流动性高换手率: TOP500和TOP200夏普比",
    "Liquid High Turnover: TOP200 Sharpe": "流动性高换手率: TOP200夏普",
    "High Turnover: After cost Sharpe": "高换手率: 成本后夏普",
    "Investable High Turnover: Max Trade Sharpe": "可投资高换手率: 最大交易夏普",
    "Investable High Turnover: Max Trade turnover": "可投资高换手率: 最大交易换手率",
    "Investable High Turnover: Max Position Sharpe": "可投资高换手率: 最大持仓夏普",
    "Investable High Turnover: Max Position turnover": "可投资高换手率: 最大持仓换手率",
    "High Turnover: Orthogonal RAM neutralization": "高换手率: 正交RAM中性化",
    "2 year Sharpe": "2年夏普",
    "Classification High Turnover": "分类高换手率",
    "Power pool correlation": "PowerPool相关性",
    "Osmosis allocation": "Osmosis分配",
    "Self-correlation": "自相关",
    "Data overuse": "数据过度使用",
    "Production correlation": "生产相关性",
    "Alpha submissions quota": "Alpha提交配额",

    # ── Settings Menu ──
    "Settings": "设置",
    "Use Local Corr": "本地相关",
    "Compute Self Corr / PPC locally from cached PnL instead of the platform API": "从缓存PnL本地计算自相关/PPC，而非平台API",
    "Corr Strict Platform Parity": "相关性严格平台一致",
    "Exclude freshly-submitted peers from local self/ppc pool to match platform snapshot": "从本地自相关/PPC池中排除刚提交的Alpha，以匹配平台快照",
    "Help": "帮助",
    "Shortcuts": "快捷键",
    "Next tab": "下一个标签页",
    "Previous tab": "上一个标签页",
    "Jump to next unviewed tab": "跳到下一个未查看标签页",
    "Jump to previous unviewed tab": "跳到上一个未查看标签页",
    "Clone current tab": "克隆当前标签页",
    "Close current tab": "关闭当前标签页",
    "Move current tab left": "左移当前标签页",
    "Move current tab right": "右移当前标签页",
    "Simulate current tab": "回测当前标签页",
    "Fill current tab": "填充当前标签页",
    "Cancel current tab": "取消当前标签页",
    "Tune current tab": "调参当前标签页",
    "Close dialog / popup": "关闭对话框/弹窗",
    "Local SC": "本地自相关",
    "Local PPC": "本地PPC",
    "Show top correlated alphas": "显示高相关Alpha",
    "Most correlated alphas": "最高相关的Alpha",
    "Least correlated alphas": "最低相关的Alpha",
    "Most correlated:": "最高相关:",
    "Least correlated:": "最低相关:",

    # ── Title Bar ──
    "Minimize": "最小化",
    "Maximize": "最大化",
    "Restore": "向下还原",

    "EST": "美东时间",
    "Check": "检查",
}
