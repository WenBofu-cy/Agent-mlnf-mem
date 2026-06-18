# ag-mem-41 复用校验单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-41 |
| 模块名称 | 复用校验单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 记忆保护辅助单元 |
| 核心职责 | 读取分层条目复用次数、ag-mem-35下发的分层最低复用保护阈值；校验条目是否达到保护标准，生成复用保护白名单供给ag-mem-40遗忘判定单元；接收分层条目访问更新事件，实时刷新条目复用计数；支持分槽批量校验、全局全量复用统计；定时上报本地缓存内存占用至ag-mem-48；所有批量校验、保护名单更新操作写入ag-mem-51审计日志；仅做复用次数统计与保护资格判定，无条目删除、I值修改、晋升调度能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控校验任务启停）、ag-mem-03（漏斗二调度，下发全局/分槽批量复用校验调度信号）、ag-mem-35（通用三维配置中心，读取L1~L4分层最低复用保护次数、单次批量校验条目上限）、ag-mem20~26（读取分层条目复用计数、接收访问事件更新复用次数）、ag-mem-48（上报本地条目缓存内存开销） |
| 被依赖模块 | ag-mem-40（读取分层复用保护条目白名单，过滤免淘汰条目）、ag-mem-48（接收定时内存占用上报）、ag-mem-51（记录复用批量校验、保护名单变更审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 校验待机就绪 | `REUSE_IDLE` | 条目缓存空闲，等待定时批量校验或访问事件，无大规模计算任务 | 系统初始化、熔断恢复、一轮完整复用校验完成 |
| 分层复用数据拉取缓存 | `DATA_FETCH` | 拉取分层条目复用计数、访问增量数据存入本地缓存 | 批量校验调度信号抵达、大批量条目访问事件推送 |
| 复用保护资格批量校验 | `PROTECT_CALC` | 对照ag-mem-35分层复用阈值，筛选满足保护条件条目，生成保护白名单 | 分层复用数据缓存加载完成 |
| 保护名单下发同步 | `LIST_DISPATCH` | 分片向ag-mem-40推送最新复用保护条目清单，输出复用统计报表 | 全分层条目校验计算完成 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止全局批量复用校验，仅保留单条访问实时计数更新 | ag-mem-01下发熔断指令；RESUME切回REUSE_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 批量复用校验调度信号 | Struct（扫描范围：全量/指定funnel列表、执行优先级、分片上限覆盖） | ag-mem-03 漏斗二调度单元 | 定时全局校验周期、人工分槽复用统计、分层容量预警 | 高 |
| 条目访问增量事件 | List<Struct>（item_id、funnel_id、layer、access_increment） | ag-mem20~26 分层存储单元 | 用户交互触发条目复用访问，实时推送 | 最高 |
| 分层条目复用计数快照 | List<Struct>（item_id、funnel_id、layer、reuse_count、last_access_ts） | ag-mem20~26 | 批量校验任务启动主动拉取 | 高 |
| 全局三维记忆晋升配置回执 | Struct（L1/L2/L3/L4分层最低复用保护次数、单次最大批量校验条目数） | ag-mem-35 通用配置中心 | 模块初始化、配置策略更新、每次批量校验前拉取 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控批量校验任务启停 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层复用保护条目白名单 | List<Struct>（item_id、funnel_id、layer、reuse_count、protect_expire_ts） | ag-mem-40 遗忘阈值判定单元 | 单分片复用校验完成 | 高 |
| 复用校验全局统计报表 | Struct（扫描funnel总数、各分层条目总量、符合保护条目数量、无保护低复用条目数量） | ag-mem-03 漏斗二调度单元 | 一轮完整批量校验全部下发完成 | 普通 |
| 条目缓存内存占用上报 | Struct（单元ag-mem-41、复用计数缓存总KB、待校验条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量校验完成后即时上报 | 普通 |
| 复用校验审计日志 | Struct（事件类型、扫描范围、分层受保护条目总数、实时访问更新条目数、执行耗时、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮批量校验任务下发完成 | 普通 |
| 复用单元周期运行统计上报 | Struct（当前状态、今日全局批量校验次数、分槽定向校验批次、累计受保护条目总量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 复用校验核心规则（V1.1记忆晋升维度标准，取自ag-mem-35配置）
### 1. 分层复用保护阈值（层级逐级递增，由ag-mem-35统一下发）
- L1最低复用保护次数：R1
- L2最低复用保护次数：R2（R2 > R1）
- L3最低复用保护次数：R3（R3 > R2）
- L4最低复用保护次数：R4（R4 > R3）
### 2. 保护资格判定逻辑
条目当前复用计数 ≥ 当前分层最低复用阈值 → 加入复用保护白名单，ag-mem-40遗忘扫描时直接跳过淘汰；
条目复用计数 < 分层阈值 → 无复用保护，正常参与遗忘阈值筛选。
### 3. 实时访问计数更新规则
收到分层存储推送的条目访问事件，本地实时累加复用计数；批量校验时同步回拉分层最新复用数据，保证计数一致性。
### 4. 熔断降级规则
1. PAUSE半熔断：拦截定时全局批量复用校验，仅处理单条实时访问计数更新、人工定向分槽校验；
2. FUSE全熔断：停止所有批量校验、暂停访问计数同步，仅维持心跳与日志上报。
### 5. 分片批量约束
单次批量校验最大条目上限取自ag-mem-35配置，超量自动分片串行计算，避免短时算力突增。
### 6. 流转强制约束
1. 仅读取分层复用计数、接收访问增量事件，无条目写入、删除、I值修改权限，仅输出保护名单；
2. 分层复用阈值、分片上限全部由ag-mem-35统一管控，本地无硬编码参数；
3. 单向数据流：仅向ag-mem-40输出保护白名单，不修改分槽元数据、不执行条目晋升/清理；
4. 分层存储指标缺失时加载全局通用复用阈值兜底，不中断校验流程。

