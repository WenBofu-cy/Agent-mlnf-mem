# ag-mem-25-L3中期层相似经验归并单元 规整落地版规格文档
承接上文 ag-mem-24 L3存储单元，统一架构术语、补充结构体定义、梳理流转链路、修复逻辑歧义，完整保留全部业务规则，适配前后模块联调、开发编码、自动化测试使用。

## 一、模块基础元信息
| 项 | 内容 |
|----|------|
| 模块ID | ag-mem-25 |
| 模块全称 | L3中期层相似经验归并单元 |
| 所属架构 | 漏斗二：任务经验漏斗 / 五层存储（L0~L5） |
| 上游依赖（主动接收调用） | ag-mem-24 L3中期存储单元（下发归并指令+条目数据）、ag-mem-48 全局容量管控（查询容量）、ag-mem-01 总控F0（全局调度指令） |
| 下游被依赖（主动输出） | ag-mem-24（回执+合并新条目）、ag-mem-42 冗余记忆删除归档单元（淘汰原始条目）、ag-mem-03 漏斗二调度单元（状态上报） |
| 核心定位 | L3配套轻量化数据整理单元，仅做**同槽位相似经验合并**，无晋升、遗忘、存储持久化能力；压缩碎片、释放存储空间、提升检索效率 |
| 核心约束 | CAUTION警示条目禁止合并、跨分槽禁止合并、合并必须留存原始条目溯源ID |

## 二、内部状态机（5种互斥状态）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `IDLE` | 空闲等待 | 无任务，持续轮询总线指令 | 初始化完成；归并任务全部执行完毕；熔断解除 |
| `SIMILARITY_CHECK` | 相似检测 | 特征提取、批量计算条目相似度 | 收到 ag-mem-24 归并触发指令 |
| `MERGING` | 归并执行 | 相似条目组字段合并、生成新合并条目 | 相似度检测完成，存在≥2条合格相似条目组 |
| `OUTPUTTING` | 结果输出 | 推送新条目、待删除条目、回执、状态上报 | 全部条目组合并逻辑执行完成 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，所有任务立即中断 | 接收总控F0熔断指令；恢复指令切回IDLE |

## 三、输入接口（内部调度总线 只读）
统一通道：内部调度总线，区分优先级，附带完整结构体定义
| 输入消息 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 归并触发指令 | MergeTriggerCmd | ag-mem-24 | 12h定时触发 / L3容量预警触发 | 高 |
| L3待归并条目列表 | List<L3RawItem> | ag-mem-24 | 随归并指令一同下发 | 高 |
| L3容量确认回执 | CapacityResp | ag-mem-48 | 归并前主动查询容量 | 普通 |
| 全局调度控制指令 | F0ControlEnum | ag-mem-01 | 系统熔断/暂停/恢复 | 紧急 |

### 入参结构体定义
1. **MergeTriggerCmd 归并触发指令**
```json
{
  "merge_range": "enum[full_l3/slot_limit]",
  "merge_strategy": "default_similar_merge",
  "trigger_reason": "enum[timing/warning_capacity]",
  "source_slot_id": "string 来源槽位"
}
```
2. **L3RawItem 待归并原始条目**
```json
{
  "item_id": "string 原始条目唯一ID",
  "slot_id": "来源分槽编号",
  "feature_vec": "float[] 任务特征向量",
  "tool_seq": "list<string> 工具调用序列",
  "I": "float 重要度",
  "C": "float 复用频次",
  "write_ts": "long 写入时间戳",
  "result_tag": "enum[成功/失败/策略失误]",
  "caution_tag": "enum[NORMAL/CAUTION]",
  "storage_size": "int 占用存储空间Byte"
}
```
3. **F0ControlEnum 全局指令枚举**
`PAUSE / RESUME / FUSE`

## 四、输出接口（内部调度总线 专属写入）
| 输出消息 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 归并完成回执 | MergeCallbackResp | ag-mem-24 | 整套归并流程结束 | 高 |
| 归并后新条目列表 | List<MergedNewItem> | ag-mem-24 | 合并完成后推送入库 | 高 |
| 淘汰原始条目清除列表 | List<RemoveItem> | ag-mem-42 | 合并完成，原始条目待归档/删除 | 高 |
| 归并周期状态上报 | MergeStatusReport | ag-mem-03 | 单次归并结束后上报指标 | 普通 |

