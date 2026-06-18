# ag-mem-19 分槽全局汇总统计单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书前置分槽配套汇总模块）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-19 |
| 模块名称 | 分槽全局汇总统计单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 前置分槽配套汇总辅助单元（汇总ag-mem15/16/17/18全部分槽指标） |
| 核心职责 | 统一聚合ag-mem-15分槽基础元数据、ag-mem-16冷热指标、ag-mem-17负载流量、ag-mem-18生命周期回收四类维度数据；按场景、用户空间、任务类型做分层聚合统计；输出全局分槽大盘指标、冷热分布、流量负载、闲置回收统计报表；为ag-mem-03漏斗调度提供全局资源视图；对外提供聚合统计快照供ag-mem-37、ag-mem-40做全局分层权重计算；定时上报统计缓存内存占用至ag-mem-48；所有全量聚合、报表生成操作写入ag-mem-51审计日志；无原始经验存储、无分槽管控/销毁能力，纯数据聚合统计模块，不参与记忆晋升、归档、存储写入业务。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-15（场景分槽主调度，读取基础funnel元数据）、ag-mem-16（冷热监控单元，读取冷热评分）、ag-mem-17（负载限流单元，读取流量指标）、ag-mem-18（生命周期回收单元，读取回收状态）、ag-mem-35（三维权重配置单元，读取聚合分组维度、统计周期、报表输出阈值）、ag-mem-48（全局容量配额管控，上报统计缓存内存开销） |
| 被依赖模块 | ag-mem-03（漏斗二调度单元，接收全局分槽汇总大盘报表、资源风险总览）、ag-mem-37（重要度定时刷新单元，读取全局聚合分槽权重快照）、ag-mem-40（遗忘阈值判定单元，基于全局分槽分布调整整体遗忘策略）、ag-mem-48（接收统计缓存内存定时上报）、ag-mem-51（记录全量聚合统计、报表生成审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 统计待机就绪 | `STAT_IDLE` | 统计缓存空闲，等待定时全量聚合周期，无报表计算任务 | 系统初始化、熔断恢复、一轮全局汇总处理完毕 |
| 多维度分槽指标拉取缓存 | `DATA_FETCH` | 同步拉取15/16/17/18四单元全量分槽指标存入本地聚合缓冲 | 全局汇总定时周期倒计时归零 |
| 分层聚合统计计算 | `AGG_CALC` | 按场景/用户空间/任务维度聚合，计算冷热、负载、回收大盘指标，生成分层报表 | 全部分槽多源指标拉取完成 |
| 汇总报表与快照下发 | `STAT_DISPATCH` | 推送全局大盘报表至ag-mem-03，对外提供聚合统计快照 | 分层聚合计算全部完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空本地聚合缓存，停止指标拉取、聚合计算、报表下发 | F0下发FUSE熔断指令；RESUME切回STAT_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 全量funnel基础元数据快照 | List<Struct>（funnel_id、scene、user_space_id、task_type、create_ts、last_access_ts、active_item_count） | ag-mem-15 | 定时聚合周期主动拉取 | 高 |
| 全量分槽冷热评分快照 | List<Struct>（funnel_id、hot_score、cold_flag、idle_days） | ag-mem-16 | 同步拉取冷热分层统计数据 | 高 |
| 全量分槽负载流量快照 | List<Struct>（funnel_id、per_second_write、7d_avg_qps、limit_mode） | ag-mem-17 | 同步拉取流量负载聚合数据 | 高 |
| 全量分槽生命周期回收快照 | List<Struct>（funnel_id、recycle_status、protect_flag、mark_recycle_ts） | ag-mem-18 | 同步拉取闲置回收统计数据 | 高 |
| 聚合统计配置规则回执 | Struct（聚合分组维度、统计刷新周期、高风险资源阈值、单次最大聚合分槽数量） | ag-mem-35 | 模块初始化、统计报表策略更新 | 普通 |
| 全局聚合指标批量查询请求 | Struct（筛选维度：scene/user_space/task_type / 全量大盘导出） | ag-mem-37 / ag-mem-40 | 全局I值重算、全分层遗忘策略调整 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、全局熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分槽全局汇总大盘报表 | Struct（总funnel数量、热/温/冷分槽数量、全局总QPS、待回收分槽数、资源风险清单、分层维度明细） | ag-mem-03 漏斗二调度单元 | 一轮全量聚合计算完成 | 普通 |
| 分层聚合统计快照 | List<Struct>（分组key、funnel数量、平均hot_score、平均QPS、待回收槽数量、资源占用预估） | ag-mem-37、ag-mem-40 | 收到聚合指标批量查询请求 | 高 |
| 统计缓存内存占用上报 | Struct（单元标识ag-mem-19、聚合缓存总KB、参与统计funnel总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、一轮聚合完成后即时上报 | 普通 |
| 聚合统计审计日志 | Struct（事件类型、参与聚合funnel总数、分层分组数量、高风险资源分组数量、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮全局汇总、报表生成完成 | 普通 |
| 统计单元周期运行统计上报 | Struct（当前状态、今日全量聚合执行次数、识别高风险分组总数、分层报表输出总量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 分槽全局汇总核心规则（V1.1前置资源大盘监控规范）
### 1. 全局聚合配置参数（ag-mem-35统一分发）
1. 聚合分层维度优先级：user_space_id > scene场景 > task_type任务类型；
2. 单次最大聚合处理funnel上限：2000条，超量分片聚合；
3. 资源高风险判定阈值：分组内冷槽占比≥70% 或 分组长期零流量占比≥60%；
4. 全局统计刷新周期：300秒执行一次完整全量聚合。

