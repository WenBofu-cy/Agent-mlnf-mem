# ag-mem-40 遗忘阈值判定单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-40 |
| 模块名称 | 遗忘阈值判定单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 记忆淘汰计算辅助单元 |
| 核心职责 | 接收ag-mem-03定时扫描调度信号与ag-mem-37输出的最新条目I值快照；基于ag-mem-35下发的记忆晋升维度分层遗忘阈值，分层筛选低于淘汰阈值的待清理条目；结合ag-mem-16冷热指标、ag-mem-41复用保护标记做二次过滤，排除受保护条目；生成待淘汰条目候选清单下发至ag-mem-42冗余记忆删除单元；定时上报自身快照缓存内存占用至ag-mem-48；全量遗忘扫描、分槽定向淘汰筛选操作完整写入ag-mem-51审计日志；仅负责条目淘汰判定筛选，无条目删除、存储写入、I值重算能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控扫描任务启停）、ag-mem-03（漏斗二调度，下发定时/人工遗忘扫描调度信号）、ag-mem-35（通用三维配置中心，读取各分层遗忘I阈值、单次扫描分片上限、冷槽淘汰倍率）、ag-mem-37（读取全分层最新条目I值快照）、ag-mem-16（读取分槽冷热评分，调整冷槽淘汰宽松度）、ag-mem-41（读取分层复用保护条目黑名单，过滤免淘汰条目）、ag-mem-48（上报本地快照缓存内存开销） |
| 被依赖模块 | ag-mem-42（接收待淘汰条目候选清单，执行实际删除清理）、ag-mem-48（接收定时内存占用上报）、ag-mem-51（记录遗忘扫描筛选审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 判定待机就绪 | `FORGET_IDLE` | 快照缓存空闲，等待定时扫描或人工定向扫描信号，无筛选计算任务 | 系统初始化、熔断恢复、一轮完整遗忘筛选任务执行完毕 |
| I值与辅助指标缓存加载 | `DATA_FETCH` | 同步拉取ag-mem37全分层I快照、ag-mem16冷热指标、ag-mem41复用保护清单存入本地缓存 | 遗忘扫描调度信号抵达 |
| 分层遗忘阈值筛选计算 | `THRESHOLD_FILTER` | 对照ag-mem-35分层遗忘阈值，初步筛选I值不达标的待淘汰条目，再过滤保护条目 | 全套I值、冷热、复用保护指标缓存加载完成 |
| 淘汰候选清单下发 | `CANDIDATE_DISPATCH` | 分片向ag-mem-42推送分层待删除条目清单，同步输出扫描统计报表 | 全分层条目筛选计算完成 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止所有全量遗忘扫描，仅保留人工紧急定向筛选通路 | ag-mem-01下发熔断指令；RESUME切回FORGET_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 遗忘扫描调度信号 | Struct（扫描范围：全量/指定funnel列表、执行优先级、分片数量覆盖） | ag-mem-03 漏斗二调度单元 | 定时扫描周期到达、运维手动发起分槽清理、分层容量预警 | 高 |
| 分层条目最新I值全量快照 | List<Struct>（item_id、funnel_id、layer、current_I、last_refresh_ts） | ag-mem-37 重要度定时刷新单元 | 遗忘扫描任务启动前主动拉取 | 高 |
| 分槽冷热评分指标快照 | List<Struct>（funnel_id、hot_score、cold_flag、cold_eliminate_multiplier） | ag-mem-16 分槽冷热监控单元 | 筛选计算时用于调整淘汰阈值倍率 | 普通 |
| 分层复用保护条目清单 | List<Struct>（item_id、layer、protect_expire_ts） | ag-mem-41 复用校验单元 | 过滤满足最低复用次数、受保护不可淘汰条目 | 普通 |
| 全局三维记忆晋升配置回执 | Struct（L1~L4分层遗忘I阈值、冷槽淘汰倍率、单次最大扫描条目分片上限） | ag-mem-35 通用配置中心 | 模块初始化、配置更新、每次扫描前拉取最新阈值 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控遗忘扫描任务启停 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层待淘汰条目候选清单 | List<Struct>（item_id、funnel_id、layer、item_I、eliminate_reason） | ag-mem-42 冗余记忆删除单元 | 单分片条目筛选计算完成 | 高 |
| 遗忘扫描统计汇总报表 | Struct（扫描funnel总数、各分层扫描条目总量、初步筛选条目数、剔除保护条目数、最终待淘汰条目总量） | ag-mem-03 漏斗二调度单元 | 一轮完整遗忘筛选任务全部下发完成 | 普通 |
| 快照缓存内存占用上报 | Struct（单元ag-mem-40、I快照缓存总KB、待筛选条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量扫描完成后即时上报 | 普通 |
| 遗忘扫描审计日志 | Struct（事件类型、扫描范围、分层待淘汰条目数量、冷热过滤剔除数、复用保护剔除数、执行耗时、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮遗忘筛选任务全部下发完成 | 普通 |
| 判定单元周期运行统计上报 | Struct（当前状态、今日全量遗忘扫描次数、定向分槽清理批次、累计输出待淘汰条目总数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 遗忘阈值判定核心规则（V1.1记忆晋升维度标准，取自ag-mem-35配置）
### 1. 分层基础遗忘阈值（由ag-mem-35统一下发，层级递增）
- L1遗忘阈值：基础T1
- L2遗忘阈值：基础T2（T2 > T1）
- L3遗忘阈值：基础T3（T3 > T2）
- L4遗忘阈值：基础T4（T4 > T3）
### 2. 冷热修正倍率规则
冷分槽读取ag-mem-35配置冷槽淘汰倍率，放大分层阈值，更容易触发淘汰；热分槽倍率固定为1，使用原生分层阈值。
修正后实际淘汰阈值 = 分层基础阈值 × 冷热淘汰倍率
条目当前I < 修正后阈值 → 进入初步待淘汰候选池
### 3. 复用保护过滤规则
候选池内条目若存在于ag-mem-41复用保护清单，直接剔除，不进入最终淘汰清单，保护高频复用记忆。
### 4. 扫描触发与熔断降级规则
1. 全量遗忘扫描：由ag-mem-03按ag-mem-35配置周期调度；
2. 定向分槽扫描：分层容量预警、人工运维清理时仅扫描指定funnel，减少算力开销；
3. PAUSE半熔断：拦截定时全量遗忘扫描，仅放行人工紧急定向清理；FUSE全熔断停止全部筛选计算。
### 5. 分片批量约束
单次扫描最大条目上限取自ag-mem-35配置，超量自动分片串行筛选，防止瞬时算力占用过高。

