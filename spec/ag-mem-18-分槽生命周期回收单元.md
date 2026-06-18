# ag-mem-18 分槽生命周期回收单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书前置分槽配套管控模块）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-18 |
| 模块名称 | 分槽生命周期回收单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 前置分槽配套辅助单元（绑定ag-mem-15场景分槽主调度、协同ag-mem-16冷热监控、ag-mem-17负载限流） |
| 核心职责 | 统一管理全funnel分槽完整生命周期：新建监控、闲置判定、过期标记、资源回收销毁；定期拉取ag-mem-15分槽元数据、ag-mem-16冷热评分、ag-mem-17负载指标，综合判定闲置过期分槽；生成分槽销毁指令下发至ag-mem-15，释放内存元数据占用；同步向ag-mem-03推送分槽回收统计与资源释放告警；对外提供生命周期快照供ag-mem-37、ag-mem-40做分槽权重剔除；定时上报生命周期计算内存占用至ag-mem-48；所有分槽标记过期、销毁回收操作全量写入ag-mem-51审计日志；无原始经验存储、无流量分发、无记忆晋升逻辑，仅负责funnel分槽资源生命周期清理。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-15（场景分槽主调度，读取/销毁funnel分槽）、ag-mem-16（分槽冷热监控，读取冷热评分）、ag-mem-17（分槽负载限流，读取长期低负载指标）、ag-mem-35（三维权重配置单元，读取分槽闲置阈值、回收冷却周期、保护白名单）、ag-mem-48（全局容量配额管控，上报生命周期指标内存开销） |
| 被依赖模块 | ag-mem-15（接收分槽销毁指令，清理本地funnel元数据池）、ag-mem-03（漏斗二调度单元，接收分槽回收资源释放统计、闲置资源告警）、ag-mem-37（重要度定时刷新单元，过滤已销毁分槽数据）、ag-mem-40（遗忘阈值判定单元，剔除过期分槽扫描范围）、ag-mem-48（接收生命周期缓存内存定时上报）、ag-mem-51（记录分槽过期标记、销毁回收全流程审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 生命周期待机就绪 | `LC_IDLE` | 缓存空闲，等待定时生命周期扫描周期，无批量回收任务 | 系统初始化、熔断恢复、一轮全量分槽回收处理完毕 |
| 多源分槽指标拉取缓存 | `SLOT_DATA_FETCH` | 同步拉取ag-mem-15元数据、ag-mem-16冷热评分、ag-mem-17负载指标存入本地缓存 | 生命周期扫描定时周期倒计时归零 |
| 生命周期综合判定计算 | `LC_JUDGE_CALC` | 结合闲置时长、冷热评分、长期低负载、白名单保护判定待回收分槽，生成销毁指令清单 | 全部分槽多源指标拉取完成 |
| 回收指令与资源告警下发 | `LC_DISPATCH` | 批量推送分槽销毁指令至ag-mem-15，资源释放统计告警推送ag-mem-03 | 生命周期判定计算全部完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空本地分槽指标缓存，停止拉取、判定、回收指令下发 | F0下发FUSE熔断指令；RESUME切回LC_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 全量funnel分槽基础元数据快照 | List<Struct>（funnel_id、scene、user_space_id、create_ts、last_access_ts、manual_protect_tag） | ag-mem-15 场景分槽主调度 | 生命周期定时扫描主动拉取 | 高 |
| 全量分槽冷热评分指标快照 | List<Struct>（funnel_id、hot_score、cold_flag、idle_days） | ag-mem-16 分槽冷热监控辅助单元 | 同步拉取冷热判定数据用于闲置校验 | 高 |
| 全量分槽长期负载指标快照 | List<Struct>（funnel_id、avg_qps_7d、zero_traffic_days） | ag-mem-17 分槽负载限流管控单元 | 同步拉取流量指标判断长期无访问分槽 | 高 |
| 生命周期回收规则配置回执 | Struct（闲置过期天数、回收冷却缓冲天数、业务保护白名单funnel列表、单次最大回收分槽数量） | ag-mem-35 三维权重配置单元 | 模块初始化、生命周期回收策略更新 | 普通 |
| 分槽生命周期批量查询请求 | Struct（funnel_id列表 / 全量导出标记） | ag-mem-37 / ag-mem-40 | 全局I值重算、分层遗忘扫描过滤失效分槽 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、全局熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 待回收分槽批量销毁指令 | List<Struct>（funnel_id、destroy_reason、release_memory_kb） | ag-mem-15 场景分槽主调度 | 综合判定满足回收条件，且不在保护白名单 | 高 |
| 闲置分槽资源回收统计告警 | Struct（recycle_slot_count、total_release_kb、long_idle_slot_list、suggest_action） | ag-mem-03 漏斗二调度单元 | 本轮扫描存在可回收销毁分槽 | 普通 |
| 分槽完整生命周期元数据快照 | List<Struct>（funnel_id、idle_days、hot_score、7d_avg_qps、recycle_status、protect_flag） | ag-mem-37、ag-mem-40 | 收到生命周期批量查询请求 | 高 |
| 生命周期缓存内存占用上报 | Struct（单元标识ag-mem-18、指标缓存总KB、监控funnel总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、一轮回收扫描完成后即时上报 | 普通 |
| 生命周期回收审计日志 | Struct（事件类型、本轮标记过期分槽数量、实际销毁分槽数量、释放总内存KB、时间戳、场景分布） | ag-mem-51 记忆变更日志追溯单元 | 每一轮全生命周期扫描、销毁指令下发完成 | 普通 |
| 生命周期周期运行统计上报 | Struct（当前状态、今日累计销毁分槽总数、累计释放内存总量、白名单保护分槽数量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 分槽生命周期回收核心规则（V1.1前置资源轻量化规范）
### 1. 全局生命周期配置参数（ag-mem-35统一分发）
1. 闲置过期基准天数：90天，距上次访问超90天标记为待回收；
2. 回收冷却缓冲天数：7天，标记待回收后需等待7天才可执行销毁；
3. 长期无流量判定：7天平均QPS=0，加速进入待回收队列；
4. 单次扫描最大回收分槽上限：1500条，超量分片串行销毁；
5. 保护白名单：人工标记/核心业务funnel永久跳过回收流程。

