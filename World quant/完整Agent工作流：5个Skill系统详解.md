# WorldQuant BRAIN 挖Alpha的Agent流程及Skills（完整工作流）

> **来源**：JX84394 (Expert consultant, Osmosis Allocator)  
> **发布时间**：3 hours ago  
> **主题**：5个Agent Skill + WebDataScope插件 + 实战28批全程记录

---

## 📦 全套文件打包下载

**文件名**：wqb-share-03.zip  
**百度网盘链接**：https://pan.baidu.com/s/1GhZ7A_XNqrh2iJWQoo53Sg?pwd=8s4v  
**提取码**：8s4v

**包含内容**：
- 5个Skill（SKILL.md + references/）
- WebDataScope 1.0.6插件源码
- WebData_20260219_V0.10.9.zip数据包（164个.bin, 35MB）
- tools/webdata_quality.py（数据质量排名脚本）
- tracking/experiment_log.md（28批实验全程日志）
- 课件.md（完整教程）

---

## 前言：为什么需要固化成Skill？

这半年我一直用 Claude Code + wqb-mcp 做alpha挖掘。踩过最大的坑不是模型不够聪明，而是：

> **同一套经验没法在下一个session复现**

今天讲清楚的规则，明天换个对话又从零开始，于是又交了一批IS好看、OS崩掉的alpha。

**解决方案**：把所有约束、门槛、判定表全部固化成Skill文件（SKILL.md + references/）。

**Skill = 写给agent看的作业指导书**：
- 什么时候触发
- 按什么顺序做
- 什么条件下必须REJECT
- 做完怎么验证

放进项目里之后，agent每次开工自动读，规则不再靠我口头复述。

---

## 一、5个Skill职责划分

| Skill | 职责 | 什么时候用 |
|-------|------|------------|
| **brain-alpha-orchestrator** | 端到端调度：预算分配、批次多样性、去重、可观测性 | 长周期挖矿（"跑一天"）|
| **brain-alpha-research** | 方向研究：数据集/字段/setting空间扩展、论坛模板归类 | 找方向、研究新数据集 |
| **brain-alpha-repair** | 候选修复：换手率、覆盖度、相关性、失败轨迹恢复 | 审计给出CONDITIONAL |
| **brain-alpha-robustness** | 提交前稳健性审计 + 过拟合判定 | set_alpha_properties前必跑 |
| **alpha-template-labs-data-analysis** | Brain Labs原始数据体检（Python alpha前置）| 设计Python alpha之前 |

---

## 二、最值钱的硬门槛（容易被忽略）

### 1. WebDataScope Failed计数是硬门槛

**比只看`result == "FAIL"`严格得多**

一个Sharpe 2.0、Fitness 1.5、ProdCorr 0.4的候选，只要**Failed RA ≠ 0**，就不是合格候选。

#### Failed RA计数口径（来自WebDataScope源码）
统计以下检测项中`result`既不是`PASS`也不是`PENDING`的数量：

```
HIGH_TURNOVER
LOW_TURNOVER
LOW_FITNESS
LOW_RETURNS
LOW_SHARPE
LOW_GLB_AMER_SHARPE
LOW_GLB_APAC_SHARPE
LOW_GLB_EMEA_SHARPE
LOW_ASI_JPN_SHARPE
IS_LADDER_SHARPE
LOW_2Y_SHARPE
LOW_SUB_UNIVERSE_SHARPE
LOW_ROBUST_UNIVERSE_SHARPE
LOW_AFTER_COST_ILLIQUID_UNIVERSE_SHARPE
LOW_INVESTABILITY_CONSTRAINED_SHARPE
LOW_ROBUST_UNIVERSE_RETURNS
CONCENTRATED_WEIGHT
```

**硬性要求**：
- REGULAR alpha: `Failed RA == 0`
- PPA alpha: `Failed PPA == 0`

---

### 2. 稳健性判定按"近3年regime"做

**不要求10年全强**

老年份的厂字形/CV/max-min只写成soft-flag，不单独REJECT。

**原因**：要求全历史都漂亮，会把还活着的信号一起杀掉。

---

### 3. 饱和数据集（≥10K alpha）必须假说优先

在饱和数据集上（如news12有120K alpha），**模板采样已经被社区挖穿了**，继续刷变体是在烧算力。

**正确做法**：假说优先
- 一个batch = 一个实验（primary + ablation + control + variant）
- 伪信号在第1批就被判死，而不是40次模拟之后

