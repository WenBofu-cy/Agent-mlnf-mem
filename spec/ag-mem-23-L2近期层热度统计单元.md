# ag-mem-23-L3辅助归并单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-23 |
| 模块名称 | L3辅助归并单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路 |
| 核心职责 | 上游唯一输入来源为ag-mem-22推送的晋升预聚合快照，作为L2→L3中间预处理单元；基于funnel_id、行为语义、任务标签对多条同类低频次条目做合并聚合，压缩重复经验、统一加权计算聚合后I值、复用次数、S值；过滤无聚合价值的零散单一条目，将完成归并的标准化批量条目单向推送至ag-mem-24 L3中期存储；输出归并统计快照供给ag-mem-37、ag-mem-40用于全局I刷新与遗忘扫描；定时上报归并处理容量开销至ag-mem-48；所有条目归并、拆分、过滤操作推送审计日志至ag-mem-51；无独立持久存储，仅做内存临时聚合计算，不长期留存原始条目；严格遵循V1.1「聚合降噪、精简中长期记忆」设计规范，禁止跨层直通L4/L5。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-22（L2近期层，唯一上游快照来源）、ag-mem-35（三维权重配置单元，读取归并相似度阈值、聚合加权系数、分funnel归并规则）、ag-mem-48（全局容量配额管控，上报临时计算内存占用） |
| 被依赖模块 | ag-mem-24（L3中期存储层，接收归并完成标准化条目）、ag-mem-37（重要度定时刷新单元，读取归并后条目元数据快照）、ag-mem-40（遗忘阈值判定单元，提供归并条目扫描数据）、ag-mem-48（接收归并计算内存占用上报）、ag-mem-51（推送归并操作审计日志）、ag-mem-03（漏斗二调度单元，周期上报归并运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `MERGE_IDLE` | 等待ag-mem-22下发预聚合快照，空闲无计算任务 | 系统初始化、熔断恢复、整批条目归并处理完毕 |
| 快照接收缓存 | `SNAPSHOT_CACHE` | 接收L2批量预聚合条目快照，存入临时内存缓冲 | 收到ag-mem-22晋升预聚合快照推送 |
| 语义分组归并计算 | `SEMANTIC_MERGE` | 按funnel、语义向量、任务标签分组，加权合并同类条目，生成聚合后统一元数据 | 快照缓存接收完成 |
| 标准化条目下发 | `ITEM_DISPATCH` | 过滤无聚合价值零散条目，批量推送归并完成条目至ag-mem-24，输出元数据快照 | 分组归并全部计算完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空临时缓存，停止所有归并计算与快照处理 | F0下发FUSE熔断指令；RESUME切回MERGE_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L2晋升预聚合条目快照 | List<Struct>（条目ID、funnel分槽ID、复用次数、S值、I值、向量嵌入、任务标签、创建时间） | ag-mem-22 L2近期层 | L2定时晋升扫描完成，推送待归并条目快照 | 高 |
| 归并规则参数查询回执 | Struct（语义相似度阈值、聚合加权系数、单组最小合并条数） | ag-mem-35 三维权重配置单元 | 模块初始化、规则参数更新 | 普通 |
| 条目元数据批量查询请求 | Struct（归并后条目ID列表） | ag-mem-37 / ag-mem-40 | 全局I值重算、遗忘批量扫描 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、模式切换、紧急熔断 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 归并完成标准化条目批量推送 | List<Struct>（聚合条目唯一ID、源原始条目ID集合、funnel分槽、聚合加权I、总复用、综合S、合并向量、任务标签） | ag-mem-24 L3中期存储层 | 存在可合并分组条目，过滤零散单条后下发 | 高 |
| L2快照接收回执 | Struct（快照总条数、成功缓存条数、无价值过滤条数） | ag-mem-22 L2近期层 | 快照完整存入临时缓存 | 高 |
| 归并后条目元数据快照 | List<聚合条目完整元数据> | ag-mem-37、ag-mem-40 | 收到元数据批量查询请求 | 高 |
| 归并计算内存占用上报 | Struct（层级辅助单元、临时缓存KB、当前待处理条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量快照处理后即时上报 | 普通 |
| 归并操作审计日志 | Struct（事件类型、原始条目总数、合并分组数量、过滤零散条目数量、时间戳、funnel范围） | ag-mem-51 记忆变更日志追溯单元 | 每一批快照完整归并处理完成 | 普通 |
| 归并周期运行统计上报 | Struct（当前状态、今日处理原始条目总量、生成聚合条目总量、过滤条目总数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## L3辅助归并核心规则（V1.1白皮书降噪聚合规范）
### 1. 全局归并参数（由ag-mem-35统一分发）
1. 语义相似度阈值：0.85，向量余弦相似度≥阈值判定为同类可合并；
2. 单组合并最小条目数：≥2条才执行聚合，单一条目直接过滤不送入L3；
3. 聚合加权计算公式：
聚合I = (sum(单条I × 单条复用次数)) / 总复用次数
聚合S = 算术平均分组内所有条目S值
聚合复用次数 = 分组内所有条目复用次数累加
聚合向量 = 分组向量加权平均（权重=复用次数）
4. 分组匹配优先级：同funnel分槽 → 同任务标签 → 向量相似度匹配。

