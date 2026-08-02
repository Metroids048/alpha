# Alpha生成与提交 - 完整闭环系统使用指南

**更新时间**: 2026-08-02  
**系统版本**: v50.7 + 迭代反馈闭环

---

## 🎯 系统概览

这是一个**带反馈学习的Alpha生成与提交闭环系统**，核心特点：

1. ✅ **质量门槛**: 只生成符合WorldQuant标准的alpha（sharpe≥1.58, fitness≥1.0, turnover≤70%）
2. ✅ **迭代改进**: 自动分析失败原因，生成改进prompt
3. ✅ **链路验证**: 认证、simulate、提交全链路打通
4. ✅ **反馈学习**: 失败经验持久化，避免重复错误

---

## 📁 核心文件说明

### 生成脚本
- `生成高质量Alpha.py` - **唯一生成入口**，集成v50引擎
- `auto_alpha_pipeline_rebuilt_v50.py` - 核心引擎（不要直接运行）

### 验证与提交脚本
- `批量simulate验证.py` - 批量验证alpha质量
- `迭代提交Alpha.py` - 手动迭代提交（需人工介入）
- `自动迭代闭环.py` - **自动分析反馈并生成改进prompt**

### 数据文件
- `高质量Alpha候选.csv` - 生成的候选表达式（293个）
- `simulate_results.csv` - simulate验证结果
- `alpha_submission_feedback.csv` - **反馈数据库**（供v50学习）
- `LLM改进prompt.txt` - **自动生成的改进指导**

### 认证文件（敏感，不要提交git）
- `.wq_auth_state.json` - WorldQuant认证状态
- `.env` - API密钥等敏感配置

---

## 🚀 完整工作流程

### 阶段1: 生成候选

```bash
# 生成高质量候选（自动读取历史反馈）
python 生成高质量Alpha.py
```

**输出**: `高质量Alpha候选.csv`（约300个候选）

**v50自动做的事情**:
- ✅ 读取`alpha_submission_feedback.csv`学习失败模式
- ✅ 避免低sharpe/高turnover的表达式结构
- ✅ 优先生成经过验证的因子组合
- ✅ 自动去重、质量筛选

---

### 阶段2: 批量验证

```bash
# 验证前10个候选（测试）
python 批量simulate验证.py --limit 10

# 验证全部候选
python 批量simulate验证.py --full
```

**输出**: `simulate_results.csv`

**结果示例**:
```
候选ID,alpha_id,status,sharpe,fitness,turnover
arch_hybrid_z_liq:primary,N1b1gxxp,COMPLETE,0.20,0.06,0.1354
...
```

---

### 阶段3: 自动分析反馈

```bash
# 分析失败原因，生成改进prompt
python 自动迭代闭环.py
```

**输出**:
1. `LLM改进prompt.txt` - 发送给LLM的改进指导
2. `alpha_submission_feedback.csv` - 更新的反馈数据库

**自动分析的内容**:
- ✅ 统计失败原因分布（低sharpe/高turnover/低fitness）
- ✅ 提取失败表达式的共同特征
- ✅ 生成具体的改进策略
- ✅ 提供推荐的表达式模板

---

### 阶段4: LLM改进生成

**手动步骤**:

1. 打开`LLM改进prompt.txt`
2. 将内容复制到Claude/GPT/DeepSeek
3. LLM会返回15个改进的表达式
4. 将新表达式保存到`高质量Alpha候选.csv`（覆盖或追加）

**自动化方案**（可选）:
```bash
# 如果配置了LLM API
python 生成高质量Alpha.py --use-feedback --iterations 3
```

---

### 阶段5: 重复迭代

回到**阶段2**，验证新生成的候选，直到：
- ✅ 获得足够数量的通过alpha（例如10个）
- ✅ Sharpe ≥ 1.58, Fitness ≥ 1.0, Turnover ≤ 70%

---

## 📊 当前状态（2026-08-02）

### ✅ 已完成
- [x] 认证链路打通（浏览器扫脸登录）
- [x] Simulate API调用成功
- [x] 批量处理稳定运行
- [x] 反馈数据收集（40条记录）
- [x] 失败模式分析完成
- [x] LLM改进prompt生成

### 📈 质量分析

**第1轮验证结果（40个候选）**:
- 总计: 40
- 成功: 20 (50%)
- 失败: 20 (50%)

**主要问题**:
- 低Sharpe: 23个（平均0.25，目标≥1.58）❌
- 高Turnover: 24个（平均173%，目标≤70%）❌
- 低Fitness: 22个（平均0.08，目标≥1.0）❌

**根本原因**:
1. 初始候选来源于"proven"模板，但未经平台验证
2. 使用了`ts_delta(anl*/cap, 126)`导致高turnover
3. 信号强度不足（未组合足够强的基本面因子）

---

## 🎯 改进方向（已在LLM改进prompt.txt）

### 提高Sharpe
- 使用更强的预测信号（基本面变化、分析师创新）
- 增加信号平滑（ts_mean, ts_decay_linear）
- 组合多个弱相关信号

### 控制Turnover
- 使用更长回看窗口（126天以上）
- 避免ts_delta短窗口
- 使用ts_decay_linear代替简单rank

### 提高Fitness
- 选择高预测力因子（收益、现金流、估值）
- 确保横截面区分度
- 避免过度嵌套

---

## 🔧 故障排查

### 问题1: 认证失败（401错误）
**解决**: 重新浏览器扫脸登录
```bash
# 认证状态会保存到.wq_auth_state.json
# 有效期约4小时
```

### 问题2: Simulate返回ERROR
**常见原因**:
- 表达式语法错误 → 使用FastPlus门禁预检
- 未知字段/函数 → 检查字段名拼写
- 速率限制 → 增加间隔时间

### 问题3: 所有alpha都FAIL
**根本原因**: 质量门槛设置过高或表达式模板有问题

**解决**:
1. 检查`LLM改进prompt.txt`，确认失败模式
2. 使用推荐的模板重新生成
3. 降低初始目标，先获得sharpe>0.5的alpha再优化

---

## 📝 最佳实践

### DO ✅
- 每次生成后**立即验证**前10个候选
- **保存**每轮的反馈数据到`alpha_submission_feedback.csv`
- 将LLM改进prompt.txt**发送给LLM**获取改进建议
- 定期备份`.wq_auth_state.json`

### DON'T ❌
- 不要跳过反馈分析直接生成新候选
- 不要删除`alpha_submission_feedback.csv`（这是学习的基础）
- 不要同时运行多个simulate任务（会触发速率限制）
- 不要提交未经simulate验证的alpha

---

## 🔗 相关资源

- WorldQuant Brain平台: https://platform.worldquantbrain.com
- Alpha表达式文档: [见项目知识库]
- v50架构说明: `auto_alpha_pipeline_rebuilt_v50.py`头部注释

---

## 🆘 需要帮助？

1. 查看`项目知识库/当前状态.md`
2. 查看`项目知识库/已验证解决方案.md`
3. 查看本文件的"故障排查"章节

---

**最后更新**: 2026-08-02  
**当前迭代**: 第1轮完成，第2轮准备中  
**反馈数据**: 40条记录  
**待改进**: 发送LLM改进prompt.txt到LLM，获取新候选
