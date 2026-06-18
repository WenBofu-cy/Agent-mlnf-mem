# ag-mem-27-L4长期层经验抽象提炼单元 规整落地版接口规格文档
统一对齐 ag-mem-24/25/26 系列文档架构，补充标准化结构体、梳理全链路交互、补全逻辑细节，完整保留全部原生业务规则，适配开发编码、跨模块联调、自动化测试使用。

## 一、模块基础元信息
| 项 | 内容 |
|----|------|
| 模块唯一ID | ag-mem-27 |
| 模块全称 | L4长期层经验抽象提炼单元 |
| 所属架构 | 三、漏斗二：任务经验漏斗 / 五层存储（L0~L5） |
| 上游依赖（接收消息） | ag-mem-26 L4长期存储单元（下发提炼指令+条目快照）、ag-mem-01 总控F0（全局熔断调度指令） |
| 下游被依赖（对外输出） | ag-mem-26（回执+通用规则列表）、ag-mem-28 L5核心存储单元（高置信规则推送）、ag-mem-03 漏斗二调度单元（提炼指标上报） |
| 核心定位 | L4配套规则提炼专用计算单元，仅做特征聚类、序列挖掘、通用规则生成；无存储、写入、晋升、遗忘决策能力，纯离线特征加工模块 |
| 核心约束 | 最小3条条目才可提炼；工具/特征相似度双阈值校验；输入使用数据快照隔离并发修改；规则自动剥离个性化参数 |

## 二、内部状态机（6种互斥运行状态）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `IDLE` | 空闲等待 | 无提炼任务，轮询总线指令 | 初始化完成；提炼任务全部执行完毕；熔断解除恢复 |
| `FEATURE_EXTRACT` | 特征提取 | 批量提取工具序列、任务特征向量、标签分布 | 收到 ag-mem-26 抽象提炼触发指令 |
| `RULE_GENERATE` | 规则生成 | 聚类、LCS序列匹配、置信度计算、组装通用规则 | 特征提取完成，满足所有基础提炼阈值 |
| `OUTPUTTING` | 结果输出 | 下发规则、回执、状态上报、可选推送高置信规则至L5 | 规则生成完成 |
| `INSUFFICIENT_DATA` | 数据不足 | 条目数量/特征相似度不达标，终止提炼 | 条目＜3 / 序列一致性＜0.6 / 特征显著度＜0.6 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，立即中断当前计算，丢弃半成品 | 接收F0熔断指令；RESUME指令切回IDLE |

## 三、提炼全局阈值配置
| 配置项 | 阈值 | 业务说明 |
|--------|:---:|------|
| 最小有效提炼条目数 | ≥3 | 低于该值直接返回数据不足通知 |
| 工具调用序列一致性阈值 | ≥0.60 | LCS公共序列长度/序列平均长度，不足则终止提炼 |
| 任务特征共性显著度阈值 | ≥0.60 | K-Means聚类中心与样本平均余弦相似度 |
| 结果标签最低兼容占比 | ≥0.70 | 低于该值不阻断提炼，仅降低置信度权重 |
| 单次单批次最大处理条目 | 50 | 超量自动拆分多批次串行处理 |
| 高置信推送L5门槛 | ≥0.85 | 仅置信度达标才推送规则至ag-mem-28 |
| 一般规则区分线 | ≥0.80 | 0.80~0.84为一般规则；≥0.85高置信规则 |

## 四、输入总线接口（内部调度总线 只读）
| 输入消息名称 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 抽象提炼触发指令 | AbstractTriggerCmd | ag-mem-26 | 同槽累计20条新条目 / 72h定时全局提炼 | 高 |
| L4同类经验条目快照集 | List<L4RawItemSnapshot> | ag-mem-26 | 随提炼指令一并下发（快照隔离并发修改） | 高 |
| 全局调度控制指令 | F0ControlEnum | ag-mem-01 | 系统暂停/恢复/熔断 | 紧急 |

### 入参结构体定义
1. **AbstractTriggerCmd 提炼触发指令**
```json
{
  "merge_range": "slot_xxx / all_unabstract",
  "source_slot_id": "string 来源分槽标识",
  "item_group": "List<L4RawItemSnapshot>",
  "trigger_cause": "enum[accumulate_20 / timing_72h]"
}
```
2. **L4RawItemSnapshot 条目快照（不可变）**
```json
{
  "item_id": "原始条目唯一ID",
  "exp_data": "脱敏后L4经验数据（无用户隐私）",
  "I_l4": "float L4重算后重要度",
  "slot_id": "来源分槽",
  "feature_vec": "float[] 任务特征向量",
  "tool_seq": "list<string> 标准化工具调用序列",
  "result_tag": "enum[成功/失败/部分成功]"
}
```
3. **F0ControlEnum 调度指令枚举**
`PAUSE / RESUME / FUSE`

