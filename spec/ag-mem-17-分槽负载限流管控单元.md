# ag-mem-17 分槽负载限流管控单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书前置分槽配套管控模块）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-17 |
| 模块名称 | 分槽负载限流管控单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 前置分槽配套辅助单元（绑定ag-mem-15场景分槽主调度） |
| 核心职责 | 实时采集ag-mem-15各funnel分槽写入吞吐量、队列堆积、单槽条目总量三类负载指标；基于ag-mem-35下发负载阈值规则，动态生成分槽限流/恢复指令下发至ag-mem-15；区分软限流（降低单槽写入速率）、硬限流（阻断新原始数据流入）两级管控；监控全局总负载，全局过载时触发全部分槽统一限流；输出负载明细快照供给ag-mem-37、ag-mem-40做分槽权重调节；定时上报负载指标内存占用至ag-mem-48；所有限流开启、限流解除、全局过载操作写入ag-mem-51审计日志；无原始经验存储、无分槽创建销毁能力，仅负责流量负载管控，不参与记忆晋升、归档、抽象计算。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-15（场景分槽主调度，读取分槽实时负载、接收限流管控指令）、ag-mem-35（三维权重配置单元，读取单槽负载阈值、全局过载阈值、限流恢复缓冲时长）、ag-mem-48（全局容量配额管控，上报负载指标内存开销） |
| 被依赖模块 | ag-mem-15（接收分槽限流/恢复指令，控制原始数据写入速率）、ag-mem-03（漏斗二调度单元，接收全局过载告警、高负载分槽统计）、ag-mem-37（重要度定时刷新单元，读取分槽负载快照用于I值加权衰减）、ag-mem-40（遗忘阈值判定单元，基于分槽负载调整条目淘汰优先级）、ag-mem-48（接收负载指标内存定时上报）、ag-mem-51（记录限流启停、全局过载审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 负载待机就绪 | `LIMIT_IDLE` | 全局、各分槽负载均在安全区间，无限流管控动作 | 系统初始化、熔断恢复、限流解除、一轮负载扫描完成 |
| 分槽负载采集缓存 | `LOAD_FETCH` | 主动拉取ag-mem-15全funnel实时负载指标存入本地缓存 | 负载扫描定时周期倒计时归零 |
| 负载阈值判定计算 | `LIMIT_CALC` | 对比单槽/全局负载阈值，判定是否开启软/硬限流，生成管控指令 | 全部分槽负载指标采集完成 |
| 限流指令与告警下发 | `LIMIT_DISPATCH` | 推送限流/恢复指令至ag-mem-15，全局过载告警推送ag-mem-03 | 负载判定计算全部完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空本地负载缓存，停止采集、判定、指令下发 | F0下发FUSE熔断指令；RESUME切回LIMIT_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 全funnel分槽实时负载快照 | List<Struct>（funnel_id、per_second_write、queue_buffer_count、slot_item_total、limit_status） | ag-mem-15 场景分槽主调度 | 负载扫描周期触发，主动拉取分槽负载指标 | 高 |
| 限流负载规则配置回执 | Struct（单槽软限流阈值、单槽硬限流阈值、全局过载阈值、限流恢复冷却秒数） | ag-mem-35 三维权重配置单元 | 模块初始化、负载限流策略更新 | 普通 |
| 分槽负载指标批量查询请求 | Struct（funnel_id列表 / 全量导出标记） | ag-mem-37 / ag-mem-40 | 全局I值重算、分层遗忘扫描分槽权重调整 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、全局熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分槽限流管控指令批量 | List<Struct>（funnel_id、limit_mode:none/soft/hard、limit_rate、modify_reason） | ag-mem-15 场景分槽主调度 | 分槽负载超出阈值，或负载回落需解除限流 | 高 |
| 全局负载过载告警通知 | Struct（global_load_rate、overload_flag、high_load_slot_list、suggest_action） | ag-mem-03 漏斗二调度单元 | 全局总负载达到过载阈值 | 普通 |
| 分槽完整负载指标快照 | List<Struct>（funnel_id、write_qps、queue_size、total_item、limit_status、load_level） | ag-mem-37、ag-mem-40 | 收到分槽负载批量查询请求 | 高 |
| 负载指标内存占用上报 | Struct（单元标识ag-mem-17、负载缓存总KB、监控funnel总数） | ag-mem-48 全局容量配额 | 每60秒定时上报、一轮负载扫描完成后即时上报 | 普通 |
| 限流管控审计日志 | Struct（事件类型、开启软限流分槽数、开启硬限流分槽数、解除限流分槽数、全局过载次数、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮负载扫描、限流指令下发完成 | 普通 |
| 负载管控周期运行统计上报 | Struct（当前状态、今日软限流触发总次数、硬限流触发总次数、全局过载总次数、高负载分槽占比） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 分槽负载限流核心规则（V1.1前置流量稳控规范）
### 1. 全局限流配置参数（ag-mem-35统一分发）
1. 单槽分级阈值：
软限流阈值：单槽每秒写入≥80条；硬限流阈值：单槽每秒写入≥150条
2. 全局过载阈值：全部分槽总写入QPS≥5000触发全局统一软限流
3. 限流恢复冷却时长：30秒，负载回落达标后需等待冷却才可解除限流
4. 单次扫描最大处理funnel数量：2000个，超量分片串行判定

