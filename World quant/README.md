# WorldQuant BRAIN 社区精华整理

本目录包含从WorldQuant BRAIN中文论坛手动整理的高价值内容，已去除网页导航、评论区等噪音。

---

## 📋 文档索引

### 1. 🔧 核心：API认证问题及解决方案.md
**解决你当前遇到的API 401问题**

- **问题症状**：`authentication endpoint returned HTTP 401`
- **根本原因**：香港账号需要人脸识别/生物验证（Persona/Biometric）
- **关键修复**：在用户完成人脸验证后，代码需要持续轮询biometric URL直到状态码200/201
- **完整代码**：包含正确的persona轮询实现
- **集成指导**：如何修复你项目中的 `alpha_mining/auth/session_manager.py` 和 `client.py`

**⚠️ 立即行动**：
```bash
python diagnose_wq_auth.py --mode both --simulate
```
根据诊断结果应用文档中的修复方案。

---

### 2. 💡 优质Alpha挖掘：AI工作流优化方法.md
**JR57542的三板斧魔改GEM工作流**

核心思想：解决"AI无法区分表达式错误 vs 数据质量不足"的根本矛盾

#### 三板斧
1. **论文预搜索机制**：搜索20篇相关学术论文，给AI搭上经济学基础
2. **校准策略（螺旋迭代）**：批量回测200个→分析结果→修改idea→再生成→迭代1-2轮
3. **信号灯系统（Direction Radar）**：
   - 🟢 GREEN：加大预算
   - 🟡 YELLOW：谨慎继续
   - 🔴 RED：结构性改动
   - ⚫ DEAD：换方向

#### 关键创新：算子多样性分数
将BRAIN算子分成6大族，要求每批至少覆盖3个不同族，避免"算子选错"和"方向本身不行"的混淆。

#### 实战效果
- 平均每找到一个合格alpha的回测次数从300+降到150左右
- 错误方向平均探索轮次从5轮降到2轮
- 产出比提升约40%

---

### 3. 🎯 完整Agent工作流：5个Skill系统详解.md
**JX84394的生产级实战经验**

包含：
- 5个Claude Code Agent Skill完整文档
- WebDataScope数据包解析（三层社区先验数据库）
- 28批/280次模拟/9个数据集的完整实验日志
- 最终成果：Sharpe 1.74, Fitness 1.04的完整alpha

#### 5个Skill职责
| Skill | 职责 |
|-------|------|
| brain-alpha-orchestrator | 端到端调度 |
| brain-alpha-research | 方向研究 |
| brain-alpha-repair | 候选修复 |
| brain-alpha-robustness | 稳健性审计 |
| alpha-template-labs-data-analysis | 数据体检 |

#### 最值钱的4个硬门槛
1. **WebDataScope Failed RA == 0**（比result=="FAIL"严格）
2. **稳健性判定按近3年regime**（不要求10年全强）
3. **饱和数据集必须假说优先**（≥10K alpha的数据集）
4. **幽灵算子守卫**（17个论坛常见但平台不存在的算子）

#### 十条过程教训
从中性化先验、数据体检、ProdCorr破局到互相关矩阵仲裁，全是血泪经验。

---

## 🎁 资源包下载

**JX84394完整打包**（wqb-share-03.zip）：
- 链接: https://pan.baidu.com/s/1GhZ7A_XNqrh2iJWQoo53Sg?pwd=8s4v
- 提取码: 8s4v

包含：
- 5个Skill源码
- WebDataScope 1.0.6插件
- WebData数据包（164个.bin, 35MB）
- 质量排名脚本
- 28批实验全程日志

---

## 🚀 快速开始

### 步骤1：修复认证问题（优先）
```bash
# 1. 运行诊断
python diagnose_wq_auth.py --mode both --simulate

# 2. 根据结果应用《核心：API认证问题及解决方案.md》中的修复
```

### 步骤2：学习工作流
阅读《优质Alpha挖掘：AI工作流优化方法.md》了解三板斧思想。

### 步骤3：搭建Skill系统
1. 下载wqb-share-03.zip
2. 按照《完整Agent工作流：5个Skill系统详解.md》设置目录结构
3. 放置在`.claude/skills/`目录下

### 步骤4：实战
使用文档中的提示词模板开始挖掘。

---

## 📊 关键数据参照

### API认证
- Session有效期：**4小时**
- 登录频率限制：**24小时内≤25次**
- 人脸验证轮询间隔：**2秒**

### Alpha指标（USA/D1参照）
- 社区平均Sharpe：**0.358**（90万已提交alpha）
- 提交门槛Sharpe：**1.58**（难度系数：4.4×均值）
- 数据集甜点区：**100~3000次提交 且 sharpe≥1.1×区域均值**
- 饱和数据集阈值：**≥10K alpha**

### 中性化先验（全局USA）
1. STATISTICAL: 0.461
2. SUBINDUSTRY: 0.424
3. INDUSTRY: 0.358

但**分数据集差异巨大**，需查表而非盲扫。

---

## ⚠️ 关键注意事项

### 必须遵守
1. **使用API请关闭VPN**（SSL握手问题）
2. **Failed RA必须==0才能提交**（WebDataScope计数口径）
3. **不使用幽灵算子**（17个论坛常见但平台不存在）
4. **饱和数据集用假说优先**（避免模板空转）

### 强烈建议
1. Session复用（避免触发25次/日限制）
2. 实现persona轮询（香港账号必需）
3. 按数据集查表选中性化（不盲扫）
4. 算子多样性≥3族（避免结构单一）

---

## 🔗 相关资源

- **官方API文档**：https://api.worldquantbrain.com/documentation
- **WebDataScope插件**：github.com/AlphaQuantKit/WebDataScope
- **MCP工具**：github.com/lavender1203/world-quant-brain-mcp
- **基因库进化**：github.com/EvoMap/evolver

---

## 📝 更新日志

- **2026-07-29**：初始整理，清理3个核心文档
  - 解决API认证问题
  - AI工作流优化方法
  - 5个Skill系统详解

---

*整理者说明*：
本目录内容来自WorldQuant BRAIN中文论坛，已获取公开分享帖子并整理。
所有技术方案均经社区验证有效。
如有更新或补充，请保持文档结构一致性。