**识别标志**：
- 数据集已提交alpha数 ≥ 10,000
- 平均Fitness徘徊在低位（如0.42墙）
- 模板变体频繁出现UNITS WARN

---

### 4. 幽灵算子守卫

以下算子名字在论坛帖和旧笔记里满天飞，但**平台上根本没有**，用了会静默失败掉整个batch：

```
ts_entropy
ts_skewness
ts_percentage
group_normalize
tanh
sigmoid
s_log_1p
ts_median
ts_min_max_diff
ts_min_max_cps
ts_partial_corr
ts_co_kurtosis
ts_delta_limit
group_median
group_percentage
group_vector_proj
ts_decay_exp_window
```

**防御措施**：开工先跑一次`get_operators`对表。

---

## 三、WebDataScope数据包——三层社区先验数据库

这是这次更新最值钱的发现之一。`WebData_*.zip`用zlib+msgpack编码：

### 三层结构

#### 1. data/oth/osis_data.bin
- 各区域数据集级已提交alpha统计（count/sharpe/fitness）
- **用途**：数据集质量先验

#### 2. data/oth/info_data.bin
- **isos**：数据集+字段+类别级统计
- **neutralization**：每个数据集/字段在11种中性化下的表现
- **用途**：中性化选择、字段先验

#### 3. data/<ds>_<区>_<域>_Delay<N>.bin
- 每字段10年体检：逐年覆盖率、正负占比、离散性、偏度、更新频率、分位直方图
- **用途**：预处理与窗口决策

### 数据集质量快照（USA/D1）

- 约90万个已提交alpha（2022-02→2026-02）
- 平均sharpe: **0.358**
- 后面1.58门槛有多难，参照系就是它

### 一键出排名

```bash
python3 tools/webdata_quality.py --zip WebData_20260219_V0.10.9.zip --region USA --delay 1
```

---

## 四、目录结构（重要）

```
wqb-share-03/
├── 课件.md                          # 本教程完整版
├── WebDataScope-1.0.6/              # 华子哥插件源码
├── WebData_20260219_V0.10.9.zip     # 插件离线数据包
├── .claude/skills/                  # ⚠️ 注意是.claude目录！
│   ├── brain-alpha-orchestrator/
│   ├── brain-alpha-research/
│   ├── brain-alpha-repair/
│   ├── brain-alpha-robustness/
│   └── alpha-template-labs-data-analysis/
├── tools/webdata_quality.py
└── tracking/experiment_log.md
```

### 三个要点

1. **Skill必须放在`.claude/skills/<name>/SKILL.md`**
   - 这是Claude Code项目级技能的约定位置
   - 放对之后新开会话，技能列表里会直接出现这5个技能
   - Agent自动加载，也能`/brain-alpha-research`手动调用

2. **检查SKILL.md里引用的路径是否正确**
   - 如果不正确可以让AI修复

3. **华子哥插件仓库**
   - GitHub: github.com/AlphaQuantKit/WebDataScope
   - 压缩包里已带1.0.6源码和数据包

---

## 五、添加MCP

用自己的mcp也行，或者用推荐的：
```bash
# GitHub: github.com/lavender1203/world-quant-brain-mcp
claude mcp add wqb-mcp --transport http http://127.0.0.1:8876/mcp
```

### 验证
新开会话让agent调`authenticate`，返回`authenticated`即接通。

### 实验主链路
```
get_operators（算子白名单）
  → recommend_datasets / get_datafields
  → create_multi_simulation（8条/批）
  → get_alpha_details（Failed RA）
  → get_alpha_yearly_stats / get_alpha_pnl
  → check_self_correlation（本地免额度）
  → check_correlation（ProdCorr）
  → compute_mutual_correlation（篮子互相关）
  → set_alpha_properties
```

批次失败先`lookINTO_SimError_message`归因再重发。

---

## 六、第一步实操：让Agent研究WebDataScope并优化Skill

直接把这段提示词丢给agent：

```
先研究下这个项目 WebDataScope-1.0.6（数据在WebData_20260219_V0.10.9.zip目录），
看看对寻找alpha有哪些帮助，已知对挑选高质量数据集和数据字段还有中性化选择有帮助,
已知极少有人提交alpha的数据集和字段是低质量的，当然可能还有其他用途比如数据预处理，
时间窗口选择等，尽可能多的发掘更多用法提升alpha挖掘效率，然后修正和优化skill
```

