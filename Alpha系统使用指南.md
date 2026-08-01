# Alpha生成与提交系统使用指南

## 📋 系统概述

新架构将Alpha工作流分为两个独立阶段：

1. **生成Alpha.py** - 离线生成（无需登录WorldQuant）
2. **提交Alpha.py** - 在线提交（需要登录）

---

## ⚙️ 环境准备（首次运行必需）

### 步骤1：安装LLM依赖

```powershell
pip install -r requirements-llm.txt
```

或手动安装：
```powershell
pip install python-dotenv httpx sentence-transformers
```

### 步骤2：配置环境变量

确保 `.env` 文件包含：
```
DEEPSEEK_API_KEY=your_api_key_here
```

### 步骤3：验证数据库

确保 `research_memory.sqlite` 存在且包含：
- 研究主题（research_topics）
- 假设（hypotheses）
- 数据映射（data_mappings）

---

## 📥 阶段1：生成Alpha（离线）

### 基本用法

```powershell
# 生成100个候选（默认）
python 生成Alpha.py

# 自定义数量
python 生成Alpha.py --limit 50

# 详细日志
python 生成Alpha.py --limit 20 --verbose

# 自定义输出文件
python 生成Alpha.py --output 我的列表.csv
```

### 输出文件

**文件名**：`待提交Alpha列表.csv`

**格式**：
```
候选ID,主题ID,假设ID,研究家族,策略家族,变异类型,机制,数据集,表达式,精确哈希,参数骨架,字段骨架,生成时间
```

### 预期运行时间

- 首次运行：5-10分钟（下载embedding模型）
- 后续运行：1-3分钟（生成100个候选）

### 常见问题

**Q: 提示"python-dotenv is required"**
```powershell
pip install python-dotenv
```

**Q: 提示"DEEPSEEK_API_KEY is required"**
- 检查 `.env` 文件是否存在
- 确认API key已配置

**Q: 生成0个候选**
- 检查数据库是否有活跃的研究主题
- 查看日志中的 `deferred_reason`

---

## 📤 阶段2：提交Alpha（在线）

### 前置条件

1. ✅ 已运行 `生成Alpha.py`
2. ✅ 存在 `待提交Alpha列表.csv`
3. ✅ 已完成WorldQuant扫脸登录
4. ✅ 认证文件 `.wq_auth_state.json` 存在

### 基本用法

```powershell
# 1. 模拟运行（测试，不实际提交）
python 提交Alpha.py --dry-run

# 2. 仅simulate，不submit
python 提交Alpha.py --simulate-only

# 3. 完整流程（simulate + submit）
python 提交Alpha.py --batch-size 10

# 4. 使用自定义输入文件
python 提交Alpha.py --input 我的列表.csv
```

### 智能提交逻辑

- ✅ 所有alpha都会**simulate**
- ✅ 只有**生成了有效description**的alpha才会**submit**
- ✅ description由DeepSeek LLM自动生成
- ✅ 无效或过短的description会跳过submit

### 批次处理

- 默认每批10个
- 批次间延迟5秒（避免触发rate limit）
- 失败的alpha不会中断流程

---

## 🔄 完整工作流示例

```powershell
# === 第1步：生成 ===
python 生成Alpha.py --limit 50 --verbose

# 输出：
# [生成Alpha] ✓ 生成完成: 50 个候选
# [生成Alpha] ✓ 已写入: 待提交Alpha列表.csv

# === 第2步：检查生成结果 ===
# 打开 待提交Alpha列表.csv 查看

# === 第3步：完成扫脸登录（手动操作）===
# 确保 .wq_auth_state.json 存在

# === 第4步：测试提交 ===
python 提交Alpha.py --dry-run

# === 第5步：正式提交 ===
python 提交Alpha.py --batch-size 10
```

---

## 📊 监控与调试

### 生成阶段日志

```
[生成Alpha] LLM生成服务已初始化（DeepSeek + ExpressionGenerator）
[生成Alpha] ✓ 生成完成: 100 个候选
[生成Alpha]   - 已选主题: 5
[生成Alpha]   - 策略家族: ('momentum', 'reversal', 'fundamental')
[生成Alpha]   - 拒绝统计: {'duplicate_exact': 12, 'duplicate_skeleton': 8}
```

### 提交阶段日志

```
[提交Alpha] === 批次 1/5 (10 个候选) ===
[提交Alpha] [1.1] candidate_xxx...
[提交Alpha]   - ✓ simulate完成: alpha_id=abc123, sharpe=1.234
[提交Alpha]   - ✓ description生成: 156 字符
[提交Alpha]   - ✓ 已submit（带description）
```

---

## ⚠️ 注意事项

1. **生成Alpha.py 可以离线运行**，不需要WorldQuant登录
2. **提交Alpha.py 需要在线**，且必须先扫脸登录
3. **首次运行会下载embedding模型**（~500MB），需要网络和时间
4. **DeepSeek API需要付费**，请确保有足够额度
5. **CSV文件是中间文件**，可以手动编辑后再提交

---

## 🐛 故障排查

### 生成失败

**症状**：`[生成Alpha] ✗ 生成失败`

**检查**：
1. `pip list | grep -E "dotenv|httpx|sentence"`
2. `.env` 文件是否存在
3. `research_memory.sqlite` 是否有数据
4. DeepSeek API额度是否充足

### 提交失败

**症状**：`[提交Alpha] ✗ simulate失败`

**检查**：
1. `.wq_auth_state.json` 是否存在
2. 是否需要重新扫脸登录
3. 网络连接是否正常
4. WorldQuant平台是否正常

---

## 📞 技术支持

如遇问题，请提供：
1. 完整的错误日志
2. 运行的命令
3. Python版本：`python --version`
4. 依赖版本：`pip list | grep -E "dotenv|httpx|sentence"`

---

**最后更新**：2026-08-01
**版本**：v1.0
