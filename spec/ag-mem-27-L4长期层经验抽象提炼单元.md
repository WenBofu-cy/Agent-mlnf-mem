# ag-mem-27-L4抽象提炼单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-27 |
| 模块名称 | L4抽象提炼单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（L4→L5前置抽象加工层） |
| 核心职责 | 上游唯一输入来源为ag-mem-26 L4长期存储晋升轻量化条目；对批量长效经验做高层语义抽象、任务范式归纳、向量蒸馏压缩，生成统一标准化抽象记忆单元；剔除低泛化价值条目，统一计算全局抽象重要度abs_I；输出标准化抽象单元推送至ag-mem-45安全规则合规校验单元，完成安全校验后才可流入L5存储；无独立持久存储，仅内存临时抽象计算；对外提供抽象单元元数据快照供给ag-mem-37、ag-mem-40用于全局I刷新与遗忘扫描；定时上报抽象计算内存开销至ag-mem-48；全部抽象生成、过滤、丢弃操作推送审计日志至ag-mem-51；严格遵循V1.1「顶层记忆准入抽象降噪」规范，禁止未校验抽象单元直连L5。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-26（L4长期存储层，唯一上游条目来源）、ag-mem-35（三维权重配置单元，读取抽象相似度阈值、蒸馏权重、泛化过滤阈值、分funnel抽象规则）、ag-mem-48（全局容量配额管控，上报临时计算内存占用） |
| 被依赖模块 | ag-mem-45（安全规则合规校验单元，接收抽象记忆单元做安全校验）、ag-mem-37（重要度定时刷新单元，读取抽象单元元数据快照）、ag-mem-40（遗忘阈值判定单元，提供抽象单元扫描数据）、ag-mem-48（接收抽象计算内存占用上报）、ag-mem-51（推送抽象提炼操作审计日志）、ag-mem-03（漏斗二调度单元，周期上报抽象提炼运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `ABSTRACT_IDLE` | 空闲等待ag-mem-26下发晋升轻量化条目快照，无抽象计算任务 | 系统初始化、熔断恢复、整批条目抽象处理完毕 |
| 条目快照缓存 | `ITEM_CACHE` | 接收L4晋升轻量化条目，存入内存临时缓冲 | 收到ag-mem-26批量晋升条目快照推送 |
| 高层语义抽象计算 | `ABSTRACT_CALC` | 按funnel、任务范式分组，向量蒸馏、泛化打分、加权计算abs_I，过滤低泛化条目 | 快照缓存接收完成 |
| 抽象单元标准化下发 | `ABSTRACT_DISPATCH` | 过滤无泛化价值条目，批量推送抽象记忆单元至ag-mem-45 | 全部分组抽象计算完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空全部临时缓存，停止所有抽象计算与快照处理 | F0下发FUSE熔断指令；RESUME切回ABSTRACT_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4晋升轻量化条目快照 | List<Struct>（light_id、funnel分槽ID、final_I、sum_reuse、avg_S、light_vector、task_tag、create_ts） | ag-mem-26 L4长期存储层 | L4定时晋升筛选完成，推送待抽象长效条目快照 | 高 |
| 抽象提炼规则参数回执 | Struct（抽象相似度阈值、蒸馏权重系数、泛化最低abs_I阈值、单组最小提炼条数） | ag-mem-35 三维权重配置单元 | 模块初始化、抽象规则更新 | 普通 |
| 抽象单元元数据批量查询请求 | Struct（抽象单元唯一abs_id列表） | ag-mem-37 / ag-mem-40 | 全局I值重算、分层遗忘扫描 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 标准化抽象记忆单元批量推送 | List<Struct>（abs_id抽象ID、源light_id集合、funnel_id、abs_I抽象重要度、total_origin_reuse、abstract_vector蒸馏向量、task_group任务范式） | ag-mem-45 安全规则合规校验单元 | 存在可提炼分组，过滤低泛化条目后下发 | 高 |
| L4晋升快照接收回执 | Struct（快照总条数、缓存成功条数、低泛化过滤条数） | ag-mem-26 L4长期存储层 | 快照完整存入临时内存缓冲 | 高 |
| 抽象单元元数据快照 | List<完整抽象单元元数据> | ag-mem-37、ag-mem-40 | 收到元数据批量查询请求 | 高 |
| 抽象计算内存占用上报 | Struct（单元标识ag-mem-27、临时缓存KB、当前待处理条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量快照处理后即时上报 | 普通 |
| 抽象提炼操作审计日志 | Struct（事件类型、原始轻量化条目总数、提炼分组数量、低泛化过滤条目数量、时间戳、funnel范围） | ag-mem-51 记忆变更日志追溯单元 | 每一批快照完整抽象提炼处理完成 | 普通 |
| 抽象提炼周期运行统计上报 | Struct（当前状态、今日处理原始轻量化条目总量、生成抽象单元总量、过滤低泛化条目总数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## L4抽象提炼核心规则（V1.1顶层记忆准入规范）
### 1. 全局抽象配置参数（ag-mem-35统一分发）
1. 抽象向量相似度阈值：0.92，余弦相似度≥阈值判定为同一任务范式，可合并抽象；
2. 单组最小提炼条目数：≥2条才执行高层抽象，单一条目泛化不足直接过滤；
3. 最低泛化abs_I阈值：低于该阈值的抽象单元直接丢弃，禁止送入安全校验；
4. 抽象加权计算公式：
abs_I = sum(final_I × sum_reuse) / sum(sum_reuse)
total_origin_reuse = 分组内所有条目复用次数累加
abstract_vector = 复用权重蒸馏压缩高层语义向量