### 2. 分层聚合计算逻辑
1. 一级聚合：全局大盘（全funnel总量、冷热总量、总流量、待回收总量）；
2. 二级聚合：按user_space用户空间分组统计；
3. 三级聚合：同用户空间内按scene场景分组；
4. 四级聚合：同场景内按task_type任务细分；
每层均计算：分组槽数、平均冷热评分、平均7日QPS、待回收分槽占比、预估内存占用。

### 3. 高风险分组判定规则（满足任意一条标记风险写入大盘报表）
1. 分组冷分槽占比 ≥70%；
2. 分组7天平均QPS=0的分槽占比 ≥60%；
3. 分组待回收标记分槽数量 ≥该分组总funnel的50%。

### 4. 流转强制约束
1. 只读ag-mem15/16/17/18四类分槽指标，无任何修改、创建、销毁funnel的权限；
2. 无原始任务经验读写、持久存储能力，仅做指标聚合计算；
3. 不参与L0~L5任意存储层条目写入、晋升、归档、向量计算业务；
4. 单向数据流：仅读取上游分槽辅助单元数据，只输出统计报表与聚合快照，无管控指令下发能力。

### 5. 批量约束
单次聚合最多处理2000个funnel，超量自动分片串行聚合，避免大批量全量计算抢占主线程算力。