### Agent实际做了什么（压缩包里已是优化后版本）

1. **解包数据**（zlib+msgpack），写出`tools/webdata_quality.py`

2. **读源码**：
   - `background.js`的`getAlphaCheckStates()`（Failed RA/PPA计数口径）
   - `dataAna.js`的字段体检指标
   - `background.js:327`一条被注释掉的经验：
     > "risk neut那个就是用传统neut跑的时候会有个risk neut的线，大概sharpe和fit都更高的话就需要遍历risk neut"

3. **量化验证"极少有人提交 = 低质量"**
   - 找到了反例边界：fundamental2有29784次提交但平均sharpe -0.003
   - 大量尝试仍失败是另一种低质量
   
   **双向规则**：
   - 甜点区 = 100~3000次提交 且 sharpe ≥ 1.1×区域均值
   - <50次不可信
   - >30K饱和（ProdCorr死区）

4. **提取分数据集中性化统计**
   - 全局：STATISTICAL(0.461) > SUBINDUSTRY(0.424) > INDUSTRY(0.358)
   - 但逐数据集差异巨大：
     - insiders3 → SLOW 0.755
     - model38 → REVERSION_AND_MOMENTUM 0.724
   - **从此不再盲扫中性化，按数据集查表排优先级**

### 这些发现写成了4处Skill增量

- **research**：新增`references/webdatascope-data-quality.md` + 第15步（零成本预筛）
- **orchestrator**：第17步（数据驱动中性化优先级 + risk-neut遍历）
- **repair**：第2b步（按字段分布形态选修复方向）

---

## 七、第二步实操：挖矿测试提示词

```
/goal 挖掘region=USA 探索不同的universe delay=D1 max_trade=ON REGULAR类型的alpha
sharpe>1.58 fitness>1 2ysharpe>1.6 margin>5bp，turnover>5%,turnover<30%，
risk neutralization表现要好sharpe>1,fitness>0.7,margin>5bp，
操作符数量<8，ra_failed_count=0，
挑选未点亮的金字塔的1个数据集，不要频繁切换数据集除非alpha模板多样性已穷尽并且查阅论坛还是无计可施，
使用1-2个字段，直到找到3个满足提交要求的不同数据集的完全不同策略风格的单数据集的未提交的alpha,
这些alpha彼此之间的相关性<0.4，去点亮更多的金字塔,否则不要停下来，
生产相关性结果没有出来或生产相关性>0.7的alpha不符合提交要求，不要提交，不要提交，记住了吗，我想自己手动提交。
回测使用multi_create_simulate 8个并发，不要使用create_simulate，
每找到一个就test robust和进行严格的过拟合测试,then pass帮我设置好属性。
不要使用trade_when\add\multiply，不要创建自动化任务,
可以参考论坛模板与idea文章,每10轮回测进行alpha表达式多样性评估，
包括操作符探索率，字段探索率，模板骨架多样性，风格多样性，预处理，收益来源归因，失效风险等进行总结和扫盲，
中文回答
```

### 提示词的每个要求如何被Skill接住

- `ra_failed_count=0` → orchestrator 14a + robustness B.0 的WebDataScope硬门槛
- "挑未点亮金字塔" → research 15 + recommend_datasets
- "不频繁切换数据集" → 升级阶梯（2批无起色换字段/骨架，3批不达80%才换备用数据集）
- "相关性<0.4" → self-corr（免费）→ ProdCorr → 凑齐2个立即互相关
- "不要提交" → 流水线止于set_alpha_properties

---

## 八、真实实验：28批 / 280次模拟 / 9个数据集

完整逐批日志在压缩包`tracking/experiment_log.md`。

### 轨迹提炼

| 阶段 | 批次 | 关键动作 |
|------|------|----------|
| 准备 | 0 | 认证/127算子白名单/金字塔/三风格轨道入围 |
| 轮转探索 | 1-9 | 三轨爬到0.9-1.1平台期；发现情绪反向定价、离散度桶>方向桶 |
| 中性化跃迁 | 7 | analyst39 SUBINDUSTRY→FAST，0.59→1.04翻倍 |
| 换仓与新臂 | 10-14 | insiders3→shortinterest3；应急开other566，首批即1.80 |
| 冲线 | 15-17 | 组内混合+平滑+decay10 → 4条候选Failed RA=0全指标达标 |
| ProdCorr攻防 | 18-20 | 0.829✗ → 0.779✗ → 0.593✓，signed_power³凸性一击破局 |
| 收尾扫荡 | 21-28 | 注意力臂1.50近失；news54等证伪；互相关矩阵定稿 |