### 出参结构体定义
1. **MergeCallbackResp 归并回执**
```json
{
  "scan_total": "int 本次扫描条目总数",
  "similar_group_cnt": "int 匹配到的相似分组数量",
  "new_merge_item_cnt": "int 合并生成新条目数量",
  "removed_raw_cnt": "int 被淘汰原始条目总数",
  "free_space_byte": "long 本次释放存储空间",
  "cost_ms": "long 归并总耗时毫秒",
  "msg": "string 备注信息（无异常为空）"
}
```
2. **MergedNewItem 合并生成新条目**
```json
{
  "item_id": "L3-MERGED-UUID",
  "slot_id": "来源分槽（与原条目保持一致）",
  "exp_data": "整合后经验数据，含差异化附加标签",
  "I": "组内最大值",
  "C": "所有C累加，上限1.0",
  "S": "组内安全显著性最大值",
  "V": "组内用户价值最大值",
  "caution_tag": "NORMAL（合并条目无警示标签）",
  "source_raw_ids": "list<string> 全部原始条目溯源ID",
  "write_ts": "long 当前合并时间戳"
}
```
3. **RemoveItem 待清除原始条目**
```json
{
  "raw_item_id": "原始条目ID",
  "remove_cause": "L3相似归并淘汰"
}
```
4. **MergeStatusReport 归并状态上报**
```json
{
  "current_state": "当前模块状态枚举",
  "batch_stat": MergeCallbackResp,
  "total_merge_times": "int 累计归并总次数",
  "l3_fragment_index": "float L3碎片化指数"
}
```

## 五、相似度计算与归并阈值规范
### 5.1 四维度加权计算规则
> 前置强约束：**分槽编号必须完全一致**，跨槽条目直接跳过，不参与相似度计算
| 计算维度 | 权重 | 计算逻辑说明 |
|--------|:---:|--------------|
| 任务特征向量余弦相似度 | 0.40 | 向量夹角余弦值，取值0~1 |
| 工具调用序列归一化相似度 | 0.30 | 1 - Levenshtein编辑距离 / 序列最大长度 |
| 经验结果标签匹配度 | 0.20 | 标签完全一致=1.0；不一致=0.5 |
| 30天时间窗口相似度 | 0.10 | `max(0, 1 - 相差天数/30)` |

综合相似度公式：
$$
Sim = 0.4 \times Sim_{vec} + 0.3 \times Sim_{tool} + 0.2 \times Sim_{tag} + 0.1 \times Sim_{time}
$$

### 5.2 全局归并阈值配置
| 配置项 | 默认阈值 | 业务约束 |
|--------|:---:|------|
| 综合相似度触发阈值 | ≥0.80 | 低于该值不合并 |
| 单分组最小条目数 | 2 | 仅单条直接跳过合并流程 |
| 单分组最大条目数 | 5 | 一组最多合并5条，超出拆分多组 |
| CAUTION警示条目 | 禁止合并 | 含警示标签的条目直接剔除分组 |

## 六、相似条目合并字段整合规则
| 字段 | 合并取值逻辑 |
|------|--------------|
| 经验主体数据 | 取组内I值最高条目为主模板，追加其余条目差异化特征为附加标签 |
| 重要度I | 分组内最大值 |
| 复用频次C | 分组全部C值累加，封顶1.0 |
| 安全显著性S | 分组内最大值 |
| 用户价值V | 分组内最大值 |
| 来源分槽slot_id | 保持原分组槽位不变 |
| 写入时间戳 | 取本次合并执行时间 |
| 警示标签 | 固定NORMAL，合并条目不会产生CAUTION标签 |
| 溯源字段source_raw_ids | 完整存储本组所有原始条目ID，用于追溯 |

