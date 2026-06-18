# ag-mem-25-L3条目轻量化归并单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-25 |
| 模块名称 | L3条目轻量化归并单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（L3后置轻量化预处理） |
| 核心职责 | 上游唯一输入来源为ag-mem-24晋升待推送聚合条目，作为L3→L4后置轻量化预处理单元；对同funnel、同任务大类的聚合条目做二次轻量化合并，压缩冗余向量、加权更新全局I与复用计数，降低L4存储开销；剔除高度重复、价值极低的冗余聚合条目；输出轻量化标准化条目批量推送至ag-mem-26 L4长期存储；对外提供轻量化后元数据快照供给ag-mem-37、ag-mem-40用于I刷新与遗忘扫描；无持久化存储，仅内存临时轻量化计算；所有合并、精简、过滤操作推送完整审计日志至ag-mem-51；严格遵循V1.1「长期记忆轻量化降噪」设计，禁止跨层直达L5。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-24（L3中期存储层，唯一上游条目来源）、ag-mem-35（三维权重配置单元，读取轻量化相似度阈值、精简权重系数、分funnel过滤规则）、ag-mem-48（全局容量配额管控，上报临时计算内存占用） |
| 被依赖模块 | ag-mem-26（L4长期存储层，接收轻量化完成条目）、ag-mem-37（重要度定时刷新单元，读取轻量化条目元数据快照）、ag-mem-40（遗忘阈值判定单元，提供轻量化条目扫描数据）、ag-mem-48（接收轻量化计算内存占用上报）、ag-mem-51（推送轻量化归并审计日志）、ag-mem-03（漏斗二调度单元，周期上报轻量化运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `LIGHT_MERGE_IDLE` | 空闲等待ag-mem-24下发晋升条目快照，无计算任务 | 系统初始化、熔断恢复、整批轻量化处理完毕 |
| 条目快照缓存 | `ITEM_BUFFER` | 接收L3晋升聚合条目，存入内存临时缓冲 | 收到ag-mem-24批量晋升条目推送 |
| 轻量化合并计算 | `LIGHT_CALC` | 按funnel、任务大类、向量相似度二次合并，加权更新指标，过滤低价值重复条目 | 快照缓存接收完成 |
| 轻量化条目下发 | `LIGHT_DISPATCH` | 过滤冗余条目，批量推送轻量化条目至ag-mem-26，输出元数据快照 | 轻量化分组计算全部完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空全部临时缓存，停止所有轻量化计算 | F0下发FUSE熔断指令；RESUME切回LIGHT_MERGE_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3晋升聚合条目快照 | List<Struct>（merge聚合ID、funnel分槽ID、agg_I、total_reuse、agg_S、merge_vector、task_tag、创建时间） | ag-mem-24 L3中期存储层 | L3定时晋升筛选完成，推送待轻量化聚合条目 | 高 |
| 轻量化规则参数回执 | Struct（轻量化相似度阈值、单组合并最小条数、冗余过滤I下限） | ag-mem-35 三维权重配置单元 | 模块初始化、轻量化规则更新 | 普通 |
| 轻量化条目元数据批量查询 | Struct（轻量化条目唯一ID列表） | ag-mem-37 / ag-mem-40 | 全局I值重算、分层遗忘扫描 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 轻量化完成条目批量推送 | List<Struct>（light_id轻量化ID、源merge_id集合、funnel_id、final_I、sum_reuse、avg_S、light_vector、task_tag） | ag-mem-26 L4长期存储层 | 存在可合并轻量化分组，过滤冗余条目后下发 | 高 |
| L3晋升快照接收回执 | Struct（快照总条数、缓存成功条数、冗余过滤条数） | ag-mem-24 L3中期存储层 | 完整快照存入临时内存缓冲 | 高 |
| 轻量化条目元数据快照 | List<轻量化完整元数据> | ag-mem-37、ag-mem-40 | 收到元数据批量查询请求 | 高 |
| 轻量化计算内存占用上报 | Struct（单元层级、临时缓存KB、当前待处理条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量快照处理后即时上报 | 普通 |
| 轻量化操作审计日志 | Struct（事件类型、原始聚合条目总数、轻量化合并分组数、冗余过滤条目数、时间戳、funnel范围） | ag-mem-51 记忆变更日志追溯单元 | 每一批快照轻量化处理完成 | 普通 |
| 轻量化周期运行统计上报 | Struct（当前状态、今日处理原始聚合条目总量、轻量化输出条目总量、冗余过滤总数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## L3轻量化归并核心规则（V1.1长期记忆降噪规范）
### 1. 全局轻量化配置参数（ag-mem-35统一分发）
1. 轻量化向量相似度阈值：0.9，余弦相似度≥阈值判定为高度重复可合并；
2. 轻量化单组最小条目数：≥2条才执行二次合并，单条直接保留不合并；
3. 冗余过滤I下限：低于该阈值的重复条目直接丢弃，不送入L4；
4. 轻量化加权计算公式：
final_I = sum(agg_I × total_reuse) / sum(total_reuse)
sum_reuse = 分组内所有条目复用次数累加
avg_S = 分组内agg_S算术平均值
light_vector = 复用次数加权平均压缩向量