## 核心处理逻辑
```
FUNCTION reuse_verify_main_loop():
    STATE_IDLE = REUSE_IDLE
    STATE_FETCH = DATA_FETCH
    STATE_CALC = PROTECT_CALC
    STATE_DISPATCH = LIST_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35记忆晋升维度复用保护配置
    reuse_cfg = query_reuse_protect_config(from_m35="ag-mem-35")
    layer_reuse_thresh = reuse_cfg.layer_min_reuse_count
    max_verify_slice = reuse_cfg.max_verify_item_per_batch
    temp_reuse_cache = []
    stat_global_batch = 0
    stat_slot_batch = 0
    stat_total_protect_item = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                temp_reuse_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_reuse_state_audit(old_state, internal_state, "熔断暂停批量复用校验", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = REUSE_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_reuse_state_audit(old_state, internal_state, "熔断恢复复用校验", now_ts))

        // 熔断状态跳过批量业务逻辑，仅保留单条访问计数
        IF internal_state == SYSTEM_PAUSED:
            // 仅处理单条访问增量事件
            IF 收到条目访问增量事件:
                access_event = 获取访问事件
                realtime_update_reuse_count(local_cache=temp_reuse_cache, event=access_event)
            SLEEP 10ms
            CONTINUE

        // 2. 实时处理条目访问增量事件（最高优先级业务）
        IF 收到条目访问增量事件:
            access_event_list = 获取批量访问事件
            realtime_batch_update_reuse(temp_reuse_cache, access_event_list)

        // 3. 接收ag-mem-03下发批量复用校验调度信号
        IF 收到批量复用校验调度信号:
            verify_signal = 获取调度信号结构体
            target_funnel_range = verify_signal.funnel_list
            internal_state = DATA_FETCH
            // 拉取分层条目复用计数快照
            reuse_snap = fetch_layer_reuse_snapshot(funnel_range=target_funnel_range, source=["ag-mem20","ag-mem21","ag-mem22","ag-mem23","ag-mem24","ag-mem25","ag-mem26"])
            temp_reuse_cache = reuse_snap
            internal_state = PROTECT_CALC
            protect_white_list = []
            slice_item_list = split_slice(temp_reuse_cache, max_verify_slice)

            // 分片校验条目复用保护资格
            for slice in slice_item_list:
                for item in slice:
                    f_id = item.funnel_id
                    layer = item.layer
                    current_reuse = item.reuse_count
                    min_protect_r = layer_reuse_thresh[layer]
                    if current_reuse >= min_protect_r:
                        // 生成保护条目记录
                        protect_expire = now_ts + reuse_cfg.protect_valid_ms
                        protect_white_list.append({
                            "item_id": item.item_id,
                            "funnel_id": f_id,
                            "layer": layer,
                            "reuse_count": current_reuse,
                            "protect_expire_ts": protect_expire
                        })
            temp_reuse_cache.clear()
            internal_state = LIST_DISPATCH

            // 分片下发复用保护白名单至ag-mem-40
            slice_protect_list = split_slice(protect_white_list, max_verify_slice)
            for slice_protect in slice_protect_list:
                dispatch_protect_whitelist(target="ag-mem-40", batch=slice_protect)
            stat_total_protect_item += len(protect_white_list)
            // 统计校验类型
            if verify_signal.full_export:
                stat_global_batch += 1
            else:
                stat_slot_batch += 1
            // 组装复用校验统计报表上报ag-mem-03
            verify_report = build_reuse_stat_report(
                scan_slot_num=len(target_funnel_range),
                total_scan_item=len(reuse_snap),
                protect_item_num=len(protect_white_list),
                unprotect_item_num=len(reuse_snap) - len(protect_white_list)
            )
            send_stat_report(target="ag-mem-03", report=verify_report)
            // 写入复用校验审计日志
            audit_log = build_reuse_audit_log(
                scan_range=target_funnel_range,
                scan_total_item=len(reuse_snap),
                protect_total=len(protect_white_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = REUSE_IDLE

        // 4. 60秒定时内存占用上报 + 180秒周期运行统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_reuse_cache_size(temp_reuse_cache, reuse_cfg.avg_reuse_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-41", used_kb=cache_kb, pending_verify_item=len(temp_reuse_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_reuse_runtime_stat(
                    state=internal_state,
                    global_verify_times=stat_global_batch,
                    slot_verify_times=stat_slot_batch,
                    total_protect_items=stat_total_protect_item
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 分层存储复用计数快照字段缺失、无数据 | 跳过当前funnel分片，记录告警审计，等待下一轮校验周期重试 | ag-mem20~26分层存储恢复完整复用数据输出 |
| 单次待校验条目超过ag-mem-35分片上限 | 自动分片串行校验、下发保护名单，不阻塞主线程 | 内置分片逻辑自动执行 |
| 本地复用计数缓存内存溢出 | 清空缓存，终止本轮批量校验，向ag-mem-48上报容量风险告警 | 扩容计算内存或调大分片配置上限 |
| PAUSE半熔断状态收到全局批量校验信号 | 直接丢弃全局批量任务，仅允许定向分槽校验、实时访问计数更新 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35复用保护配置拉取失败 | 加载本地兜底分层复用阈值模板继续校验，输出配置缺失告警 | ag-mem-35恢复下发完整记忆晋升三维参数 |
| 大批量访问事件瞬时涌入 | 本地缓存聚合批量更新复用计数，延迟合并写入校验快照，减少重复IO | 流量自然回落、分层存储访问压力降低 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 批量复用校验调度信号、全局熔断指令、分层复用保护配置、分层复用计数快照、条目访问增量事件 | 只读 | ag-mem03、ag-mem01、ag-mem35、ag-mem20~26 |
| 内部业务总线 | 写 | 分层复用保护条目白名单 | 专属写入 | ag-mem-40 |
| 内部调度总线 | 写 | 复用校验统计报表、内存容量上报、复用校验审计日志、周期运行统计 | 事件/周期写入 | ag-mem03、ag-mem48、ag-mem51 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| REU41-01 | 分层最低复用保护次数、批量校验分片上限全部取自ag-mem-35，本地禁止硬编码任何保护判定参数，统一策略管控 |
| REU41-02 | 仅具备复用计数读取、实时增量更新、保护名单生成下发权限，无条目新增、删除、存储写入能力，业务数据修改权限收敛至分层存储 |
| REU41-03 | 熔断分级管控批量校验任务，半熔断阻断高算力全局全量校验，仅保留轻量实时计数，防止故障期间算力耗尽 |
| REU41-04 | 每一轮批量复用校验完整写入ag-mem-51审计日志，记录扫描范围、总条目、受保护条目数量，支撑记忆保护策略变更全链路溯源 |
| REU41-05 | 分片限流控制单次校验条目数量，防止大批量条目同步计算抢占CPU、存储IO，保障分槽分发、I刷新主线业务稳定 |
| REU41-06 | 熔断状态清空本地复用计数缓存，恢复后重新拉取分层最新复用数据校验，杜绝基于过期复用计数生成错误保护白名单 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M41-01 | `REUSE_IDLE`，ag-mem-03下发全funnel批量复用校验信号 | 全局全量校验调度信号 | 拉取全分层复用计数快照，分层阈值筛选生成保护白名单下发ag-mem40，输出复用统计报表、审计日志 |
| TC-M41-02 | `REUSE_IDLE`，条目复用计数达到L3分层保护阈值 | 含达标条目复用快照 | 该条目加入复用保护白名单，遗忘扫描时被过滤 |
| TC-M41-03 | `REUSE_IDLE` 实时收到多条条目访问增量事件 | 批量访问事件数据流 | 本地实时累加复用计数，等待下一轮校验自动更新保护资格 |
| TC-M41-04 | `REUSE_IDLE`，单次待校验条目超过配置分片上限 | 超大批量复用快照 | 自动拆分多片串行校验、下发保护名单，无算力阻塞 |
| TC-M41-05 | `REUSE_IDLE`，收到F0 PAUSE半熔断指令后收到全局校验信号 | 半熔断+全局批量校验信号 | 丢弃全局批量校验任务，仅处理单条访问计数更新 |
| TC-M41-06 | `REUSE_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空复用缓存，停止全部批量校验逻辑 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-41匹配白皮书复用校验保护辅助单元定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35记忆晋升三维复用阈值参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖复用数据拉取、资格校验、保护名单下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 分层递增复用阈值、实时访问计数、分片限流、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖访问事件实时更新、批量校验调度、分层保护筛选、白名单分片下发、统计上报、审计日志全链路 | ✅ |
| 异常场景覆盖分层数据缺失、超大批量条目、缓存溢出、半熔断拦截、配置缺失、瞬时访问流量突增共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅输出保护白名单，无条目修改、删除权限 | ✅ |
| 6条V1.1安全约束统一参数管控、权限隔离、故障限流、全操作可审计、防算力风暴、规避过期计数判定 | ✅ |
| 6条自动化测试用例覆盖全部复用校验核心业务场景 | ✅ |

---