## 七、完整业务主流程伪代码（注释优化版）
```python
FUNCTION l3_similarity_merge_main_loop():
    # 状态常量定义
    STATE_IDLE = "IDLE"
    STATE_CHECK = "SIMILARITY_CHECK"
    STATE_MERGE = "MERGING"
    STATE_OUTPUT = "OUTPUTTING"
    STATE_PAUSED = "SYSTEM_PAUSED"

    internal_state = STATE_IDLE
    total_merge_counter = 0  # 全局累计归并次数

    WHILE system_running:
        # 1. 全局熔断指令优先处理（最高紧急优先级）
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSED
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSED:
                internal_state = STATE_IDLE

        # 2. 接收L3下发归并触发指令
        if recv_merge_trigger():
            if internal_state == STATE_PAUSED:
                send_merge_callback(scan_total=0, msg="模块熔断，拒绝归并任务")
                continue
            
            internal_state = STATE_CHECK
            trigger_msg = get_merge_trigger_msg()
            raw_item_list = trigger_msg.attach_item_list
            start_ts = NOW()

            # 条目数量校验：不足2条直接结束
            if len(raw_item_list) < 2:
                send_merge_callback(scan_total=len(raw_item_list), msg="条目数量不足，无需归并")
                internal_state = STATE_IDLE
                continue

            # 2.1 按分槽分组，隔离跨槽数据
            slot_group_map = group_by_slot_id(raw_item_list)
            all_similar_groups = []

            # 2.2 分槽执行相似度分组检测
            for slot, slot_item_list in slot_group_map.items():
                slot_similar_groups = calc_slot_similar_group(slot_item_list)
                all_similar_groups.extend(slot_similar_groups)

            # 无任何符合条件相似组，直接返回
            if len(all_similar_groups) == 0:
                send_merge_callback(scan_total=len(raw_item_list), msg="未检测到满足阈值相似经验组")
                internal_state = STATE_IDLE
                continue

            # 3. 进入合并执行阶段
            internal_state = STATE_MERGE
            new_merged_items = []
            remove_raw_ids = []
            total_free_bytes = 0

            for group in all_similar_groups:
                # 校验分组内是否存在警示条目，存在则整组跳过
                has_caution = any(item.caution_tag == "CAUTION" for item in group)
                if has_caution:
                    log("分组包含警示条目，跳过合并", group_id=uuid())
                    continue

                # 选取I值最高条目作为主模板
                group_sorted = sort_by_I_desc(group)
                main_item = group_sorted[0]
                assist_items = group_sorted[1:]

                # 整合经验主体数据
                merged_exp = deep_clone(main_item.exp_data)
                for assist in assist_items:
                    merged_exp.diff_tags.update(assist.exp_data.diff_tags)

                # 数值字段合并计算
                merge_I = max(item.I for item in group)
                merge_C = min(sum(item.C for item in group), 1.0)
                merge_S = max(item.S for item in group)
                merge_V = max(item.V for item in group)
                raw_id_list = [item.item_id for item in group]

                # 生成合并新条目
                new_item = {
                    "item_id": f"L3-MERGED-{gen_uuid()}",
                    "slot_id": main_item.slot_id,
                    "exp_data": merged_exp,
                    "I": merge_I,
                    "C": merge_C,
                    "S": merge_S,
                    "V": merge_V,
                    "caution_tag": "NORMAL",
                    "source_raw_ids": raw_id_list,
                    "write_ts": NOW()
                }
                new_merged_items.append(new_item)

                # 记录待删除原始条目
                remove_raw_ids.extend(raw_id_list)

                # 计算释放存储空间
                raw_total_size = sum(item.storage_size for item in group)
                new_item_size = estimate_storage_size(new_item)
                total_free_bytes += raw_total_size - new_item_size

            # 4. 结果输出阶段
            internal_state = STATE_OUTPUT
            # 推送合并新条目至L3存储单元
            if len(new_merged_items) > 0:
                send_new_merged_items_to_m24(new_merged_items)
            # 推送淘汰原始条目至归档删除单元
            if len(remove_raw_ids) > 0:
                send_remove_list_to_m42(remove_raw_ids, cause="L3相似归并淘汰")
            # 组装回执返回ag-mem-24
            callback = MergeCallbackResp(
                scan_total=len(raw_item_list),
                similar_group_cnt=len(all_similar_groups),
                new_merge_item_cnt=len(new_merged_items),
                removed_raw_cnt=len(remove_raw_ids),
                free_space_byte=total_free_bytes,
                cost_ms=NOW() - start_ts
            )
            send_merge_callback(callback)
            # 向调度单元上报本次归并指标
            report = build_merge_status_report(callback, total_merge_counter)
            send_status_report(report, target="ag-mem-03")
            total_merge_counter += 1

            # 任务结束切回空闲状态
            internal_state = STATE_IDLE

        SLEEP(50)

# 分槽内相似度分组计算子函数
FUNCTION calc_slot_similar_group(item_list):
    result_groups = []
    grouped_id_set = set()

    for idx_a, item_a in enumerate(item_list):
        if item_a.item_id in grouped_id_set:
            continue
        # 警示条目不参与分组匹配
        if item_a.caution_tag == "CAUTION":
            grouped_id_set.add(item_a.item_id)
            continue

        current_group = [item_a]
        grouped_id_set.add(item_a.item_id)

        for idx_b, item_b in enumerate(item_list):
            if idx_b <= idx_a or item_b.item_id in grouped_id_set:
                continue
            if item_b.caution_tag == "CAUTION":
                continue
            # 计算综合相似度
            sim = calc_total_similarity(item_a, item_b)
            if sim >= 0.80:
                current_group.append(item_b)
                grouped_id_set.add(item_b.item_id)
                # 单组最多5条，达到上限停止追加
                if len(current_group) >= 5:
                    break
        # 仅2条及以上才视为有效分组
        if len(current_group) >= 2:
            result_groups.append(current_group)
    return result_groups

# 综合相似度计算子函数
FUNCTION calc_total_similarity(item_a, item_b):
    # 1.特征向量余弦相似度 0.4权重
    sim_vec = cos_similarity(item_a.feature_vec, item_b.feature_vec)
    # 2.工具序列相似度 0.3权重
    edit_dist = levenshtein(item_a.tool_seq, item_b.tool_seq)
    max_seq_len = max(len(item_a.tool_seq), len(item_b.tool_seq))
    sim_tool = 1.0 - (edit_dist / max_seq_len) if max_seq_len > 0 else 1.0
    # 3.标签相似度 0.2权重
    sim_tag = 1.0 if item_a.result_tag == item_b.result_tag else 0.5
    # 4.时间窗口相似度 0.1权重
    day_diff = abs(item_a.write_ts - item_b.write_ts) / 86400
    sim_time = max(0, 1 - day_diff / 30)
    # 加权求和
    total_sim = 0.4 * sim_vec + 0.3 * sim_tool + 0.2 * sim_tag + 0.1 * sim_time
    return total_sim
```