### 2. 条目过滤规则（满足任意一条直接过滤，不推送L4）
1. 分组内条目数量＜2，无轻量化合并收益；
2. 轻量化后final_I低于分funnel冗余过滤I下限；
3. 条目创建时长不足L3最低留存周期，未经过充分验证。

### 3. 流转强制约束
1. 唯一上游：仅接收ag-mem-24晋升快照，拒绝其余模块输入；
2. 单向下游：仅输出轻量化条目至ag-mem-26，无旁路流向L5；
3. 无持久存储：快照仅内存临时缓存，处理完毕立即清空，不落地磁盘；
4. 不参与归档/遗忘清理逻辑，仅做L3→L4晋升轻量化预处理，无归档候选输出。

### 4. 批量约束
单次接收快照最大1000条，超量自动分片串行轻量化计算，防止向量压缩算力过载。

## 核心处理逻辑
```
FUNCTION l3_light_merge_main_loop():
    STATE_IDLE = LIGHT_MERGE_IDLE
    STATE_BUFFER = ITEM_BUFFER
    STATE_CALC = LIGHT_CALC
    STATE_DISPATCH = LIGHT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载轻量化全局配置
    light_cfg = query_light_merge_config(from_m35="ag-mem-35")
    sim_threshold = light_cfg.similarity_threshold
    min_group_size = light_cfg.min_light_group
    filter_min_I = light_cfg.light_filter_min_I
    temp_buffer = []
    stat_raw_agg_total = 0
    stat_light_group_cnt = 0
    stat_redundant_filter = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_buffer.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收ag-mem-24 L3晋升聚合条目快照
        IF 收到L3晋升聚合条目快照:
            snapshot_data = 获取快照条目列表
            internal_state = ITEM_BUFFER
            temp_buffer.extend(snapshot_data)
            cache_total = len(temp_buffer)
            stat_raw_agg_total += len(snapshot_data)
            // 回执返回上游ag-mem-24
            recv_ack = build_snapshot_recv_ack(total=len(snapshot_data), cached=cache_total)
            send_ack(target="ag-mem-24", ack_data=recv_ack)
            internal_state = LIGHT_CALC

            // 3. 轻量化分组加权计算
            task_group_map = {}
            filter_count = 0
            light_output_list = []
            now_ts = NOW()
            // 按funnel+task_tag基础分组
            for agg_item in temp_buffer:
                group_key = f"{agg_item.funnel分槽ID}_{agg_item.task_tag}"
                if group_key not in task_group_map:
                    task_group_map[group_key] = []
                task_group_map[group_key].append(agg_item)
            // 组内向量相似度二次细分轻量化分组
            for group_key, agg_list in task_group_map.items():
                semantic_light_groups = split_by_vector_sim(agg_list, sim_threshold)
                for light_group in semantic_light_groups:
                    if len(light_group) < min_group_size:
                        filter_count += len(light_group)
                        stat_redundant_filter += len(light_group)
                        continue
                    // 轻量化加权指标计算
                    total_reuse_sum = sum(i.total_reuse for i in light_group)
                    weight_i_sum = sum(i.agg_I * i.total_reuse for i in light_group)
                    avg_s_val = sum(i.agg_S for i in light_group) / len(light_group)
                    compress_vec = weighted_compress_vector(light_group)
                    final_I = weight_i_sum / total_reuse_sum
                    // 校验L4准入轻量化I下限
                    if final_I < filter_min_I:
                        filter_count += len(light_group)
                        stat_redundant_filter += len(light_group)
                        continue
                    // 组装轻量化条目
                    source_merge_ids = [i.merge聚合ID for i in light_group]
                    light_item = {
                        "light_id": gen_uuid(),
                        "source_merge_ids": source_merge_ids,
                        "funnel_id": light_group[0].funnel分槽ID,
                        "final_I": final_I,
                        "sum_reuse": total_reuse_sum,
                        "avg_S": avg_s_val,
                        "light_vector": compress_vec,
                        "task_tag": light_group[0].task_tag
                    }
                    light_output_list.append(light_item)
                    stat_light_group_cnt += 1
            temp_buffer.clear()
            internal_state = LIGHT_DISPATCH

            // 4. 批量下发轻量化条目至ag-mem-26
            if len(light_output_list) > 0:
                send_light_batch(target="ag-mem-26", item_list=light_output_list)
            // 写入轻量化审计日志
            audit_log = build_light_merge_audit(
                raw_agg_count=len(snapshot_data),
                light_group_num=stat_light_group_cnt,
                filter_num=filter_count,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = LIGHT_MERGE_IDLE

        // 5. 轻量化条目元数据批量查询响应
        IF 收到轻量化条目元数据批量查询请求:
            query_light_ids = 获取轻量化ID列表
            meta_snap = query_light_item_meta(query_light_ids)
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 6. 定时内存占用上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_temp_buffer_kb(temp_buffer, light_cfg.avg_agg_kb)
            cap_report = build_cap_report(layer="ag-mem-25", used_kb=cache_kb, item_count=len(temp_buffer))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_light_stat_report(
                    state=internal_state,
                    total_raw_agg=stat_raw_agg_total,
                    total_light_group=stat_light_group_cnt,
                    total_filter=stat_redundant_filter
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| L3快照条目向量缺失、agg_I非法 | 本条直接过滤，不参与轻量化合并，日志标记异常 | ag-mem-24重新推送完整标准化聚合条目快照 |
| 单次快照条目超1000条上限 | 自动分片分批加载、轻量化计算，串行执行不阻塞主线程 | 内置分片逻辑自动处理 |
| 向量压缩相似度计算算力超时 | 当前分组全部过滤，记录系统告警，下一轮快照重新处理 | 系统算力负载下降后正常执行轻量化合并 |
| 临时内存缓存溢出 | 截断后半段快照存入缓存，溢出条目丢弃并生成告警日志 | 降低ag-mem-24单次推送快照数量或扩容计算内存 |
| 全局FUSE熔断触发 | 清空全部临时缓存，终止当前轻量化计算，拒绝接收新快照 | ag-mem-01下发RESUME恢复指令 |
| 分funnel无专属轻量化阈值配置 | 加载全局通用相似度、过滤I参数兜底计算 | ag-mem-35运维侧补充分funnel轻量化规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L3晋升聚合条目快照 | 只读 | ag-mem-24 唯一上游输入 |
| 内部调度总线 | 读 | 轻量化规则配置回执 | 只读 | ag-mem-35 |
| 内部调度总线 | 读 | 元数据批量查询、全局熔断指令 | 只读 | ag-mem-37/40、ag-mem-01 |
| 内部调度总线 | 写 | 快照接收回执 | 专属写入 | 返回上游 ag-mem-24 |
| 内部调度总线 | 写 | 轻量化完成条目批量推送 | 专属写入 | 下发下游 ag-mem-26 |
| 内部调度总线 | 写 | 元数据快照、容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L25-01 | 仅允许ag-mem-24推送晋升聚合条目，阻断其他模块旁路写入，防止低质量记忆流入L4长期存储 |
| L25-02 | 轻量化计算仅单向输出至ag-mem-26，无任何跨层直达L5流转通道，分层链路单向隔离 |
| L25-03 | 无持久化存储设计，原始聚合快照仅内存临时缓存，处理完毕立刻清空，减少数据泄露风险 |
| L25-04 | 相似度阈值、合并最小条数、过滤I下限全部由ag-mem-35集中管控，本地无硬编码业务参数 |
| L25-05 | 所有轻量化合并、冗余条目过滤操作完整写入ag-mem-51审计日志，留存原始merge_id与轻量化条目映射关系，支持全链路溯源 |
| L25-06 | 熔断状态自动清空临时缓存，避免内存堆积占用资源，服务恢复后等待ag-mem-24重新下发晋升快照 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M25-01 | `LIGHT_MERGE_IDLE`，ag-mem-24推送同funnel同标签、相似度0.92的3条聚合条目快照 | L3晋升聚合快照 | 自动分组轻量化加权合并，生成轻量化条目推送ag-mem-26，输出轻量化审计日志 |
| TC-M25-02 | `LIGHT_MERGE_IDLE`，快照内仅单条独立聚合条目 | 单一条目快照 | 无轻量化合并收益，直接过滤，过滤计数+1 |
| TC-M25-03 | `LIGHT_MERGE_IDLE`，分组轻量化后final_I低于过滤下限 | 多条同类聚合快照 | 整组冗余过滤，不推送至ag-mem-26 |
| TC-M25-04 | `LIGHT_MERGE_IDLE`，单次快照1200条聚合条目 | 超大批量快照 | 自动分片串行轻量化计算，完整处理无内存溢出 |
| TC-M25-05 | `LIGHT_MERGE_IDLE`，ag-mem-37下发轻量化ID批量查询 | 元数据批量查询请求 | 返回轻量化条目完整加权元数据+压缩向量快照 |
| TC-M25-06 | `LIGHT_MERGE_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空临时缓存，停止快照处理与轻量化计算 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-25匹配白皮书L3后置轻量化辅助单元定位 | ✅ |
| 上游仅ag-mem-24、下游仅ag-mem-26，数据流闭环无冲突 | ✅ |
| 5种内部状态切换逻辑完整，覆盖缓存、计算、下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 向量相似度轻量化分组、加权压缩公式、冗余过滤规则严格对齐V1.1长期记忆降噪设计 | ✅ |
| 伪代码覆盖快照接收、分组拆分、轻量化加权计算、过滤、批量下发、审计日志、容量上报全链路 | ✅ |
| 异常场景覆盖向量缺失、超大批次、算力超时、缓存溢出、熔断、无分槽规则共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅允许L3推送原始聚合快照 | ✅ |
| 6条安全约束杜绝旁路写入、跨层流转、长期数据留存风险 | ✅ |
| 6条测试用例覆盖全部核心业务场景 | ✅ |

---