### 2. 分槽待回收判定条件（全部满足才可标记待回收）
1. 距last_access_ts闲置时长 ≥ 90天；
2. hot_score＜20，持续冷分槽；
3. 7天平均写入QPS=0，长期无业务流量；
4. 不在ag-mem-35下发的业务保护白名单内；
5. 无manual_protect_tag人工锁定标记。

### 3. 分槽销毁执行条件
1. 已标记待回收，且等待冷却缓冲7天；
2. 当前无正在处理的原始数据写入队列；
3. 无正在执行的分槽路由分发任务。

### 4. 流转强制约束
1. 仅下发销毁指令，无权限直接删除ag-mem-15内部funnel元数据，由主调度统一执行清理；
2. 无原始经验读写、存储持久化、记忆晋升/归档能力，纯资源回收辅助单元；
3. 只读ag-mem15/16/17三类分槽指标，不修改任何分槽基础业务数据；
4. 单向链路：多源读取指标，仅输出销毁指令与资源告警，无反向业务数据写入。

### 5. 批量约束
单次生命周期扫描最多处理1500个funnel，超量自动分片串行判定、分批下发销毁指令，避免瞬时资源清理冲击业务链路。

## 核心处理逻辑
```
FUNCTION slot_lifecycle_recycle_main_loop():
    STATE_IDLE = LC_IDLE
    STATE_FETCH = SLOT_DATA_FETCH
    STATE_CALC = LC_JUDGE_CALC
    STATE_DISPATCH = LC_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    lc_cfg = query_lifecycle_config(from_m35="ag-mem-35")
    idle_expire_day = lc_cfg.idle_expire_day
    recycle_cool_day = lc_cfg.recycle_cool_day
    zero_traffic_day = lc_cfg.zero_traffic_detect_day
    protect_whitelist = lc_cfg.slot_protect_whitelist
    scan_cycle_sec = lc_cfg.lc_scan_interval
    scan_countdown = scan_cycle_sec
    temp_slot_cache = []
    slot_mark_recycle_map = {} // key:funnel_id, value:mark_ts
    stat_total_destroy = 0
    stat_total_release_kb = 0
    last_report_ts = NOW()
    max_recycle_batch = 1500

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_slot_cache.clear()
                slot_mark_recycle_map.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = LC_IDLE

        // 2. 定时全生命周期扫描流程
        IF internal_state == LC_IDLE:
            scan_countdown -= 10
            IF scan_countdown <= 0:
                internal_state = SLOT_DATA_FETCH
                // 多源同步拉取分槽指标
                meta_all = fetch_all_funnel_meta(target="ag-mem-15")
                hotcold_all = fetch_all_hotcold_data(target="ag-mem-16")
                load_all = fetch_all_slot_load_data(target="ag-mem-17")
                // 合并多源数据
                merged_slot_data = merge_multi_source_slot(meta_all, hotcold_all, load_all)
                temp_slot_cache = merged_slot_data
                internal_state = LC_JUDGE_CALC
                now_ts = NOW()
                destroy_cmd_list = []
                recycle_mark_count = 0
                slice_list = split_slice(temp_slot_cache, max_recycle_batch)

                for slice_slot in slice_list:
                    for slot in slice_slot:
                        f_id = slot.funnel_id
                        # 白名单/人工保护直接跳过
                        if f_id in protect_whitelist or slot.manual_protect_tag == "lock":
                            continue
                        idle_ms = now_ts - slot.last_access_ts
                        idle_days = idle_ms / (24 * 3600 * 1000)
                        # 判定待回收基础条件
                        cond_idle = idle_days >= idle_expire_day
                        cond_cold = slot.hot_score < 20
                        cond_zero_flow = slot.avg_qps_7d == 0
                        if cond_idle and cond_cold and cond_zero_flow:
                            # 未标记待回收：新增标记
                            if f_id not in slot_mark_recycle_map:
                                slot_mark_recycle_map[f_id] = now_ts
                                recycle_mark_count += 1
                                continue
                            # 已标记，校验冷却周期
                            mark_ts = slot_mark_recycle_map[f_id]
                            cool_wait_days = (now_ts - mark_ts) / (24 * 3600 * 1000)
                            if cool_wait_days >= recycle_cool_day:
                                # 满足销毁条件，生成销毁指令
                                est_release_kb = calc_slot_meta_kb(slot, lc_cfg.avg_meta_kb)
                                destroy_cmd_list.append({
                                    "funnel_id": f_id,
                                    "destroy_reason": f"闲置{idle_days:.1f}天，长期冷分槽无流量，冷却期满回收",
                                    "release_memory_kb": est_release_kb
                                })
                                stat_total_destroy += 1
                                stat_total_release_kb += est_release_kb
                temp_slot_cache.clear()
                internal_state = LC_DISPATCH

                // 下发批量销毁指令至ag-mem-15
                if len(destroy_cmd_list) > 0:
                    send_destroy_batch(target="ag-mem-15", cmd_list=destroy_cmd_list)
                    // 清理已销毁分槽标记
                    for cmd in destroy_cmd_list:
                        del slot_mark_recycle_map[cmd.funnel_id]
                // 推送资源回收告警至ag-mem-03
                alert_payload = build_recycle_alert(
                    recycle_num=len(destroy_cmd_list),
                    total_release_kb=stat_total_release_kb,
                    idle_slot_ids=[s.funnel_id for s in temp_slot_cache if s.funnel_id in slot_mark_recycle_map],
                    suggest="定期清理长期闲置业务分槽，释放内存资源"
                )
                send_recycle_alert(target="ag-mem-03", alert=alert_payload)
                // 写入生命周期审计日志
                audit_log = build_lc_audit_log(
                    scan_total=len(merged_slot_data),
                    new_mark_slot=recycle_mark_count,
                    destroy_slot=len(destroy_cmd_list),
                    release_kb=stat_total_release_kb,
                    ts=now_ts
                )
                send_audit_log(target="ag-mem-51", log_data=audit_log)
                scan_countdown = scan_cycle_sec
                internal_state = LC_IDLE

        // 3. 响应生命周期元数据批量查询
        IF 收到分槽生命周期批量查询请求:
            query_param = 获取查询参数
            meta_snap = []
            if query_param.full_export:
                full_merge_data = merge_multi_source_slot(
                    fetch_all_funnel_meta(target="ag-mem-15"),
                    fetch_all_hotcold_data(target="ag-mem-16"),
                    fetch_all_slot_load_data(target="ag-mem-17")
                )
                for slot in full_merge_data:
                    recycle_status = "normal"
                    if slot.funnel_id in slot_mark_recycle_map:
                        recycle_status = "wait_recycle"
                    meta_snap.append({
                        "funnel_id": slot.funnel_id,
                        "idle_days": (NOW() - slot.last_access_ts) / (24 * 3600 * 1000),
                        "hot_score": slot.hot_score,
                        "7d_avg_qps": slot.avg_qps_7d,
                        "recycle_status": recycle_status,
                        "protect_flag": slot.funnel_id in protect_whitelist or slot.manual_protect_tag == "lock"
                    })
            else:
                target_fids = query_param.funnel_id_list
                full_merge_data = merge_multi_source_slot(
                    fetch_all_funnel_meta(target="ag-mem-15"),
                    fetch_all_hotcold_data(target="ag-mem-16"),
                    fetch_all_slot_load_data(target="ag-mem-17")
                )
                fid_map = {item.funnel_id: item for item in full_merge_data}
                for fid in target_fids:
                    if fid in fid_map:
                        slot = fid_map[fid]
                        recycle_status = "wait_recycle" if fid in slot_mark_recycle_map else "normal"
                        meta_snap.append({
                            "funnel_id": fid,
                            "idle_days": (NOW() - slot.last_access_ts) / (24 * 3600 * 1000),
                            "hot_score": slot.hot_score,
                            "7d_avg_qps": slot.avg_qps_7d,
                            "recycle_status": recycle_status,
                            "protect_flag": fid in protect_whitelist or slot.manual_protect_tag == "lock"
                        })
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 4. 定时内存占用上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_lc_cache_kb(temp_slot_cache, lc_cfg.avg_slot_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-18", used_kb=cache_kb, monitor_slot_count=len(temp_slot_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                protect_count = len(protect_whitelist)
                stat_report = build_lc_stat_report(
                    state=internal_state,
                    total_destroy_slot=stat_total_destroy,
                    total_release_memory_kb=stat_total_release_kb,
                    white_list_protect_num=protect_count
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem15/16/17任意模块指标拉取失败 | 本轮扫描终止，告警写入ag-mem-51，等待下一周期重试 | 对应配套分槽辅助模块服务恢复输出完整指标 |
| 单次扫描funnel总量超1500条上限 | 自动分片串行判定、分批下发销毁指令，不阻塞主线程 | 内置分片逻辑自动执行 |
| 多源指标合并算力超时 | 当前分片全部跳过，记录告警，下一轮完整重扫 | 系统整体算力负载下降后正常执行判定 |
| 本地多源指标缓存内存溢出 | 清空缓存，跳过本轮扫描，向ag-mem-48上报容量风险告警 | 扩容计算内存或调长生命周期扫描周期 |
| 全局FUSE熔断触发 | 清空本地指标缓存、待回收标记表，停止拉取、判定、销毁指令下发 | ag-mem-01下发RESUME恢复指令 |
| 无生命周期回收配置 | 加载全局通用闲置90天、冷却7天规则兜底判定 | ag-mem-35运维侧补充分场景生命周期回收参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 分槽元数据、冷热指标、负载指标、生命周期规则、全局熔断指令 | 只读 | ag-mem-15、ag-mem-16、ag-mem-17、ag-mem-35、ag-mem-01 |
| 内部调度总线 | 读 | 分槽生命周期批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 分槽批量销毁指令 | 专属写入 | 下发至ag-mem-15 |
| 内部调度总线 | 写 | 闲置分槽资源回收统计告警 | 专属写入 | 下发至ag-mem-03 |
| 内部调度总线 | 写 | 生命周期元数据快照、容量上报、审计日志、周期运行统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| LC18-01 | 仅具备多源分槽指标只读权限，无直接删除funnel元数据权限，销毁操作通过指令交由ag-mem-15执行，权限分层隔离防止误删核心业务分槽 |
| LC18-02 | 无任何原始任务经验读写、持久存储能力，仅做分槽闲置数值判定，规避业务交互数据泄露风险 |
| LC18-03 | 闲置过期天数、冷却缓冲、保护白名单全部由ag-mem-35统一管控，本地无硬编码回收阈值 |
| LC18-04 | 标记待回收、下发销毁指令、资源释放统计全量写入ag-mem-51审计日志，留存funnel ID、闲置时长、预估释放内存，支撑资源回收溯源 |
| LC18-05 | 分片限制单次回收分槽数量，避免大批量同步销毁抢占主线程算力，保障记忆通路数据分发稳定 |
| LC18-06 | 熔断清空本地缓存与待回收标记，恢复后重新全量拉取最新指标，防止基于过期数据误销毁活跃分槽 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M18-01 | `LC_IDLE`，92天无访问、冷分槽、7天零流量funnel，不在白名单 | 多源合并分槽指标快照 | 第一轮扫描标记待回收，冷却7天后下发销毁指令至ag-mem-15 |
| TC-M18-02 | `LC_IDLE`，长期闲置funnel位于业务保护白名单 | 含白名单分槽指标快照 | 全程跳过标记、销毁流程，不生成回收指令 |
| TC-M18-03 | `LC_IDLE`，闲置95天但近7天存在流量写入 | 带流量分槽指标快照 | 不满足回收条件，不标记待回收 |
| TC-M18-04 | `LC_IDLE`，单次扫描2000个funnel分槽 | 超大批量多源指标快照 | 自动分片串行判定，分批下发销毁指令无阻塞 |
| TC-M18-05 | `LC_IDLE`，ag-mem-37下发指定funnel生命周期查询 | 分槽ID批量查询请求 | 返回闲置天数、冷热评分、流量、待回收状态完整快照 |
| TC-M18-06 | `LC_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空指标缓存与待回收标记，停止全部生命周期扫描回收流程 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-18匹配白皮书ag-mem-15配套生命周期回收单元定位 | ✅ |
| 上游只读ag-mem15/16/17多源指标，下游仅销毁指令+资源告警，数据流闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖指标拉取、综合判定、回收指令下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 闲置判定、冷却缓冲、白名单保护、分片回收规则严格对齐V1.1前置资源轻量化规范 | ✅ |
| 伪代码覆盖多源指标合并、闲置综合判定、待回收标记、分批销毁、资源告警、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖多源指标缺失、超大分槽总量、算力超时、缓存溢出、熔断、无回收配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅读取分槽指标，无直接删除funnel权限 | ✅ |
| 6条V1.1安全约束防止核心分槽误删、算力抢占、业务数据泄露、过期指标误回收 | ✅ |
| 6条自动化测试用例覆盖全部生命周期回收核心场景 | ✅ |

---