## 核心处理逻辑
```
FUNCTION slot_global_stat_main_loop():
    STATE_IDLE = STAT_IDLE
    STATE_FETCH = DATA_FETCH
    STATE_AGG = AGG_CALC
    STATE_DISPATCH = STAT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    stat_cfg = query_stat_config(from_m35="ag-mem-35")
    agg_dim_order = stat_cfg.agg_dim_list
    high_risk_cold_ratio = stat_cfg.high_risk_cold_percent
    high_risk_zero_flow_ratio = stat_cfg.high_risk_zero_flow_percent
    max_agg_slot = stat_cfg.max_agg_funnel
    scan_interval = stat_cfg.stat_refresh_sec
    scan_countdown = scan_interval
    temp_merge_cache = []
    stat_agg_run_times = 0
    stat_high_risk_group = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_merge_cache.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STAT_IDLE

        // 2. 定时全量多源指标拉取聚合流程
        IF internal_state == STAT_IDLE:
            scan_countdown -= 10
            IF scan_countdown <= 0:
                internal_state = DATA_FETCH
                // 同步拉取四单元完整分槽指标
                meta_data = fetch_all_meta(target="ag-mem-15")
                hotcold_data = fetch_all_hotcold(target="ag-mem-16")
                load_data = fetch_all_load(target="ag-mem-17")
                lc_data = fetch_all_lifecycle(target="ag-mem-18")
                // 多源数据合并关联funnel_id
                merged_all = merge_all_slot_source(meta_data, hotcold_data, load_data, lc_data)
                temp_merge_cache = merged_all
                internal_state = AGG_CALC
                now_ts = NOW()
                stat_agg_run_times += 1
                risk_group_list = []
                total_funnel = len(temp_merge_cache)
                slice_list = split_slice(temp_merge_cache, max_agg_slot)

                // 分层聚合容器
                user_space_agg_map = {}
                scene_agg_map = {}
                task_agg_map = {}
                global_total = {
                    "total_slot": 0,
                    "hot_slot": 0,
                    "warm_slot": 0,
                    "cold_slot": 0,
                    "global_qps": 0,
                    "wait_recycle_slot": 0
                }

                for slice_item in slice_list:
                    for slot in slice_item:
                        us_id = slot.user_space_id
                        sc = slot.scene
                        tt = slot.task_type
                        hot_score = slot.hot_score
                        qps7 = slot.avg_qps_7d
                        is_recycle_wait = slot.recycle_status == "wait_recycle"
                        is_cold = slot.cold_flag

                        // 全局大盘统计
                        global_total["total_slot"] += 1
                        global_total["global_qps"] += slot.per_second_write
                        if hot_score >= 60:
                            global_total["hot_slot"] += 1
                        elif hot_score >= 20:
                            global_total["warm_slot"] += 1
                        else:
                            global_total["cold_slot"] += 1
                        if is_recycle_wait:
                            global_total["wait_recycle_slot"] += 1

                        // 四层分组聚合
                        us_key = us_id
                        sc_key = f"{us_id}_{sc}"
                        tt_key = f"{us_id}_{sc}_{tt}"
                        // 用户空间聚合
                        if us_key not in user_space_agg_map:
                            user_space_agg_map[us_key] = init_agg_group()
                        fill_group_data(user_space_agg_map[us_key], slot)
                        // 场景聚合
                        if sc_key not in scene_agg_map:
                            scene_agg_map[sc_key] = init_agg_group()
                        fill_group_data(scene_agg_map[sc_key], slot)
                        // 任务类型聚合
                        if tt_key not in task_agg_map:
                            task_agg_map[tt_key] = init_agg_group()
                        fill_group_data(task_agg_map[tt_key], slot)

                // 遍历所有分组判定高风险
                all_groups = list(user_space_agg_map.values()) + list(scene_agg_map.values()) + list(task_agg_map.values())
                for group in all_groups:
                    slot_cnt = group.slot_count
                    cold_ratio = group.cold_slot / slot_cnt if slot_cnt > 0 else 0
                    zero_flow_ratio = group.zero_flow_slot / slot_cnt if slot_cnt > 0 else 0
                    recycle_ratio = group.wait_recycle_slot / slot_cnt if slot_cnt > 0 else 0
                    if cold_ratio >= high_risk_cold_ratio or zero_flow_ratio >= high_risk_zero_flow_ratio or recycle_ratio >= 0.5:
                        risk_group_list.append(group.group_key)
                        stat_high_risk_group += 1

                temp_merge_cache.clear()
                internal_state = STAT_DISPATCH

                // 组装全局汇总大盘报表下发ag-mem-03
                global_report = build_global_stat_report(
                    global_total=global_total,
                    user_space_agg=user_space_agg_map,
                    scene_agg=scene_agg_map,
                    task_agg=task_agg_map,
                    high_risk_groups=risk_group_list,
                    ts=now_ts
                )
                send_global_report(target="ag-mem-03", report=global_report)
                // 写入聚合审计日志
                audit_log = build_stat_audit_log(
                    agg_total_slot=total_funnel,
                    group_count=len(all_groups),
                    high_risk_group_num=len(risk_group_list),
                    ts=now_ts
                )
                send_audit_log(target="ag-mem-51", log_data=audit_log)
                scan_countdown = scan_interval
                internal_state = STAT_IDLE

        // 3. 响应分层聚合指标批量查询
        IF 收到全局聚合指标批量查询请求:
            query_param = 获取查询筛选条件
            full_merge = merge_all_slot_source(
                fetch_all_meta(target="ag-mem-15"),
                fetch_all_hotcold(target="ag-mem-16"),
                fetch_all_load(target="ag-mem-17"),
                fetch_all_lifecycle(target="ag-mem-18")
            )
            agg_snap = generate_filter_agg_snapshot(full_merge, query_param)
            send_meta_snapshot(target="ag-mem-37", meta_list=agg_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=agg_snap)

        // 4. 定时内存上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_stat_cache_kb(temp_merge_cache, stat_cfg.avg_slot_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-19", used_kb=cache_kb, agg_slot_count=len(temp_merge_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_stat_runtime_report(
                    state=internal_state,
                    total_agg_run=stat_agg_run_times,
                    total_high_risk_group=stat_high_risk_group
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem15/16/17/18任一模块指标拉取失败 | 本轮聚合终止，记录告警至ag-mem-51，等待下一周期重试 | 对应前置分槽辅助模块恢复完整指标输出 |
| 单次参与聚合funnel超过2000条上限 | 自动分片串行聚合计算，不阻塞主线程 | 内置分片逻辑自动执行 |
| 多层分组聚合算力超时 | 当前分片跳过，写入告警日志，下一轮完整重聚合 | 系统整体算力负载回落恢复正常 |
| 本地多源合并缓存内存溢出 | 清空缓存，跳过本轮聚合，向ag-mem-48上报容量风险告警 | 扩容计算内存或调长全局统计刷新周期 |
| 全局FUSE熔断触发 | 清空本地合并缓存，停止指标拉取、聚合、报表下发 | ag-mem-01下发RESUME恢复指令 |
| 无聚合分层配置 | 采用user_space+scene+task三层通用维度兜底聚合 | ag-mem-35运维侧补充分场景聚合规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 15/16/17/18分槽多源指标、聚合配置、全局熔断指令 | 只读 | ag-mem-15、ag-mem-16、ag-mem-17、ag-mem-18、ag-mem-35、ag-mem-01 |
| 内部调度总线 | 读 | 全局聚合指标批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 分槽全局汇总大盘报表 | 专属写入 | 下发至ag-mem-03 |
| 内部调度总线 | 写 | 分层聚合统计快照、内存容量上报、审计日志、周期运行统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| ST19-01 | 仅具备全部分槽指标只读权限，无创建、修改、销毁funnel的任何操作权限，纯观测统计单元，杜绝分槽资源误操作 |
| ST19-02 | 无原始任务交互经验读写、持久存储能力，仅聚合数值指标，规避业务原始数据泄露风险 |
| ST19-03 | 聚合分层维度、高风险阈值、聚合刷新周期全部由ag-mem-35统一管控，本地无硬编码统计参数 |
| ST19-04 | 每轮全量聚合、大盘报表输出完整写入ag-mem-51审计日志，记录参与统计分槽总量与高风险分组数量，支撑资源大盘溯源审计 |
| ST19-05 | 分片限制单次聚合分槽数量，防止大批量全量计算抢占记忆通路主线程算力，保障业务数据分发稳定性 |
| ST19-06 | 熔断状态清空本地合并缓存，恢复后重新拉取最新全量指标聚合，避免基于过期分槽数据生成错误资源大盘报表 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M19-01 | `STAT_IDLE`，大量冷分槽、长期零流量funnel混合指标 | 四单元合并完整分槽快照 | 分层聚合识别高风险分组，生成全局大盘报表推送ag-mem-03，标记高风险资源清单 |
| TC-M19-02 | `STAT_IDLE`，多用户空间、多场景、多任务混合分槽数据 | 多维度分槽指标快照 | 完成用户/场景/任务四层分层聚合，输出分层明细报表 |
| TC-M19-03 | `STAT_IDLE`，分组冷槽占比75%，达到高风险阈值 | 对应分组分槽指标快照 | 该分组标记为高风险写入全局报表风险清单 |
| TC-M19-04 | `STAT_IDLE`，单次聚合2400个funnel分槽 | 超大批量多源指标快照 | 自动分片串行聚合计算，完整输出全局大盘无阻塞 |
| TC-M19-05 | `STAT_IDLE`，ag-mem-37下发按用户空间筛选聚合查询 | 分层维度筛选查询请求 | 返回指定用户空间下场景、任务分层聚合统计快照 |
| TC-M19-06 | `STAT_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空本地合并缓存，停止全量聚合与报表下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-19匹配白皮书前置分槽全局汇总统计配套单元定位 | ✅ |
| 上游只读ag-mem15/16/17/18多源指标，下游仅全局报表+聚合快照，数据流闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖多源拉取、分层聚合、报表下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 四层分层聚合、高风险分组判定、分片聚合规则严格对齐V1.1前置资源大盘监控规范 | ✅ |
| 伪代码覆盖多源指标合并、四层聚合计算、高风险判定、全局报表生成、聚合快照查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖多源指标缺失、超大分槽总量、算力超时、缓存溢出、熔断、无聚合配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅读取分槽指标，无任何分槽管控操作权限 | ✅ |
| 6条V1.1安全约束防止分槽误操作、算力抢占、业务数据泄露、过期指标生成错误大盘 | ✅ |
| 6条自动化测试用例覆盖全部全局聚合统计核心场景 | ✅ |

---