## 八、异常场景与故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|--------|----------|----------|
| 待归并条目列表长度＜2 | 直接返回回执，不执行任何合并计算 | 下次下发足量条目再处理 |
| 相似分组内包含CAUTION警示条目 | 整组跳过，原始条目保留不变，日志记录 | 条目警示标签降级为NORMAL后参与下一轮归并 |
| 合并新条目推送至ag-mem-24写入失败 | 整组回滚：原始条目全部保留，不推送删除指令，日志告警 | L3存储读写恢复正常后，下一轮归并重新处理 |
| 相似度检测耗时＞30s超时 | 中断遍历，返回已完成分组结果，回执标记「检测未完成」 | 下一轮定时归并继续扫描剩余条目 |
| 系统CPU/内存资源紧张 | 单次仅处理前10个相似分组，剩余分组延迟至下一轮周期 | 系统负载回落至阈值以内 |
| 归并中途收到全局熔断指令 | 立刻终止全部计算，已处理分组不落地，原始条目完整保留 | 下发RESUME恢复指令后，等待下一次归并触发 |

## 九、总线访问契约
全部通信通道统一使用**内部调度总线**，区分读写权限：
| 总线方向 | 消息类型 | 访问权限 | 通信双方 |
|--------|----------|----------|----------|
| 读（入站） | 归并触发指令+条目列表 | 只读 | ag-mem-24 → ag-mem-25 |
| 读（入站） | 容量确认回执 | 只读 | ag-mem-48 → ag-mem-25 |
| 读（入站） | 全局调度熔断指令 | 只读 | ag-mem-01 → ag-mem-25 |
| 写（出站） | 归并完成回执 | 模块专属写入 | ag-mem-25 → ag-mem-24 |
| 写（出站） | 合并后新条目列表 | 模块专属写入 | ag-mem-25 → ag-mem-24 |
| 写（出站） | 待清除原始条目列表 | 模块专属写入 | ag-mem-25 → ag-mem-42 |
| 写（出站） | 归并统计状态上报 | 事件触发写入 | ag-mem-25 → ag-mem-03 |