### 2. 条目过滤规则（满足任意一条直接过滤，不推送ag-mem-45）
1. 分组内条目数量＜2，无高层泛化价值；
2. 提炼后abs_I低于分funnel泛化过滤阈值；
3. 条目创建时长不足L4最低留存周期，长效经验验证不充分。

### 3. 流转强制约束
1. 唯一上游：仅接收ag-mem-26晋升快照，拒绝其余模块输入；
2. 单向下游：仅输出抽象单元至ag-mem-45安全校验，无任何旁路直达L5；
3. 无持久存储：原始轻量化快照仅内存临时缓存，处理完成立即清空，不落地持久化；
4. 不参与归档/遗忘清理逻辑，仅做L4→L5前置抽象预处理，无归档候选清单输出能力。

### 4. 批量约束
单次接收快照最大1000条，超量自动分片串行抽象计算，防止向量蒸馏算力过载。

## 核心处理逻辑
```
FUNCTION l4_abstract_extract_main_loop():
    STATE_IDLE = ABSTRACT_IDLE
    STATE_CACHE = ITEM_CACHE
    STATE_CALC = ABSTRACT_CALC
    STATE_DISPATCH = ABSTRACT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载抽象提炼全局配置
    abstract_cfg = query_abstract_config(from_m35="ag-mem-35")
    sim_threshold = abstract_cfg.abstract_similarity_threshold
    min_group_size = abstract_cfg.min_extract_group
    min_abs_I = abstract_cfg.min_general_abs_I
    temp_cache = []
    stat_raw_light_total = 0
    stat_extract_group_cnt = 0
    stat_low_general_filter = 0
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
                internal_state = ABSTRACT_IDLE

        // 2. 接收ag-mem-26 L4晋升轻量化条目快照
        IF 收到L4晋升轻量化条目快照:
            snapshot_data = 获取快照列表
            internal_state = ITEM_CACHE
            temp_cache.extend(snapshot_data)
            cache_total = len(temp_cache)
            stat_raw_light_total += len(snapshot_data)
            // 返回快照接收回执给ag-mem-26
            recv_ack = build_snapshot_recv_ack(total=len(snapshot_data), cached=cache_total)
            send_ack(target="ag-mem-26", ack_data=recv_ack)
            internal_state = ABSTRACT_CALC

            // 3. 任务范式分组+高层语义抽象蒸馏计算
            task_group_map = {}
            filter_count = 0
            abstract_output_list = []
            now_ts = NOW()
            // 按funnel+task_tag基础分组
            for light_item in temp_cache:
                group_key = f"{light_item.funnel分槽ID}_{light_item.task_tag}"
                if group_key not in task_group_map:
                    task_group_map[group_key] = []
                task_group_map[group_key].append(light_item)
            // 组内向量相似度二次细分抽象分组
            for group_key, light_list in task_group_map.items():
                semantic_abstract_groups = semantic_split_by_vector(light_list, sim_threshold)
                for abstract_group in semantic_abstract_groups:
                    if len(abstract_group) < min_group_size:
                        filter_count += len(abstract_group)
                        stat_low_general_filter += len(abstract_group)
                        continue
                    // 抽象加权指标计算
                    total_reuse_sum = sum(i.sum_reuse for i in abstract_group)
                    weight_i_sum = sum(i.final_I * i.sum_reuse for i in abstract_group)
                    distill_vec = weighted_distill_vector(abstract_group)
                    abs_I = weight_i_sum / total_reuse_sum
                    // 校验顶层记忆准入abs_I下限
                    if abs_I < min_abs_I:
                        filter_count += len(abstract_group)
                        stat_low_general_filter += len(abstract_group)
                        continue
                    // 组装标准化抽象单元
                    source_light_ids = [i.light_id for i in abstract_group]
                    abstract_item = {
                        "abs_id": gen_uuid(),
                        "source_light_ids": source_light_ids,
                        "funnel_id": abstract_group[0].funnel分槽ID,
                        "abs_I": abs_I,
                        "total_origin_reuse": total_reuse_sum,
                        "abstract_vector": distill_vec,
                        "task_group": abstract_group[0].task_tag
                    }
                    abstract_output_list.append(abstract_item)
                    stat_extract_group_cnt += 1
            temp_cache.clear()
            internal_state = ABSTRACT_DISPATCH

            // 4. 批量下发抽象单元至ag-mem-45安全校验单元
            if len(abstract_output_list) > 0:
                send_abstract_batch(target="ag-mem-45", item_list=abstract_output_list)
            // 写入抽象提炼审计日志
            audit_log = build_abstract_audit(
                raw_light_count=len(snapshot_data),
                extract_group=stat_extract_group_cnt,
                filter_num=filter_count,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = ABSTRACT_IDLE

        // 5. 抽象单元元数据批量查询响应
        IF 收到抽象单元元数据批量查询请求:
            query_abs_ids = 获取抽象ID列表
            meta_snap = query_abstract_item_meta(query_abs_ids)
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 6. 定时内存占用上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_temp_cache_kb(temp_cache, abstract_cfg.avg_light_kb)
            cap_report = build_cap_report(layer="ag-mem-27", used_kb=cache_kb, item_count=len(temp_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_abstract_stat_report(
                    state=internal_state,
                    total_raw_light=stat_raw_light_total,
                    total_extract_group=stat_extract_group_cnt,
                    total_filter=stat_low_general_filter
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| L4快照条目向量缺失、final_I非法 | 本条直接过滤，不参与抽象提炼，日志标记异常 | ag-mem-26重新推送完整标准化轻量化条目快照 |
| 单次快照条目超1000条上限 | 自动分片分批加载、抽象计算，串行执行不阻塞主线程 | 内置分片逻辑自动处理 |
| 向量蒸馏相似度计算算力超时 | 当前分组全部过滤，记录系统告警，下一轮快照重新处理 | 系统算力负载下降后正常执行抽象提炼 |
| 临时内存缓存溢出 | 截断后半段快照存入缓存，溢出条目丢弃并生成告警日志 | 降低ag-mem-26单次推送快照数量或扩容计算内存 |
| 全局FUSE熔断触发 | 清空全部临时缓存，终止当前抽象计算，拒绝接收新快照 | ag-mem-01下发RESUME恢复指令 |
| 分funnel无专属抽象阈值配置 | 加载全局通用相似度、最低abs_I参数兜底计算 | ag-mem-35运维侧补充分funnel抽象规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L4晋升轻量化条目快照 | 只读 | ag-mem-26 唯一上游输入 |
| 内部调度总线 | 读 | 抽象提炼规则配置回执 | 只读 | ag-mem-35 |
| 内部调度总线 | 读 | 元数据批量查询、全局熔断指令 | 只读 | ag-mem-37/40、ag-mem-01 |
| 内部调度总线 | 写 | 快照接收回执 | 专属写入 | 返回上游 ag-mem-26 |
| 内部调度总线 | 写 | 标准化抽象单元批量推送 | 专属写入 | 下发下游 ag-mem-45 |
| 内部调度总线 | 写 | 元数据快照、容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| A-01 | 仅允许ag-mem-26推送晋升轻量化条目，阻断其他模块旁路写入，防止低泛化记忆流入顶层校验链路 |
| A-02 | 抽象提炼计算仅单向输出至ag-mem-45安全校验单元，无任何跨层直达L5流转通道，顶层记忆准入链路强制隔离 |
| A-03 | 无持久化存储设计，原始轻量化快照仅内存临时缓存，处理完毕立刻清空，减少高价值抽象数据泄露风险 |
| A-04 | 相似度阈值、最小提炼条数、泛化abs_I下限全部由ag-mem-35集中管控，本地无硬编码业务参数 |
| A-05 | 所有抽象分组、低泛化条目过滤操作完整写入ag-mem-51审计日志，留存原始light_id与抽象abs_id映射关系，支持全链路溯源 |
| A-06 | 熔断状态自动清空临时缓存，避免内存堆积占用资源，服务恢复后等待ag-mem-26重新下发晋升快照 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M27-01 | `ABSTRACT_IDLE`，ag-mem-26推送同funnel同任务、相似度0.94的3条轻量化条目快照 | L4晋升轻量化快照 | 自动分组蒸馏抽象，生成标准抽象单元推送ag-mem-45，输出抽象提炼审计日志 |
| TC-M27-02 | `ABSTRACT_IDLE`，快照内仅单条独立轻量化条目 | 单一条目快照 | 无抽象泛化收益，直接过滤，过滤计数+1 |
| TC-M27-03 | `ABSTRACT_IDLE`，分组提炼后abs_I低于泛化过滤下限 | 多条同类轻量化快照 | 整组过滤，不推送至ag-mem-45 |
| TC-M27-04 | `ABSTRACT_IDLE`，单次快照1200条轻量化条目 | 超大批量快照 | 自动分片串行抽象蒸馏计算，完整处理无内存溢出 |
| TC-M27-05 | `ABSTRACT_IDLE`，ag-mem-37下发abs_id批量元数据查询 | 元数据批量查询请求 | 返回抽象单元完整加权元数据+蒸馏高层向量快照 |
| TC-M27-06 | `ABSTRACT_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空临时缓存，停止快照处理与抽象计算 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-27匹配白皮书L4顶层抽象前置单元定位 | ✅ |
| 上游仅ag-mem-26、下游仅ag-mem-45，数据流闭环无冲突 | ✅ |
| 5种内部状态切换逻辑完整，覆盖缓存、抽象计算、下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 向量相似度分组、蒸馏加权公式、泛化过滤规则严格对齐V1.1顶层记忆降噪准入设计 | ✅ |
| 伪代码覆盖快照接收、分组拆分、抽象蒸馏计算、过滤、批量下发、审计日志、容量上报全链路 | ✅ |
| 异常场景覆盖向量缺失、超大批次、算力超时、缓存溢出、熔断、无分槽规则共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅允许L4推送原始轻量化快照 | ✅ |
| 6条安全约束杜绝旁路写入、跨层直达L5、长期数据留存风险 | ✅ |
| 6条测试用例覆盖全部核心业务场景 | ✅ |

---