### 6. 流转强制约束
1. 仅读取I快照、冷热指标、复用保护清单，无条目删除、存储写入、I值修改权限，仅输出淘汰候选清单；
2. 分层遗忘阈值、冷槽倍率、分片上限全部由ag-mem-35统一管控，本地无硬编码参数；
3. 单向数据流：仅向ag-mem-42输出候选清单，不修改分槽元数据、不执行条目晋升/归档；
4. 依赖指标缺失时加载全局兜底阈值与倍率，保证扫描流程不中断。

## 核心处理逻辑
```
FUNCTION forget_threshold_judge_main_loop():
    STATE_IDLE = FORGET_IDLE
    STATE_FETCH = DATA_FETCH
    STATE_FILTER = THRESHOLD_FILTER
    STATE_DISPATCH = CANDIDATE_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35记忆晋升维度遗忘全套配置
    forget_cfg = query_memory_forget_config(from_m35="ag-mem-35")
    layer_thresh = forget_cfg.layer_forget_threshold
    cold_elim_multi = forget_cfg.cold_slot_eliminate_multiplier
    max_scan_slice = forget_cfg.max_scan_item_per_batch
    temp_data_cache = []
    stat_full_scan_times = 0
    stat_target_slot_times = 0
    stat_total_candidate_item = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                temp_data_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_forget_state_audit(old_state, internal_state, "熔断暂停遗忘判定扫描", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = FORGET_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_forget_state_audit(old_state, internal_state, "熔断恢复遗忘判定扫描", now_ts))

        // 熔断状态跳过全部业务逻辑
        IF internal_state == SYSTEM_PAUSED:
            SLEEP 10ms
            CONTINUE

        // 2. 接收ag-mem-03下发遗忘扫描调度信号
        IF 收到遗忘扫描调度信号:
            scan_signal = 获取调度信号结构体
            target_funnel_list = scan_signal.funnel_list
            internal_state = DATA_FETCH
            // 同步拉取全套依赖指标
            i_snap = fetch_importance_snapshot(funnel_range=target_funnel_list, source="ag-mem-37")
            cold_meta = fetch_slot_cold_meta(target_funnel_list, source="ag-mem-16")
            protect_item_list = fetch_reuse_protect_list(source="ag-mem-41")
            temp_data_cache = i_snap
            internal_state = THRESHOLD_FILTER
            raw_candidate_pool = []
            final_candidate_list = []
            slice_data = split_slice(temp_data_cache, max_scan_slice)

            // 分片分层阈值筛选
            for slice in slice_data:
                for item in slice:
                    f_id = item.funnel_id
                    layer = item.layer
                    item_I = item.current_I
                    // 获取分层基础阈值与冷槽倍率
                    base_thresh = layer_thresh[layer]
                    slot_cold = cold_meta.get(f_id, {"cold_flag": False, "multi": 1.0})
                    real_thresh = base_thresh * (cold_elim_multi if slot_cold["cold_flag"] else 1.0)
                    // 初步筛选低于阈值条目
                    if item_I < real_thresh:
                        raw_candidate_pool.append({
                            "item_id": item.item_id,
                            "funnel_id": f_id,
                            "layer": layer,
                            "item_I": item_I,
                            "real_threshold": real_thresh,
                            "eliminate_reason": f"I值{item_I:.2f}低于分层淘汰阈值{real_thresh:.2f}"
                        })
            // 过滤复用保护条目
            protect_id_set = set([p["item_id"] for p in protect_item_list])
            for cand in raw_candidate_pool:
                if cand["item_id"] not in protect_id_set:
                    final_candidate_list.append(cand)
            temp_data_cache.clear()
            internal_state = CANDIDATE_DISPATCH

            // 分片下发待淘汰清单至ag-mem-42
            slice_candidate = split_slice(final_candidate_list, max_scan_slice)
            for slice_cand in slice_candidate:
                dispatch_eliminate_candidate(target="ag-mem-42", batch=slice_cand)
            stat_total_candidate_item += len(final_candidate_list)
            // 统计扫描类型
            if scan_signal.full_export:
                stat_full_scan_times += 1
            else:
                stat_target_slot_times += 1
            // 生成扫描统计报表上报ag-mem-03
            scan_report = build_forget_scan_report(
                scan_slot_num=len(target_funnel_list),
                raw_candidate_count=len(raw_candidate_pool),
                protect_filter_count=len(raw_candidate_pool) - len(final_candidate_list),
                final_candidate_count=len(final_candidate_list)
            )
            send_scan_report(target="ag-mem-03", report=scan_report)
            // 写入遗忘扫描审计日志
            audit_log = build_forget_audit_log(
                scan_range=target_funnel_list,
                raw_candidate=len(raw_candidate_pool),
                protect_filter=len(raw_candidate_pool) - len(final_candidate_list),
                final_eliminate=len(final_candidate_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = FORGET_IDLE

        // 3. 60秒定时内存上报 + 180秒周期运行统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_scan_cache_size(temp_data_cache, forget_cfg.avg_item_snap_kb)
            cap_report = build_cap_report(layer="ag-mem-40", used_kb=cache_kb, pending_scan_item=len(temp_data_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_forget_runtime_stat(
                    state=internal_state,
                    full_scan_total=stat_full_scan_times,
                    target_slot_scan=stat_target_slot_times,
                    total_candidate_items=stat_total_candidate_item
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-37 I快照拉取为空、字段缺失 | 跳过当前funnel分片，记录告警审计，等待下一扫描周期重试 | ag-mem-37正常输出完整I值快照 |
| ag-mem-16冷热指标拉取失败 | 统一使用倍率1.0（热槽标准阈值）筛选，不中断扫描流程 | ag-mem-16恢复冷热指标上报 |
| ag-mem-41复用保护清单获取失败 | 临时关闭保护过滤，全部低I条目进入候选池，写入风险告警 | ag-mem-41恢复输出保护条目列表 |
| 单次扫描条目超过ag-mem-35分片上限 | 自动分片串行筛选下发，不阻塞主线程 | 内置分片逻辑自动执行 |
| 本地快照缓存内存溢出 | 清空缓存、终止本轮扫描，向ag-mem-48上报容量风险告警 | 扩容计算内存或调大分片配置上限 |
| PAUSE半熔断状态收到全量扫描信号 | 直接丢弃全量扫描任务，仅允许定向分槽清理执行 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35遗忘配置拉取失败 | 加载本地兜底分层阈值模板继续筛选，输出配置缺失告警 | ag-mem-35恢复下发完整记忆晋升三维参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 遗忘扫描调度信号、全局熔断指令、分层遗忘配置、I值快照、冷热指标、复用保护清单 | 只读 | ag-mem03、ag-mem01、ag-mem35、ag-mem37、ag-mem16、ag-mem41 |
| 内部业务总线 | 写 | 分层待淘汰条目候选清单 | 专属写入 | ag-mem-42 |
| 内部调度总线 | 写 | 扫描统计报表、内存容量上报、遗忘扫描审计日志、周期运行统计 | 事件/周期写入 | ag-mem03、ag-mem48、ag-mem51 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| FOR40-01 | 分层遗忘阈值、冷槽淘汰倍率、扫描分片上限全部取自ag-mem-35，本地禁止硬编码任何淘汰判定参数，统一管控策略 |
| FOR40-02 | 仅具备各类指标只读、候选清单下发权限，无条目删除、存储写入、I值修改能力，数据变更操作收敛至存储与清理单元 |
| FOR40-03 | 熔断分级管控扫描任务，半熔断阻断高算力全量扫描，避免系统故障期间大量筛选抢占资源加剧雪崩 |
| FOR40-04 | 每一轮遗忘完整扫描全量写入ag-mem-51审计日志，记录扫描范围、筛选、剔除、待淘汰条目数量，支撑记忆清理行为全链路溯源 |
| FOR40-05 | 分片限流控制单次筛选条目数量，防止大批量条目同步阈值计算抢占CPU、IO，保障分槽分发、晋升主线业务稳定运行 |
| FOR40-06 | 熔断状态清空本地I快照缓存，恢复后重新拉取最新条目数据判定，杜绝基于过期I值生成错误淘汰清单 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M40-01 | `FORGET_IDLE`，ag-mem-03下发全funnel定时遗忘扫描信号 | 全量扫描调度信号 | 拉取I快照+冷热+保护清单，分层阈值筛选，剔除保护条目，向ag-mem42下发最终候选清单，输出扫描统计报表、审计日志 |
| TC-M40-02 | `FORGET_IDLE`，目标冷分槽条目I低于修正后放大阈值 | 冷分槽定向扫描信号 | 冷槽倍率放大分层阈值，更多低I条目进入淘汰候选池 |
| TC-M40-03 | `FORGET_IDLE`，条目满足淘汰阈值但存在复用保护标记 | 含受保护条目快照 | 该条目从候选池剔除，不加入待淘汰清单 |
| TC-M40-04 | `FORGET_IDLE`，单次待扫描条目超过配置分片上限 | 超大批量I值快照 | 自动拆分多片串行筛选、下发候选清单，无算力阻塞 |
| TC-M40-05 | `FORGET_IDLE`，收到F0 PAUSE半熔断指令后收到全量扫描信号 | 半熔断+全量扫描调度信号 | 丢弃全量扫描任务，仅保留定向分槽清理通路 |
| TC-M40-06 | `FORGET_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空快照缓存，停止全部遗忘筛选计算 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-40匹配白皮书遗忘阈值判定辅助单元定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35记忆晋升三维遗忘参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖指标拉取、分层筛选、候选清单下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 分层递增遗忘阈值、冷槽倍率修正、复用保护过滤、分片限流规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖扫描信号接收、多源指标拉取、阈值筛选、保护过滤、分片下发、统计上报、审计日志全链路 | ✅ |
| 异常场景覆盖I快照缺失、冷热指标失联、保护清单失效、超大批量、缓存溢出、半熔断拦截共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅输出淘汰候选清单，无条目删除修改权限 | ✅ |
| 6条V1.1安全约束统一参数管控、权限隔离、故障限流、全操作可审计、防算力风暴、规避过期数据判定 | ✅ |
| 6条自动化测试用例覆盖全部遗忘判定核心业务场景 | ✅ |

---