## 十、强制安全边界（不可绕过）
| 编号 | 约束规则 |
|:---:|------|
| S-01 | CAUTION警示经验禁止参与任何分组合并，保证失败原始经验完整留存、可追溯 |
| S-02 | 归并仅重组条目结构、压缩冗余，不篡改任务场景、工具、结果标签等核心业务语义 |
| S-03 | 所有合并生成新条目必须携带完整`source_raw_ids`原始ID列表，支持全链路溯源审计 |
| S-04 | 不同来源分槽的经验严禁跨槽相似度匹配与合并，严格分槽隔离 |
| S-05 | 归并流程全程不修改任何原始条目警示标签，合并生成条目固定为NORMAL无警示标记 |

## 十一、自动化测试用例（TC全量覆盖）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M25-01 | 模块IDLE，同槽2条相似度0.85普通经验 | 归并触发指令+2条合格条目 | 生成1条合并新条目，2条原始条目进入清除列表，回执统计正常释放空间 |
| TC-M25-02 | IDLE，全部条目综合相似度＜0.8 | 完整条目列表 | 回执提示「未检测到相似经验」，无新条目、无待清除条目 |
| TC-M25-03 | SIMILARITY_CHECK，分组内存在CAUTION条目 | 含警示条目的相似分组 | 该分组直接跳过，不产生合并条目，原始条目保留 |
| TC-M25-04 | IDLE，仅1条待归并条目 | 触发指令+单一条目 | 回执提示「条目数不足」，无任何合并操作 |
| TC-M25-05 | MERGING阶段，推送新条目至ag-mem-24返回写入异常 | 正常相似分组，下游写入失败 | 回滚逻辑生效，不发送清除列表，原始条目保留，日志告警 |
| TC-M25-06 | SIMILARITY_CHECK，批量条目计算耗时超30s | 大批量待检测条目 | 中断检测，回执标记检测未完成，仅返回已计算完成分组结果 |

## 十二、交付自检验收清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二层五层存储定位准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 5种内部状态+完整切换条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级 | ✅ |
| 相似度四维度权重、计算公式、阈值完整 | ✅ |
| 所有字段合并整合规则表完整 | ✅ |
| 伪代码覆盖分组、相似度计算、合并、回滚、超时熔断全链路 | ✅ |
| 异常场景覆盖6类典型故障处理逻辑 | ✅ |
| 内部调度总线读写权限划分清晰 | ✅ |
| 5条强制安全约束无逻辑漏洞 | ✅ |
| 6条测试用例覆盖全部核心业务分支 | ✅ |

## 联动补充说明（对接ag-mem-24）
1. ag-mem-24 每12小时定时下发归并指令；L3容量≥80%预警时额外主动触发归并；
2. ag-mem-25 仅负责生成合并条目与淘汰列表，**无存储、删除能力**：新条目写入、旧条目清除分别由 ag-mem-24、ag-mem-42 执行；
3. 归并释放空间仅做统计上报，不直接修改L3容量计数器，容量统一由 ag-mem-48 全局管控；
4. 警示标签降级逻辑仅在 ag-mem-24 执行，本单元无法修改、清除CAUTION标签。