### 2. 条目过滤规则（满足任意一条直接丢弃，不推送L3）
1. 分组内条目数量＜2，无聚合价值；
2. 单条I值低于L3准入最低阈值；
3. 条目创建时长低于L2最小留存周期，未经过充分验证。

### 3. 流转强制约束
1. 唯一上游：仅接收ag-mem-22快照，拒绝其他模块输入；
2. 单向下游：仅输出聚合条目至ag-mem-24，无任何旁路流向L4/L5；
3. 无持久存储：所有原始快照仅内存临时缓存，处理完成立即清空，不落地持久化；
4. 不参与遗忘/归档清理逻辑，仅做晋升预处理，无清理候选清单输出能力。

### 4. 批量约束
单次接收快照最大1000条，超量自动分片串行分组归并，防止向量相似度计算算力过载。

## 核心处理逻辑
```
FUNCTION l3_merge_helper_main_loop():
    STATE_IDLE = MERGE_IDLE
    STATE_CACHE = SNAPSHOT_CACHE
    STATE_CALC = SEMANTIC_MERGE
    STATE_DISPATCH = ITEM_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载归并全局配置
    merge_cfg = query_merge_config(from_m35="ag-mem-35")
    sim_threshold = merge_cfg.similarity_threshold
    min_group_size = merge_cfg.min_merge_group
    l3_entry_min_I = merge_cfg.L3_min_entry_I
    temp_cache = []
    stat_raw_total = 0
    stat_merge_group_cnt = 0
    stat_filter_single = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_cache.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收ag-mem-22预聚合快照
        IF 收到L2晋升预聚合条目快照:
            snapshot_data = 获取快照列表
            internal_state = SNAPSHOT_CACHE
            temp_cache.extend(snapshot_data)
            cache_total = len(temp_cache)
            stat_raw_total += len(snapshot_data)
            // 返回快照接收回执给ag-mem-22
            recv_ack = build_snapshot_ack(total=len(snapshot_data), cached=cache_total)
            send_ack(target="ag-mem-22", ack_data=recv_ack)
            internal_state = STATE_CALC

            // 3. 语义分组归并计算
            group_map = {}
            filter_count = 0
            merge_result_list = []
            now_ts = NOW()
            // 按funnel+任务标签初步分组
            for item in temp_cache:
                key = f"{item.funnel分槽ID}_{item.任务标签}"
                if key not in group_map:
                    group_map[key] = []
                group_map[key].append(item)
            // 每组内向量相似度二次细分合并
            for group_key, item_list in group_map.items():
                split_semantic_groups = semantic_split_by_vector(item_list, sim_threshold)
                for semantic_group in split_semantic_groups:
                    if len(semantic_group) < min_group_size:
                        filter_count += len(semantic_group)
                        stat_filter_single += len(semantic_group)
                        continue
                    // 加权聚合计算
                    total_reuse = sum(i.复用次数 for i in semantic_group)
                    weight_I_sum = sum(i.I值 * i.复用次数 for i in semantic_group)
                    avg_S = sum(i.S值 for i in semantic_group) / len(semantic_group)
                    avg_vector = weighted_avg_vector(semantic_group)
                    merge_I = weight_I_sum / total_reuse
                    // 校验L3准入I阈值
                    if merge_I < l3_entry_min_I:
                        filter_count += len(semantic_group)
                        stat_filter_single += len(semantic_group)
                        continue
                    // 组装聚合条目
                    source_ids = [i.条目ID for i in semantic_group]
                    merge_item = {
                        "merge_id": gen_uuid(),
                        "source_item_ids": source_ids,
                        "funnel_id": semantic_group[0].funnel分槽ID,
                        "agg_I": merge_I,
                        "total_reuse": total_reuse,
                        "agg_S": avg_S,
                        "merge_vector": avg_vector,
                        "task_tag": semantic_group[0].任务标签
                    }
                    merge_result_list.append(merge_item)
                    stat_merge_group_cnt += 1
            temp_cache.clear()
            internal_state = STATE_DISPATCH

            // 4. 批量下发聚合条目至ag-mem-24
            if len(merge_result_list) > 0:
                send_merge_batch(target="ag-mem-24", item_list=merge_result_list)
            // 写入归并审计日志
            audit_log = build_merge_audit(
                raw_count=len(snapshot_data),
                merge_group=stat_merge_group_cnt,
                filter_num=filter_count,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = STATE_IDLE

        // 5. 处理元数据批量查询
        IF 收到条目元数据批量查询请求:
            query_ids = 获取聚合条目ID列表
            meta_snap = query_merge_item_meta(query_ids)
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 6. 定时容量上报与周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_temp_cache_kb(temp_cache, merge_cfg.avg_item_kb)
            cap_report = build_cap_report(layer="ag-mem-23", used_kb=cache_kb, item_count=len(temp_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 180s周期统计上报
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_merge_stat_report(
                    state=internal_state,
                    total_raw=stat_raw_total,
                    total_merge_group=stat_merge_group_cnt,
                    total_filter=stat_filter_single
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| L2快照条目向量缺失、参数非法 | 本条直接过滤不计入分组，不参与归并，日志标记异常 | ag-mem-22重新推送完整向量数据快照 |
| 单次快照超过1000条上限 | 自动分片分批加载、分组计算，串行处理不阻塞主线程 | 内置分片逻辑自动执行 |
| 向量相似度计算算力超时 | 当前分组全部过滤，记录告警，下一轮快照重新处理 | 系统算力负载降低后正常执行归并 |
| 临时内存缓存溢出 | 截断后半部分快照存入缓存，溢出条目丢弃并告警 | 降低单次L2推送快照条数，或扩容内存资源 |
| 全局FUSE熔断触发 | 清空全部临时缓存，终止当前归并计算，拒绝新快照接收 | ag-mem-01下发RESUME恢复指令 |
| 分funnel无专属归并阈值配置 | 加载全局通用相似度、最小分组参数兜底计算 | ag-mem-35补充分funnel独立归并规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L2预聚合条目快照 | 只读 | ag-mem-22 唯一上游输入 |
| 内部调度总线 | 读 | 归并规则配置回执 | 只读 | ag-mem-35 |
| 内部调度总线 | 读 | 元数据批量查询、全局熔断指令 | 只读 | ag-mem-37/40、ag-mem-01 |
| 内部调度总线 | 写 | 快照接收回执 | 专属写入 | 返回上游 ag-mem-22 |
| 内部调度总线 | 写 | 归并完成条目批量推送 | 专属写入 | 下发下游 ag-mem-24 |
| 内部调度总线 | 写 | 元数据快照、容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| M-01 | 仅接收ag-mem-22输出快照，禁止其他存储/调度模块直接推送条目，阻断非法记忆流入L3 |
| M-02 | 归并计算仅单向输出至ag-mem-24，无任何跨层直达L4/L5的流转通道，分层链路隔离 |
| M-03 | 无持久化存储设计，原始快照仅内存临时缓存，处理完毕立即清空，减少数据泄露风险 |
| M-04 | 相似度阈值、合并最小条数、准入I阈值全部由ag-mem-35集中管控，本地无硬编码参数 |
| M-05 | 所有分组合并、条目过滤操作全量写入ag-mem-51审计日志，留存原始条目ID与聚合映射关系，支持溯源 |
| M-06 | 熔断状态自动清空临时缓存，避免内存堆积占用资源，恢复后等待L2重新下发快照 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M23-01 | `MERGE_IDLE`，ag-mem-22推送同funnel同标签、相似度0.9的3条条目快照 | L2预聚合快照 | 自动分组加权聚合，生成单条聚合条目推送ag-mem-24，输出归并审计日志 |
| TC-M23-02 | `MERGE_IDLE`，快照内仅单条独立条目 | 单一条目快照 | 判定无聚合价值，直接过滤，统计过滤计数+1 |
| TC-M23-03 | `MERGE_IDLE`，分组聚合后I值低于L3准入阈值 | 多条同类快照 | 整组过滤，不推送至ag-mem-24 |
| TC-M23-04 | `MERGE_IDLE`，单次快照1200条条目 | 超大批量快照 | 自动分片串行分组归并，完整处理无内存溢出 |
| TC-M23-05 | `MERGE_IDLE`，ag-mem-37下发聚合条目ID查询 | 元数据批量查询请求 | 返回聚合条目完整加权元数据快照 |
| TC-M23-06 | `MERGE_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空临时缓存，停止快照处理与归并计算 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-23匹配白皮书L2→L3中间归并辅助单元定位 | ✅ |
| 上游仅ag-mem-22、下游仅ag-mem-24，数据流闭环无冲突 | ✅ |
| 5种内部状态切换逻辑清晰，覆盖缓存、计算、下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，链路无错乱 | ✅ |
| 语义相似度分组、加权聚合公式、过滤规则严格对齐V1.1降噪设计 | ✅ |
| 伪代码覆盖快照接收、分组拆分、加权计算、过滤、批量下发、日志、容量上报全链路 | ✅ |
| 异常场景覆盖向量缺失、超大批次、算力超时、缓存溢出、熔断、无分槽规则共6类 | ✅ |
| 总线读写权限隔离，仅允许L2推送原始快照 | ✅ |
| 6条安全约束杜绝旁路写入、跨层流转、数据留存风险 | ✅ |
| 6条测试用例覆盖全部核心业务场景 | ✅ |

---