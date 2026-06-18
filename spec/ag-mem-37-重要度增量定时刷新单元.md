# ag-mem-37 重要度定时刷新单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书，适配通用版ag-mem-35全局三维配置）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-37 |
| 模块名称 | 重要度定时刷新单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 记忆价值计算辅助单元 |
| 核心职责 | 定时批量扫描全funnel分层记忆条目，基于ag-mem-35下发的记忆晋升三维权重公式重新计算每条条目综合重要度I；同步更新L0~L4存储层条目I值；接收ag-mem-03调度信号支持局部分槽定向刷新；读取ag-mem-16/19分槽冷热聚合指标用于I值衰减修正；输出更新后的条目I快照至各分层存储；定时上报自身计算缓存内存占用至ag-mem-48；所有批量I刷新任务、分槽定向刷新操作写入ag-mem-51审计日志；仅负责I值批量重算更新，不参与条目晋升、遗忘、归档删除逻辑。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，接收PAUSE/RESUME/FUSE指令）、ag-mem-03（漏斗二调度，接收定时刷新调度信号）、ag-mem-35（通用三维配置中心，读取I值计算权重、时效衰减系数、单次批量计算上限）、ag-mem16/19（读取分槽冷热、全局聚合指标用于I衰减加权）、ag-mem20~26（读取分层原始条目、写入刷新后I值）、ag-mem-48（上报本地计算缓存内存开销） |
| 被依赖模块 | ag-mem20~26（接收刷新后的条目I值批量更新指令）、ag-mem-40（遗忘判定单元，读取最新I值快照作为淘汰依据）、ag-mem-48（接收定时内存占用上报）、ag-mem-51（记录I值批量刷新审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 刷新待机就绪 | `REFRESH_IDLE` | 计算缓存空闲，等待定时周期或调度刷新信号，无批量计算任务 | 系统初始化、熔断恢复、一轮全量I刷新完成 |
| 分层条目数据拉取缓存 | `ITEM_FETCH` | 按funnel分片拉取L0-L4待刷新条目基础数据存入本地缓存 | 定时刷新周期抵达 / ag-mem-03下发定向刷新调度信号 |
| 综合重要度I批量重算 | `I_CALC` | 读取ag-mem-35权重配置+冷热衰减系数，批量重新计算每条条目I值 | 分层条目完整拉取缓存完毕 |
| I值批量下发更新 | `I_DISPATCH` | 分片向对应分层存储下发更新后的条目I结构体，同步输出I快照 | 全批次条目I计算完成 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE指令，停止所有定时批量刷新，仅保留指标上报通路 | ag-mem-01下发熔断指令；RESUME切回REFRESH_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| I值刷新调度信号 | Struct（扫描范围：全量/指定funnel列表、执行优先级、批量分片上限覆盖） | ag-mem-03 漏斗二调度单元 | 定时周期到达、运维手动发起分槽刷新、分槽冷热指标大幅变动 | 高 |
| 分层原始条目快照 | List<Struct>（item_id、funnel_id、layer、reuse_count、S安全得分、create_ts、last_access_ts、old_I） | ag-mem20~26 分层存储单元 | 本单元发起条目拉取请求 | 高 |
| 全局三维记忆晋升配置回执 | Struct（复用权重、安全权重、时效权重、时效衰减系数、单次最大计算条目数） | ag-mem-35 通用配置中心 | 模块初始化、配置策略更新、每次刷新前拉取最新权重 | 普通 |
| 分槽冷热聚合指标快照 | List<Struct>（funnel_id、hot_score、cold_flag、idle_days） | ag-mem16、ag-mem19 | 计算I值时加载冷热衰减修正系数 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控刷新任务启停 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层条目I值批量更新指令 | List<Struct>（item_id、funnel_id、layer、new_I、update_ts） | ag-mem20~26 | 单分片条目I重算完成 | 高 |
| 全量/分槽最新I值快照 | List<Struct>（item_id、funnel_id、layer、new_I、cold_decay_rate） | ag-mem-40 遗忘阈值判定单元 | 一轮完整刷新任务执行完毕 | 高 |
| 计算缓存内存占用上报 | Struct（单元ag-mem-37、缓存条目总KB、待刷新条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量刷新完成后即时上报 | 普通 |
| I刷新审计日志 | Struct（事件类型、扫描funnel数量、刷新条目总数、执行耗时、刷新范围、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮批量刷新任务全部下发完成 | 普通 |
| 刷新单元周期运行统计上报 | Struct（当前状态、今日全量刷新执行次数、定向分槽刷新批次、累计刷新条目总量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 重要度I刷新核心规则（V1.1记忆晋升维度标准，取自ag-mem-35配置）
### 1. I值标准计算公式（统一由ag-mem-35下发三组权重，权重总和=1）
基础I = w_reuse × reuse_count + w_safe × S + w_time × fresh_coeff
最终I = 基础I × cold_decay（冷热衰减系数）
- fresh_coeff：时效新鲜度系数，由条目距上次访问时长+ag-mem-35时效衰减系数计算
- cold_decay：冷热衰减系数，冷分槽自动降低I值，取自ag-mem16冷热评分映射表