### 最终成果（已设属性，未提交）

```
ts_decay_linear(signed_power(subtract(group_rank(oth566_l2r20_label, subindustry), 0.5), 3), 10)
```

**配置**：
- USA / TOP3000 / D1 / REGULAR
- REVERSION_AND_MOMENTUM / decay10 / trunc0.08 / max_trade ON

**指标**：
- Sharpe: 1.74（>1.58✓）
- Fitness: 1.04（>1✓）
- 2Y: 2.79（>1.6✓）
- Margin: 7.2bp（>5bp✓）
- 换手: 14.7%（5-30%✓）
- Failed RA=0
- ProdCorr: 0.593（≤0.7✓）
- SelfCorr: 0.416
- 4个操作符
- 近3年sharpe: 1.42/3.16/2.37
- robustness审计: PASS
- 点亮USA/D1/OTHER金字塔（×1.5）

**经济故事**：
对图表模型20日预测标签在子行业内做凸性押注，立方放大尾部信念、降低拥挤，R&M剥离反转/动量暴露。

### 结果实话

300次模拟预算内，"3个不同数据集全指标达标"完成1/3：
- 1个同风格完整通过（上述）
- 4条同数据集全合格备份
- 3条不同风格近失候选

互相关矩阵证实[图表模型, 注意力, 价值]三风格篮子两两<0.4成立。

**有意思的反例**：做空供需 × 价值 = 0.766
- 低借券关注股和便宜小盘股高度重叠
- "风格不同"必须以相关性数据仲裁，不能凭数据集类目直觉

全程未调用`submit_alpha`。

---

## 九、十条过程教训（比结果更值钱）

1. **中性化先验要看样本量**
   - 大样本全部兑现（FAST n=194→翻倍、R&M n=48→1.8+）
   - 小样本全部翻车（SLOW n=51、SECTOR n=161）

2. **风险族中性化是放大器但不可盲移植**
   - R&M对模型标签+80%，对做空供需/价值反而-30%

3. **情绪方向反向定价、离散度桶>方向桶**
   - repair skill里news修复方向(v)被逐字验证

4. **数据体检决定预处理**
   - form4_bnum覆盖0.41 → 必须ts_backfill
   - 月频字段 → ≥21d窗口

5. **一条表达式参数错误会废掉整批8条**
   - hump写成位置参数的事故
   - 批前校验要查参数签名，不只查算子名

6. **水平量强于变化量**
   - 借券利用率水平1.0 vs 变化量-1.0
   - 标签水平1.8 vs 标签修正-0.27

7. **fitness = sharpe×√(收益/换手)是硬数学**
   - 换手23%→15%（decay 6→10）让fitness 0.91→1.18达标

8. **ProdCorr破局靠形态凸性**
   - 换字段腿只降0.05
   - signed_power³一击-0.24

9. **甜点区先验有效**
   - 实验里三个最强数据集全部来自"100~3000提交 + sharpe≥1.1×均值"名单

10. **风格差异以互相关矩阵仲裁**
    - 不要信类目直觉（做空供需×价值0.766反例）

---

## 十、使用的模型

根据评论区交流：
- **早期**：anthropic的opus（反代）
- **现在**：glm5.1（总体体验堪堪够用）

---

## 十一、社区反馈

- **LG87838**: "AI能干活，但AI的产出很不稳定，想提高效率，想减少很多无效的回测。这篇帖子给出了很好的思路，期待大佬的后续分享。"
- **YR99599**: "能不能结合记忆机制，加强对某方向的特化，这样以后如果垂直点完塔可以直接进行知识迁移水平点塔？"（作者回复：可以看github.com/EvoMap/evolver）
- **DZ31817**: "token消耗多，具体会消耗到什么水平，claude pro的用量够用吗？"
- **CZ78575**: "试用以下效果不错！"
- **CH62432**: "这么长时间的任务流程，在整个流程中如何管理上下文的问题呢？"

---

*整理时间：2026-07-29*  
*原帖来源：WorldQuant BRAIN 顾问专属中文论坛*  
*发帖ID：JX84394*