## 五、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 抽象提炼完成回执 | AbstractCallbackResp | ag-mem-26 | 单批次提炼全部完成 | 高 |
| 通用规则列表 | List<L4GeneralRule> | ag-mem-26 | 规则生成完成，用于更新条目关联规则 | 高 |
| 数据不足通知 | InsufficientNoticeResp | ag-mem-26 | 任意基础阈值不满足 | 普通 |
| 高置信规则推送 | L4GeneralRule | ag-mem-28 | 规则置信度≥0.85，可选下发 | 高 |
| 提炼统计状态上报 | AbstractStatReport | ag-mem-03 | 单次提炼结束后上报指标 | 普通 |

### 出参结构体定义
1. **AbstractCallbackResp 提炼回执**
```json
{
  "range_info": "来源槽位/提炼范围描述",
  "rule_generate_cnt": "int 生成规则条数",
  "cost_ms": "long 本次提炼总耗时",
  "avg_confidence": "float 规则平均置信度",
  "source_item_ids": "list<string> 本次提炼依据的全部条目ID"
}
```
2. **L4GeneralRule 通用抽象规则**
```json
{
  "rule_id": "RULE-L4-UUID",
  "rule_desc": "基于高I条目生成标准化通用描述",
  "apply_scope": {
    "scene_type": "分槽对应场景类别",
    "tool_type_set": "公共工具集合",
    "task_category": "聚类中心推导任务类型"
  },
  "confidence": "float [0,1] 综合置信度",
  "source_item_ids": "list<string> 原始条目溯源ID",
  "rule_type": "enum[高置信度规则 / 一般规则]",
  "create_ts": "long 规则生成时间戳"
}
```
3. **InsufficientNoticeResp 数据不足通知**
```json
{
  "fail_reason": "条目数不足/序列一致性不足/特征共性不足",
  "current_item_cnt": "int 当前输入条目数量",
  "min_require_cnt": 3,
  "similar_score": "float 对应维度相似度得分"
}
```
4. **AbstractStatReport 周期上报指标**
```json
{
  "current_state": "模块状态枚举",
  "total_abstract_batch": "int 累计提炼批次",
  "total_rule_generated": "int 累计生成规则总数",
  "global_avg_confidence": "float 全局平均规则置信度"
}
```

## 六、标准化提炼算法与置信度公式
### 6.1 三层校验前置过滤
1. 条目数量校验：count < 3 → 数据不足
2. 工具序列LCS一致性校验：
$$
SeqSim = \frac{LCS长度}{所有序列平均长度}
$$
$SeqSim < 0.60$ → 终止提炼
3. 任务特征聚类相似度校验：
K-Means K=1，取聚类中心与每条样本余弦相似度平均值 $FeatSim$
$FeatSim < 0.60$ → 终止提炼

### 6.2 标签一致性计算
统计分组内各result_tag占比：
$$
TagSim = \frac{max\_tag\_count}{total\_item\_count}
$$
$TagSim \ge 0.7$ 则标签一致性权重取1.0，否则使用原始比例参与置信度计算。

### 6.3 综合置信度公式
$$
Conf = 0.4 \times FeatSim + 0.3 \times TagSim + 0.2 \times AvgI + 0.1 \times NormItemCnt
$$
- $AvgI$：分组内所有条目L4重要度均值
- $NormItemCnt = min(item\_count / 20, 1.0)$，条目越多权重越高，上限1.0
- 输出区间：$Conf \in [0,1]$

### 6.4 规则分类标准
- $Conf \ge 0.85$：高置信度规则，自动推送ag-mem-28
- $0.80 \le Conf < 0.85$：一般规则，仅下发ag-mem-26，不推送L5
- $Conf < 0.80$：低置信规则，正常返回L4，不推送L5