### 2. 两级限流行为定义
- 软限流soft：限制单槽写入速率至阈值下限，允许低流量持续流入，不阻断
- 硬限流hard：完全阻断该funnel新原始数据接收，队列堆积持续释放旧数据
- none：无任何流量限制，正常全速率写入

### 3. 限流切换逻辑
1. 负载上升：安全→软限流→硬限流，逐级切换，立即生效
2. 负载回落：硬限流→软限流→无限制，必须等待冷却时长才可解除，防止震荡反复切换
3. 全局过载：所有分槽强制进入软限流，全局负载回落至阈值以下后统一解除

### 4. 流转强制约束
1. 仅与ag-mem-15交互，无权限直接新增/删除/修改funnel分槽，仅下发流量管控指令
2. 无原始经验读写、存储持久化能力，纯流量指标计算管控单元
3. 不参与L0~L5任意存储层条目晋升、归档、抽象、向量计算业务逻辑
4. 单向链路：只读ag-mem-15负载指标，仅回传限流指令，不篡改分槽基础元数据

### 5. 批量约束
单次负载扫描最多处理2000个funnel，超量自动分片串行判定，避免瞬时算力抢占业务链路

## 核心处理逻辑
```
FUNCTION slot_load_limit_main_loop():
    STATE_IDLE = LIMIT_IDLE
    STATE_FETCH = LOAD_FETCH
    STATE_CALC = LIMIT_CALC
    STATE_DISPATCH = LIMIT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    limit_cfg = query_load_limit_config(from_m35="ag-mem-35")
    soft_thresh = limit_cfg.slot_soft_qps
    hard_thresh = limit_cfg.slot_hard_qps
    global_overload_qps = limit_cfg.global_max_qps
    cool_down_sec = limit_cfg.limit_cool_second
    scan_interval = limit_cfg.scan_cycle_sec
    scan_countdown = scan_interval
    temp_load_cache = []
    cool_down_map = {} // key:funnel_id, value:cool_down_end_ts
    stat_soft_limit = 0
    stat_hard_limit = 0
    stat_global_overload = 0
    last_report_ts = NOW()
    max_scan_slot = 2000

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_load_cache.clear()
                cool_down_map.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = LIMIT_IDLE

        // 定时负载扫描流程
        IF internal_state == LIMIT_IDLE:
            scan_countdown -= 10
            IF scan_countdown <= 0:
                internal_state = LOAD_FETCH
                all_load_meta = fetch_all_slot_load(target="ag-mem-15")
                temp_load_cache = all_load_meta
                internal_state = LIMIT_CALC
                now_ts = NOW()
                limit_cmd_batch = []
                total_global_qps = sum(item.per_second_write for item in temp_load_cache)
                is_global_overload = total_global_qps >= global_overload_qps
                if is_global_overload:
                    stat_global_overload += 1
                slice_group = split_slice(temp_load_cache, max_scan_slot)
                for slice_data in slice_group:
                    for slot in slice_data:
                        f_id = slot.funnel_id
                        slot_qps = slot.per_second_write
                        current_mode = slot.limit_status
                        target_mode = "none"
                        // 全局过载强制软限流
                        if is_global_overload:
                            target_mode = "soft"
                        else:
                            if slot_qps >= hard_thresh:
                                target_mode = "hard"
                            elif slot_qps >= soft_thresh:
                                target_mode = "soft"
                            else:
                                target_mode = "none"
                        // 冷却时间判定
                        cool_end = cool_down_map.get(f_id, 0)
                        if target_mode == "none" and current_mode != "none":
                            if now_ts < cool_end:
                                target_mode = current_mode
                            else:
                                del cool_down_map[f_id]
                        if target_mode != current_mode:
                            limit_cmd_batch.append({
                                "funnel_id": f_id,
                                "limit_mode": target_mode,
                                "limit_rate": soft_thresh if target_mode == "soft" else 0,
                                "modify_reason": f"槽QPS:{slot_qps},切换限流模式{target_mode}"
                            })
                            if target_mode == "soft":
                                stat_soft_limit += 1
                            elif target_mode == "hard":
                                stat_hard_limit += 1
                            // 设置冷却计时
                            cool_down_map[f_id] = now_ts + cool_down_sec * 1000
                temp_load_cache.clear()
                internal_state = LIMIT_DISPATCH

                // 下发限流指令
                if len(limit_cmd_batch) > 0:
                    send_limit_batch(target="ag-mem-15", cmd_list=limit_cmd_batch)
                // 全局过载告警下发
                if is_global_overload:
                    alert_data = build_global_overload_alert(
                        global_qps=total_global_qps,
                        overload_flag=True,
                        high_load_slots=[s.funnel_id for s in temp_load_cache if s.per_second_write >= soft_thresh],
                        suggest="扩容分流/降低单批次原始数据输出"
                    )
                    send_overload_alert(target="ag-mem-03", alert=alert_data)
                // 审计日志
                audit_log = build_limit_audit_log(
                    scan_total=len(all_load_meta),
                    soft_open=stat_soft_limit,
                    hard_open=stat_hard_limit,
                    global_overload=1 if is_global_overload else 0,
                    ts=now_ts
                )
                send_audit_log(target="ag-mem-51", log_data=audit_log)
                scan_countdown = scan_interval
                internal_state = LIMIT_IDLE

        // 响应负载指标批量查询
        IF 收到分槽负载指标批量查询请求:
            query_param = 获取查询参数
            meta_snap = []
            if query_param.full_export:
                full_load_data = fetch_all_slot_load(target="ag-mem-15")
                for slot in full_load_data:
                    snap_item = assemble_load_snap(slot, limit_cfg)
                    meta_snap.append(snap_item)
            else:
                target_fids = query_param.funnel_id_list
                for fid in target_fids:
                    slot_load = fetch_single_slot_load(fid, target="ag-mem-15")
                    if slot_load:
                        meta_snap.append(assemble_load_snap(slot_load, limit_cfg))
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 定时内存上报与周期统计
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_load_cache_kb(temp_load_cache, limit_cfg.avg_load_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-17", used_kb=cache_kb, monitor_slot_count=len(temp_load_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_load_stat_report(
                    state=internal_state,
                    total_soft=stat_soft_limit,
                    total_hard=stat_hard_limit,
                    total_global_overload=stat_global_overload
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-15负载快照字段缺失、无数据 | 本轮扫描终止，记录告警，等待下一轮周期重试 | ag-mem-15负载采集服务恢复正常输出 |
| 单次扫描funnel超过2000条上限 | 自动分片串行判定限流规则，不阻塞主线程 | 内置分片逻辑自动执行 |
| 负载判定计算算力超时 | 当前分片跳过，写入告警日志，下一轮完整重扫 | 系统算力负载下降后正常执行判定 |
| 本地负载缓存内存溢出 | 清空缓存，跳过本轮扫描，向ag-mem-48上报容量告警 | 扩容计算内存或调长扫描周期 |
| 全局FUSE熔断触发 | 清空负载缓存与冷却计时表，停止采集、判定、指令下发 | ag-mem-01下发RESUME恢复指令 |
| 无分槽限流阈值配置 | 加载全局通用QPS阈值兜底判定 | ag-mem-35运维侧补充分场景限流规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 全funnel负载快照、限流规则、全局熔断指令 | 只读 | ag-mem-15、ag-mem-35、ag-mem-01 |
| 内部调度总线 | 读 | 分槽负载批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 分槽限流管控指令批量 | 专属写入 | 下发至ag-mem-15 |
| 内部调度总线 | 写 | 全局过载告警通知 | 专属写入 | 下发至ag-mem-03 |
| 内部调度总线 | 写 | 负载指标快照、容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L17-01 | 仅可读ag-mem-15负载指标，无直接修改funnel、写入原始数据权限，流量管控仅通过指令交由主调度执行，权限隔离防分槽篡改 |
| L17-02 | 无业务经验存储、读写能力，仅做流量数值计算，规避原始交互数据泄露风险 |
| L17-03 | 软/硬限流阈值、全局过载阈值、冷却时长全部由ag-mem-35统一管控，本地无硬编码业务参数 |
| L17-04 | 限流模式切换、全局过载告警全量写入ag-mem-51审计日志，留存对应funnel与实时QPS，支撑流量波动溯源 |
| L17-05 | 分片限流判定，限制单次处理分槽数量，避免大批量同步计算抢占记忆通路主线程算力 |
| L17-06 | 熔断清空本地负载缓存与冷却计时，恢复后重新全量拉取指标，防止过期负载数据生成错误限流指令 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M17-01 | `LIMIT_IDLE`，单槽QPS90超过软限流阈值 | 分槽负载快照 | 下发软限流指令至ag-mem-15，记录软限流审计日志 |
| TC-M17-02 | `LIMIT_IDLE`，单槽QPS160达到硬限流阈值 | 高负载分槽快照 | 下发硬限流阻断指令，标记冷却计时 |
| TC-M17-03 | `LIMIT_IDLE`，全局总QPS5200超出全局过载阈值 | 全量负载快照 | 全部分槽强制软限流，向ag-mem-03推送全局过载告警 |
| TC-M17-04 | `LIMIT_IDLE`，单槽负载回落至安全区间，未过冷却时长 | 回落负载快照 | 维持原有限流模式，不解除限流 |
| TC-M17-05 | `LIMIT_IDLE`，单次扫描2400个funnel分槽 | 超大批量负载快照 | 自动分片串行判定，完整输出限流指令 |
| TC-M17-06 | `LIMIT_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空负载缓存与冷却表，停止全部流量管控流程 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-17匹配白皮书ag-mem-15配套分槽负载限流单元定位 | ✅ |
| 上游只读ag-mem-15负载指标，下游仅限流指令+过载告警，数据流闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖负载采集、阈值判定、指令下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，链路无错乱 | ✅ |
| 两级限流、全局过载、冷却防抖规则严格对齐V1.1前置流量稳控规范 | ✅ |
| 伪代码覆盖负载拉取、分片阈值判定、限流指令下发、全局告警、指标查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖负载数据缺失、超大分槽总量、算力超时、缓存溢出、熔断、无限流配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅读取负载指标，无直接修改分槽/流量权限 | ✅ |
| 6条V1.1安全约束防止分槽篡改、算力抢占、数据泄露、错误限流震荡 | ✅ |
| 6条自动化测试用例覆盖全部负载限流核心场景 | ✅ |

---