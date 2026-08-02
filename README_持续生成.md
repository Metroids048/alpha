# Alpha持续生成系统 - 使用说明

## 核心工作流

```
┌─────────────────────────────────────────────────────┐
│  离线持续生成（无限批次，自动归档）                    │
│  ├─ 每批次: 600个候选（离线配置上限）                 │
│  ├─ 连续5轮0新增 → 自动归档 → 重置 → 新批次          │
│  └─ 循环往复，无需人工干预                           │
└─────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│  人工筛选（低频）                                     │
│  ├─ 从归档批次中挑选优质候选                          │
│  └─ 合并到待提交列表                                 │
└─────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│  批量提交（低频）                                     │
│  ├─ 运行: python 提交Alpha.py                        │
│  └─ 平台反馈更新 hopeful_alphas.jsonl 种子库         │
└─────────────────────────────────────────────────────┘
           ↓
      （新种子 → 新候选，回到顶部）
```

---

## 快速开始

### 1. 启动持续生成（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File ".\启动高质量生成.ps1"
```

**会发生什么**：
- 第1轮：生成600个候选 → 写入 `高质量Alpha候选.csv`
- 第2-6轮：0新增（v50离线模式的固有限制）
- 第6轮：自动归档到 `archive_batch_20260802_123456.csv`，清空去重池
- 第7轮：重新生成600个候选 → 写入新的 `高质量Alpha候选.csv`
- 循环往复...

**停止**：`Ctrl+C`，当前批次会保留在 `高质量Alpha候选.csv`

---

### 2. 手动运行（自定义参数）

```bash
python 生成Alpha.py \
  --preset diverse_exploration \
  --near-pass-share 0.2 \
  --max-same-shape 40 \
  --batch-size 300 \
  --max-payloads 600 \
  --interval 30 \
  --output "我的候选.csv"
```

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--preset` | mixed | 使用 `diverse_exploration` 打开结构探索 |
| `--near-pass-share` | 0.40 | 近通过变异占比（0.2=更多样但可能通过率低） |
| `--max-same-shape` | 16 | 同结构上限（40=释放更多参数变体） |
| `--batch-size` | 300 | 每轮目标候选数 |
| `--max-payloads` | 600 | 每轮最大payload数 |
| `--interval` | 15 | 轮次间隔（秒） |

---

## 文件说明

### 输出文件

| 文件 | 说明 |
|------|------|
| `高质量Alpha候选.csv` | **当前批次**（600个候选，滚动更新） |
| `archive_batch_<时间戳>.csv` | **已归档批次**（每批次600个，永久保存） |

### 核心脚本

| 脚本 | 功能 |
|------|------|
| `生成Alpha.py` | 持续生成引擎（支持自动批次管理） |
| `提交Alpha.py` | 提交候选到WorldQuant Brain |
| `启动高质量生成.ps1` | 一键启动持续生成 |

---

## 为什么每批次只有600个？

v50引擎在离线模式下的工作原理：

```
固定输入:
├─ 64个near_pass种子（hopeful_alphas.jsonl）
├─ 5697行字段缓存（.alpha_datafields_cache.json）
└─ 固定表达式模板

         ↓

生成6305个原始候选
├─ structure_budget_exceeded: 4279个（同结构超预算）
├─ history_skeleton_seen: 670个（历史结构重复）
├─ gate_no_ts_operator: 336个（缺少时序算子）
├─ duplicate_in_run: 197个（轮内重复）
└─ **kept: 823个** → 转换为600个payload

         ↓

第1轮：600个全部写入CSV
第2轮：生成相同的600个 → exact_hash命中 → 0新增
```

**结论**：离线配置下，每批次产出约600个unique候选是上限。要获得新候选，需要提交到平台获取新反馈（更新种子库）。

---

## 高级技巧

### 1. 调整批次大小

如果想要更多候选/批次（牺牲多样性）：

```bash
python 生成Alpha.py --max-same-shape 80 --max-payloads 1200
```

可能产出 ~1000个候选/批次，但结构多样性会降低。

### 2. 强制高多样性（牺牲数量）

```bash
python 生成Alpha.py --max-same-shape 10 --near-pass-share 0.1
```

可能只产出 ~200个候选/批次，但结构多样性更高。

### 3. 监控归档文件

```powershell
# 查看已归档批次数量
Get-ChildItem archive_batch_*.csv | Measure-Object | Select-Object Count

# 合并所有归档批次
Get-Content archive_batch_*.csv | Select-Object -Unique > 全部候选汇总.csv
```

---

## 常见问题

### Q1: 为什么多样性只有9.2%？

A: `max-same-shape=40` 允许同一结构产生40个参数变体，所以600个候选可能只有55种不同结构（600/55≈11个变体/结构）。这是**参数多样性**（大）vs **结构多样性**（小）的权衡。

如果需要更高结构多样性，降低 `--max-same-shape` 到10-20。

### Q2: 连续运行会不会重复生成相同候选？

A: **不会**。每批次归档后会清空去重池（`existing_hashes.clear()`），但v50引擎在离线模式下确实会重复生成相同的600个候选。这是预期行为：离线模式下种子固定 → 输出固定。

要避免重复，需要定期提交候选到平台获取新反馈。

### Q3: 可以手动停止并继续吗？

A: 可以。`Ctrl+C` 停止后，当前批次保留在 `高质量Alpha候选.csv`。重新运行会从已有候选继续累积（去重）。

---

## 维护建议

1. **每周清理归档**：归档文件会持续增长，定期移动到备份目录
2. **定期提交反馈**：积累2000-3000个候选后提交一批，获取新种子
3. **监控registry大小**：`alpha_generated_expressions.csv` 超过10MB时考虑清理旧数据

---

## 技术细节

### 自动归档机制

```python
if new_count == 0:
    zero_new_streak += 1
    if zero_new_streak >= 5:
        # 归档当前CSV
        shutil.move(output_path, archive_path)
        # 清空去重池
        existing_hashes.clear()
        # 重置计数
        total_generated = 0
        zero_new_streak = 0
```

### 每轮引擎重新初始化

```python
def _make_pipeline():
    # 每轮创建新的PipelineConfig和WorldQuantAlphaPipeline
    # 确保读取最新的alpha_generated_expressions.csv
    ...

# 主循环
while True:
    pipeline, selector, _ = _make_pipeline()  # 重新初始化
    candidates, catalog = pipeline.generate_candidates()
    ...
```

---

## 下一步

1. **启动生成**：`powershell -ExecutionPolicy Bypass -File ".\启动高质量生成.ps1"`
2. **让它运行几个小时**，积累几个批次（每批次600个）
3. **筛选归档文件**，挑出优质候选
4. **批量提交**：`python 提交Alpha.py`
5. **等待反馈**，然后重复步骤1

---

生成时间：2026-08-02  
版本：v2.0（自动批次管理）