## 七、完整业务主流程伪代码（注释优化版）
```python
FUNCTION l4_abstraction_main_loop():
    # 状态常量定义
    STATE_IDLE = "IDLE"
    STATE_FEATURE = "FEATURE_EXTRACT"
    STATE_RULE = "RULE_GENERATE"
    STATE_OUTPUT = "OUTPUTTING"
    STATE_INSUFF = "INSUFFICIENT_DATA"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_IDLE
    total_batch_counter = 0  # 累计提炼批次计数器

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度指令
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSED
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSED:
                internal_state = STATE_IDLE

        # 2. 接收L4下发提炼触发指令
        if recv_abstract_trigger():
            if internal_state == STATE_PAUSED:
                log("模块熔断，拒绝本次提炼任务")
                continue
            
            trigger_msg = get_trigger_msg()
            raw_snapshot_list = trigger_msg.item_group
            source_slot = trigger_msg.source_slot_id
            start_ts = NOW()

            # 分批拆分：单批最大50条
            batch_list = split_to_batch(raw_snapshot_list, batch_size=50)

            for batch in batch_list:
                internal_state = STATE_FEATURE
                item_cnt = len(batch)
                source_ids = [item.item_id for item in batch]

                # 校验1：条目数量不足3条
                if item_cnt < 3:
                    internal_state = STATE_INSUFF
                    send_insufficient_notice(
                        reason="同类经验条目数不足",
                        current=item_cnt, min_req=3, score=0.0
                    )
                    internal_state = STATE_IDLE
                    continue

                # 2.1 提取全部工具序列，计算LCS序列相似度
                all_tool_seqs = [item.tool_seq for item in batch]
                lcs_result = calc_lcs(all_tool_seqs)
                avg_seq_len = sum(len(s) for s in all_tool_seqs) / len(all_tool_seqs)
                seq_sim = lcs_result["length"] / avg_seq_len if avg_seq_len > 0 else 1.0

                if seq_sim < 0.60:
                    internal_state = STATE_INSUFF
                    send_insufficient_notice(
                        reason="工具调用序列一致性不足",
                        current=item_cnt, min_req=3, score=seq_sim
                    )
                    internal_state = STATE_IDLE
                    continue

                # 2.2 特征向量聚类，计算共性显著度FeatSim
                all_feature_vecs = [item.feature_vec for item in batch]
                cluster_center = kmeans_single_cluster(all_feature_vecs)
                sim_list = [cos_sim(cluster_center, vec) for vec in all_feature_vecs]
                feat_sim = mean(sim_list)

                if feat_sim < 0.60:
                    internal_state = STATE_INSUFF
                    send_insufficient_notice(
                        reason="任务特征共性不显著",
                        current=item_cnt, min_req=3, score=feat_sim
                    )
                    internal_state = STATE_IDLE
                    continue

                # 2.3 统计标签分布，计算TagSim
                tag_count_map = {}
                for item in batch:
                    tag = item.result_tag
                    tag_count_map[tag] = tag_count_map.get(tag, 0) + 1
                max_tag_num = max(tag_count_map.values())
                tag_sim = max_tag_num / item_cnt
                tag_sim = 1.0 if tag_sim >= 0.7 else tag_sim

                # 3. 进入规则生成阶段
                internal_state = STATE_RULE
                # 计算综合置信度
                avg_I = mean([item.I_l4 for item in batch])
                norm_cnt = min(item_cnt / 20.0, 1.0)
                conf = 0.4 * feat_sim + 0.3 * tag_sim + 0.2 * avg_I + 0.1 * norm_cnt

                # 选取I最高条目作为规则描述模板
                batch_sorted_by_I = sorted(batch, key=lambda x: x.I_l4, reverse=True)
                top_item = batch_sorted_by_I[0]

                # 组装通用规则
                rule_type = "高置信度规则" if conf >= 0.85 else "一般规则"
                new_rule = {
                    "rule_id": f"RULE-L4-{gen_uuid()}",
                    "rule_desc": generate_rule_desc(top_item, lcs_result["sequence"]),
                    "apply_scope": {
                        "scene_type": get_scene_by_slot(source_slot),
                        "tool_type_set": extract_tool_types(lcs_result["sequence"]),
                        "task_category": infer_task_from_center(cluster_center)
                    },
                    "confidence": conf,
                    "source_item_ids": source_ids,
                    "rule_type": rule_type,
                    "create_ts": NOW()
                }

                # 4. 输出阶段
                internal_state = STATE_OUTPUT
                # 下发规则列表至ag-mem-26
                send_rule_list_to_m26([new_rule])
                # 高置信规则推送L5
                if conf >= 0.85:
                    send_high_conf_rule_to_m28(new_rule)
                # 组装回执返回L4
                callback = {
                    "range_info": source_slot,
                    "rule_generate_cnt": 1,
                    "cost_ms": NOW() - start_ts,
                    "avg_confidence": conf,
                    "source_item_ids": source_ids
                }
                send_abstract_callback(callback)
                total_batch_counter += 1

            # 全部批次处理完成，上报统计指标
            stat_report = build_stat_report(internal_state, total_batch_counter)
            send_stat_report(stat_report, target="ag-mem-03")
            internal_state = STATE_IDLE

        SLEEP(50)

# 子函数：批量LCS最长公共子序列计算
FUNCTION calc_lcs(seq_list):
    if len(seq_list) == 0:
        return {"sequence": [], "length": 0}
    base_seq = sorted(seq_list, key=lambda s: len(s))[0]
    common = base_seq
    for seq in seq_list[1:]:
        common = lcs_algo(common, seq)
    return {"sequence": common, "length": len(common)}
```