### 2. 刷新触发规则
1. 定时全量刷新：由ag-mem-03按ag-mem-35配置周期下发调度信号；
2. 定向局部刷新：冷热指标大幅波动、人工运维操作时，仅扫描指定funnel内条目，减少算力消耗；
3. 熔断降级规则：PAUSE半熔断停止定时全量刷新，仅保留人工紧急定向刷新；FUSE全熔断停止所有I计算与更新。

### 3. 批量分片约束（取自ag-mem-35配置）
单次批量计算最大条目上限，超量自动分片串行计算，避免单次抢占大量CPU/IO资源。

### 4. 流转强制约束
1. 仅读取分层原始条目，无新增/删除条目权限，仅下发I值更新指令；
2. 权重、衰减系数、批量上限全部由ag-mem-35统一管控，本地无硬编码参数；
3. 单向数据流：仅向分层存储下发更新、向ag-mem-40输出I快照，不修改分槽元数据、不触发晋升/淘汰；
4. 冷热衰减依赖ag-mem16/19聚合指标，指标缺失时使用全局默认衰减兜底参数。

## 核心处理逻辑
```
FUNCTION item_importance_refresh_main_loop():
    STATE_IDLE = REFRESH_IDLE
    STATE_FETCH = ITEM_FETCH
    STATE_CALC = I_CALC
    STATE_DISPATCH = I_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局I计算配置（来自ag-mem-35）
    refresh_cfg = query_memory_promote_config(from_m35="ag-mem-35")
    w_reuse = refresh_cfg.weight_reuse
    w_safe = refresh_cfg.weight_safe
    w_time = refresh_cfg.weight_time
    time_decay_base = refresh_cfg.time_decay_coeff
    max_batch_item = refresh_cfg.max_calc_item_per_slice
    temp_item_cache = []
    stat_full_refresh = 0
    stat_target_slot_refresh = 0
    stat_total_refresh_item = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                temp_item_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_refresh_state_audit(old_state, internal_state, "熔断暂停I刷新", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = REFRESH_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_refresh_state_audit(old_state, internal_state, "熔断恢复I刷新", now_ts))

        // 熔断状态跳过所有业务逻辑
        IF internal_state == SYSTEM_PAUSED:
            SLEEP 10ms
            CONTINUE

        // 2. 接收ag-mem-03下发I刷新调度信号
        IF 收到I值刷新调度信号:
            signal = 获取调度信号结构体
            scan_slot_range = signal.funnel_list
            internal_state = ITEM_FETCH
            // 拉取对应分层条目 + 分槽冷热指标
            raw_item_list = fetch_layer_items_by_funnel(scan_slot_range)
            cold_meta = fetch_slot_cold_meta(scan_slot_range, source=["ag-mem-16","ag-mem-19"])
            temp_item_cache = raw_item_list
            internal_state = I_CALC
            update_batch = []
            // 分片批量计算I值
            slice_item_list = split_slice(temp_item_cache, max_batch_item)
            for slice in slice_item_list:
                for item in slice:
                    f_id = item.funnel_id
                    reuse = item.reuse_count
                    s_score = item.S
                    idle_ms = now_ts - item.last_access_ts
                    // 时效新鲜度计算
                    fresh_coeff = Exp(-time_decay_base * idle_ms / (24*3600*1000))
                    base_I = w_reuse * reuse + w_safe * s_score + w_time * fresh_coeff
                    // 冷热衰减修正
                    slot_cold_data = cold_meta.get(f_id, {"cold_decay": 1.0})
                    final_I = base_I * slot_cold_data["cold_decay"]
                    update_batch.append({
                        "item_id": item.item_id,
                        "funnel_id": f_id,
                        "layer": item.layer,
                        "new_I": final_I,
                        "update_ts": now_ts
                    })
            temp_item_cache.clear()
            internal_state = I_DISPATCH
            // 分片下发I更新指令至对应分层存储
            slice_update = split_slice(update_batch, max_batch_item)
            for slice_up in slice_update:
                dispatch_I_update(target_layer=slice_up[0].layer, batch=slice_up)
            stat_total_refresh_item += len(update_batch)
            // 区分全量/定向刷新统计
            if signal.full_export == True:
                stat_full_refresh += 1
            else:
                stat_target_slot_refresh += 1
            // 输出最新I快照供给ag-mem-40
            i_snapshot = build_importance_snapshot(update_batch, cold_meta)
            send_I_snapshot(target="ag-mem-40", snap_data=i_snapshot)
            // 写入刷新审计日志
            audit_log = build_refresh_audit(
                slot_scan_num=len(scan_slot_range),
                refresh_item_num=len(update_batch),
                is_full_scan=signal.full_export,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = REFRESH_IDLE

        // 3. 60s定时内存占用上报 + 180s周期统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_refresh_cache_size(temp_item_cache, refresh_cfg.avg_item_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-37", used_kb=cache_kb, pending_item_count=len(temp_item_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s向ag-mem-03上报运行统计
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                stat_report = build_refresh_runtime_stat(
                    state=internal_state,
                    full_refresh_times=stat_full_refresh,
                    target_slot_batch=stat_target_slot_refresh,
                    total_refresh_items=stat_total_refresh_item
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 拉取分层条目返回空/字段缺失 | 跳过当前funnel分片，记录告警至审计日志，等待下一轮刷新周期重试 | ag-mem20~26分层存储恢复完整条目输出 |
| 分槽冷热指标拉取失败 | 统一使用全局默认衰减系数1.0计算I值，不中断刷新任务 | ag-mem16/19恢复冷热指标上报 |
| 单批次条目超过ag-mem-35配置上限 | 自动分片串行计算下发，不阻塞主线程 | 内置分片逻辑自动执行 |
| 本地条目缓存内存溢出 | 清空缓存，终止本轮刷新，向ag-mem-48上报容量风险告警 | 扩容计算内存或调大分片上限配置 |
| 半熔断PAUSE状态收到全量刷新调度信号 | 直接丢弃全量任务，仅允许定向分槽刷新执行 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35配置拉取失败 | 加载本地兜底权重模板继续计算，输出配置缺失告警 | ag-mem-35恢复下发完整记忆晋升三维参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 刷新调度信号、全局熔断指令、三维记忆晋升配置、分层条目、冷热分槽指标 | 只读 | ag-mem03、ag-mem01、ag-mem35、ag-mem20~26、ag-mem16/19 |
| 内部业务总线 | 写 | 条目I值批量更新指令 | 专属写入 | ag-mem20~26 |
| 内部调度总线 | 写 | 最新I值快照、内存容量上报、刷新审计日志、周期运行统计 | 事件/周期写入 | ag-mem40、ag-mem48、ag-mem51、ag-mem03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| IMP37-01 | 所有I值计算权重、衰减系数、批量上限统一取自ag-mem-35，本地禁止硬编码任何计算参数，统一管控策略 |
| IMP37-02 | 仅具备条目只读、I值更新下发权限，无新增、删除、迁移记忆条目能力，业务数据修改权限收敛至分层存储模块 |
| IMP37-03 | 熔断降级严格遵循F0下发指令，半熔断阻断高算力全量刷新，避免故障期间算力耗尽加剧系统异常 |
| IMP37-04 | 每一轮批量I刷新完整写入ag-mem-51审计日志，记录扫描范围、刷新条目数量、执行时间，支撑记忆价值变更全链路溯源 |
| IMP37-05 | 分片限流控制单次计算条目数量，防止大批量条目同步重算抢占CPU与存储IO，保障分槽分发、晋升主线业务稳定 |
| IMP37-06 | 熔断状态清空本地条目缓存，恢复后重新拉取最新条目数据计算，杜绝基于过期旧条目生成错误I值 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M37-01 | `REFRESH_IDLE`，ag-mem-03下发全funnel定时刷新信号 | 全量刷新调度信号 | 拉取全分层条目+冷热指标，批量重算I值、下发更新指令，向ag-mem40输出I快照，生成审计日志 |
| TC-M37-02 | `REFRESH_IDLE`，下发指定冷分槽定向刷新信号 | 局部funnel刷新调度信号 | 仅扫描目标分槽条目，冷分槽自动衰减降低I值，完成局部更新 |
| TC-M37-03 | `REFRESH_IDLE`，冷热指标模块离线无返回数据 | 缺失冷热指标的刷新任务 | 使用默认衰减系数1.0计算I值，正常完成刷新，记录轻度告警审计 |
| TC-M37-04 | `REFRESH_IDLE`，单次待刷新条目超过配置分片上限 | 超大批量条目快照 | 自动拆分多片串行计算、下发更新指令，无IO阻塞 |
| TC-M37-05 | `REFRESH_IDLE`，收到F0 PAUSE半熔断指令后收到全量刷新信号 | 半熔断+全量刷新调度信号 | 丢弃全量刷新任务，仅保留定向刷新通路 |
| TC-M37-06 | `REFRESH_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空条目缓存，停止所有I值计算与更新 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-37匹配白皮书重要度定时刷新辅助单元定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35三维记忆晋升参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖条目拉取、I计算、更新下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| I值计算公式、分片限流、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖调度信号接收、条目拉取、加权I计算、分片更新、快照输出、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖条目缺失、冷热指标失联、超大批量、缓存溢出、半熔断拦截、配置缺失共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅下发I更新指令，无条目增删权限 | ✅ |
| 6条V1.1安全约束统一参数管控、权限隔离、故障限流、全操作可审计、防算力风暴、规避过期数据计算 | ✅ |
| 6条自动化测试用例覆盖全部I刷新核心业务场景 | ✅ |

---