## 八、异常故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|--------|----------|----------|
| 输入条目数量＜3 | 发送数据不足通知，不生成任何规则 | 后续积累至3条以上再次触发提炼 |
| 工具序列一致性＜0.60 | 返回数据不足通知，标注序列相似度得分 | 补充更多同模式相似经验条目 |
| 任务特征平均相似度＜0.60 | 返回数据不足通知，标注特征显著度得分 | 补充特征高度趋同的经验 |
| 生成规则置信度＜0.5 | 正常下发至ag-mem-26，不推送L5，标记低置信规则 | 新增同类条目后重新提炼提升置信度 |
| 提炼期间上游条目并发删除/修改 | 依赖下发的快照数据计算，不受外部变更影响 | 无，快照保证计算稳定 |
| 单次输入条目＞50条 | 自动切分多批次串行处理，逐批返回结果 | 无，内置分批逻辑自动执行 |
| 提炼中途收到全局熔断指令 | 立即终止当前批次计算，丢弃未完成规则，不输出回执 | 下发RESUME恢复指令，等待下一次提炼触发 |

## 九、内部调度总线访问契约
| 总线方向 | 消息类型 | 访问权限 | 通信双方 |
|--------|----------|----------|----------|
| 读（入站） | 提炼触发指令+条目快照集 | 只读 | ag-mem-26 → ag-mem-27 |
| 读（入站） | 全局调度熔断/恢复指令 | 只读 | ag-mem-01 → ag-mem-27 |
| 写（出站） | 提炼完成回执、数据不足通知 | 模块专属写入 | ag-mem-27 → ag-mem-26 |
| 写（出站） | 通用规则列表 | 模块专属写入 | ag-mem-27 → ag-mem-26 |
| 写（出站） | 高置信规则推送 | 模块专属写入 | ag-mem-27 → ag-mem-28 |
| 写（出站） | 提炼批次统计上报 | 事件触发写入 | ag-mem-27 → ag-mem-03 |

## 十、强制安全边界（不可绕过）
| 编号 | 约束规则 |
|:---:|------|
| S-01 | 提炼仅读取脱敏后的特征向量、标准化工具序列，禁止访问原始用户输入、隐私字段 |
| S-02 | 输出通用规则必须剔除所有个性化参数，仅保留通用任务模板、工具流程、特征范式 |
| S-03 | 严格执行最小3条条目门槛，禁止用1/2条低样本数据强行生成规则输出 |
| S-04 | 置信度≥0.85的高置信规则推送至L5前，必须交由ag-mem-45安全规则库完成合规校验（上游链路强制约束） |
| S-05 | 输入统一使用条目快照副本，提炼计算全程隔离原始存储条目，并发修改不干扰结果稳定性 |

## 十一、自动化功能测试用例全覆盖
| 用例编号 | 前置条件 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M27-01 | IDLE，5条高度同类条目，序列/特征均达标 | 提炼指令+5条快照条目 | 生成1条通用规则，置信度≥0.7，回执正常返回 |
| TC-M27-02 | IDLE，仅2条同类条目 | 提炼指令+2条快照条目 | 返回数据不足通知，原因：条目数不足 |
| TC-M27-03 | IDLE，5条条目工具序列差异极大 | 提炼指令+5条快照条目 | 返回数据不足通知，标注序列一致性不足 |
| TC-M27-04 | IDLE，5条条目任务特征向量差异大 | 提炼指令+5条快照条目 | 返回数据不足通知，标注特征共性不显著 |
| TC-M27-05 | IDLE，20条高度统一同类条目 | 提炼指令+20条快照条目 | 生成高置信规则conf≥0.85，同步推送ag-mem-28 |
| TC-M27-06 | IDLE，输入55条同类条目 | 提炼指令+55条快照条目 | 自动拆分为两批（50+5），分批次输出各自规则与回执 |

## 十二、交付验收自检清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二层五层存储层级定位准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 6种内部状态+完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级 | ✅ |
| 提炼前置阈值、算法流程、置信度计算公式完整可复现 | ✅ |
| 伪代码覆盖分批处理、LCS序列匹配、K-Means聚类、置信度计算、规则组装、L5推送全链路 | ✅ |
| 异常场景覆盖7类典型故障处理逻辑 | ✅ |
| 内部调度总线读写权限划分清晰 | ✅ |
| 5条强制安全约束无逻辑漏洞 | ✅ |
| 6条测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充说明（对接ag-mem-26 / ag-mem-28）
1. ag-mem-26 定量（20条）、定时（72h）两类场景下发提炼任务，每次附带**条目快照**，避免并发读写冲突；
2. 本单元仅负责规则生成，无持久化存储能力，规则全部回传给ag-mem-26绑定至原始L4条目；
3. 仅高置信（≥0.85）规则可选推送L5，推送链路前置强制安全合规校验；
4. 提炼不修改任何原始条目数据，仅产出派生通用规则，原始经验完整保留；
5. 所有输入数据均为L4去个性化脱敏后数据，本单元不再重复隐私清洗。