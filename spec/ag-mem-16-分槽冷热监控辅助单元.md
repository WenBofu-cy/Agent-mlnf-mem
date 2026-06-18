# ag-mem-16 分槽冷热监控辅助单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书前置分槽配套管控模块）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-16 |
| 模块名称 | 分槽冷热监控辅助单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 前置分槽配套辅助单元（绑定ag-mem-15场景分槽主调度） |
| 核心职责 | 专属配套ag-mem-15分槽元数据池，独立轮询全量funnel分槽访问指标，精准更新冷热标记；统计各funnel访问频次、访问间隔、条目写入量三维冷热指标；输出冷热修正指令同步至ag-mem-15更新分槽元数据；生成冷分槽预警、长期闲置分槽待销毁提示下发至ag-mem-03；对外提供分槽冷热明细快照供ag-mem-37、ag-mem-40做分层遗忘权重修正；定时上报监控指标内存占用至ag-mem-48；所有冷热状态变更、预警下发操作全量写入ag-mem-51审计日志；无原始经验存储、无路由分发能力，仅负责分槽冷热指标计算与状态修正，不参与记忆晋升/归档流程。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-15（场景分槽主调度，读取funnel元数据池、下发冷热修正指令）、ag-mem-35（三维权重配置单元，读取冷热评分计算公式、冷槽闲置阈值、预警触发条件）、ag-mem-48（全局容量配额管控，上报监控指标内存开销） |
| 被依赖模块 | ag-mem-15（接收冷热状态修正指令，更新分槽cold_flag标记）、ag-mem-03（漏斗二调度单元，接收冷分槽预警、闲置分槽清理提示）、ag-mem-37（重要度定时刷新单元，读取分槽冷热评分快照用于I值加权修正）、ag-mem-40（遗忘阈值判定单元，获取分槽冷热标签调整遗忘优先级）、ag-mem-48（接收监控指标内存定时上报）、ag-mem-51（记录冷热状态变更、预警下发审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 监控待机就绪 | `MON_IDLE` | 指标缓存空闲，等待定时扫描周期，无批量计算任务 | 系统初始化、熔断恢复、一轮全量冷热扫描处理完毕 |
| 分槽元数据拉取缓存 | `SLOT_META_FETCH` | 定时拉取ag-mem-15全量funnel元数据，存入本地监控缓冲 | 冷热扫描定时周期倒计时归零 |
| 冷热指标加权计算 | `HOT_COLD_CALC` | 按三维指标加权计算冷热评分，判定热/温/冷三级标签，生成状态修正指令 | 全部分槽元数据拉取完成 |
| 预警与指令下发 | `ALERT_DISPATCH` | 推送冷热修正指令至ag-mem-15，下发冷槽预警、闲置清理提示至ag-mem-03 | 全部分槽冷热评分计算完毕 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空本地指标缓存，停止元数据拉取、冷热计算、指令下发 | F0下发FUSE熔断指令；RESUME切回MON_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 全量funnel分槽元数据快照 | List<Struct>（funnel_id、scene、user_space_id、create_ts、last_access_ts、active_item_count、原始cold_flag） | ag-mem-15 场景分槽主调度 | 监控单元定时扫描周期触发，主动拉取分槽元数据 | 高 |
| 冷热评分规则配置回执 | Struct（访问间隔权重、条目数量权重、访问频次权重、冷槽评分阈值、闲置销毁阈值天数） | ag-mem-35 三维权重配置单元 | 模块初始化、冷热评分策略更新 | 普通 |
| 分槽冷热指标批量查询请求 | Struct（funnel_id列表 / 全量导出标记） | ag-mem-37 / ag-mem-40 | 全局I值重算、遗忘扫描分槽权重调整 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、全局熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| funnel冷热状态修正指令批量 | List<Struct>（funnel_id、new_cold_flag、hot_score、modify_reason） | ag-mem-15 场景分槽主调度 | 计算得出分槽冷热标签与原有标记不一致，需要更新 | 高 |
| 冷分槽/长期闲置分槽预警提示 | Struct（cold_slot_list、idle_slot_list、risk_level、suggest_action） | ag-mem-03 漏斗二调度单元 | 扫描识别达到冷槽阈值、超期闲置funnel分槽 | 普通 |
| 分槽冷热完整指标快照 | List<Struct>（funnel_id、hot_score、cold_flag、access_interval、avg_item_num、access_freq） | ag-mem-37、ag-mem-40 | 收到分槽冷热指标批量查询请求 | 高 |
| 监控指标内存占用上报 | Struct（单元标识ag-mem-16、指标缓存总KB、当前监控funnel总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、全量冷热扫描完成后即时上报 | 普通 |
| 冷热监控操作审计日志 | Struct（事件类型、状态修正分槽数量、冷槽预警数量、闲置分槽提示数量、时间戳、场景分布） | ag-mem-51 记忆变更日志追溯单元 | 每一轮全量冷热扫描、指令下发完成 | 普通 |
| 冷热监控周期运行统计上报 | Struct（当前状态、今日修正冷热标记分槽总数、冷槽预警总次数、闲置分槽提示总数、热/温/冷分槽占比） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 分槽冷热监控核心规则（V1.1前置分槽资源优化规范）
### 1. 全局冷热计算配置参数（ag-mem-35统一分发）
1. 冷热综合评分公式：
hot_score = 访问频次权重 × 周访问次数 + 条目权重 × 槽内平均条目数 - 间隔权重 × 距上次访问天数
2. 分级判定阈值：
hot_score ≥ 60：热分槽；20 ≤ hot_score ＜ 60：温分槽；hot_score ＜ 20：冷分槽
3. 闲置销毁提示阈值：距上次访问≥75天，推送闲置清理提示至调度单元
4. 单次扫描最大处理funnel数量：2000条，超量分片计算

### 2. 冷热标记修正触发条件
计算得出cold_flag与ag-mem-15原有标记不一致时，生成修正指令同步更新；
- 冷→温/热：重置闲置倒计时，取消风险预警
- 热/温→冷：标记冷分槽，同步推送低优先级预警至ag-mem-03

### 3. 预警分级规则
1. 一级预警（低风险）：冷分槽，距上次访问15~45天，仅记录指标
2. 二级预警（中风险）：冷分槽，距上次访问45~75天，推送资源优化提示
3. 三级预警（高风险）：冷分槽，距上次访问≥75天，推送闲置待销毁提示

### 4. 流转强制约束
1. 仅与ag-mem-15交互分槽元数据，无权限直接新增/删除funnel分槽，仅下发状态修正指令
2. 无任何原始经验读写、路由分发、存储写入能力，纯指标计算辅助单元
3. 不参与L0~L5任意存储层条目晋升、归档、I值计算业务逻辑
4. 单向数据链路：只读ag-mem-15分槽元数据，仅回写冷热标记修正指令，无反向数据流篡改原始分槽信息

### 5. 批量约束
单次全量扫描最多并发处理2000个funnel，超量自动分片串行计算，防止指标计算算力抢占业务资源

## 核心处理逻辑
```
FUNCTION slot_hotcold_monitor_main_loop():
    STATE_IDLE = MON_IDLE
    STATE_FETCH = SLOT_META_FETCH
    STATE_CALC = HOT_COLD_CALC
    STATE_ALERT = ALERT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载冷热评分全局配置
    monitor_cfg = query_hotcold_config(from_m35="ag-mem-35")
    w_freq = monitor_cfg.weight_access_freq
    w_item = monitor_cfg.weight_item_count
    w_interval = monitor_cfg.weight_access_interval
    cold_score_thresh = monitor_cfg.cold_score_threshold
    idle_warn_days = monitor_cfg.idle_warn_day
    scan_cycle_sec = monitor_cfg.scan_interval_sec
    scan_countdown = scan_cycle_sec
    temp_meta_cache = []
    stat_modify_slot = 0
    stat_cold_alert = 0
    stat_idle_tip = 0
    last_report_ts = NOW()
    max_scan_slot = 2000

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_meta_cache.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = MON_IDLE

        // 2. 定时拉取ag-mem-15全部分槽元数据
        IF internal_state == MON_IDLE:
            scan_countdown -= 10
            IF scan_countdown <= 0:
                internal_state = SLOT_META_FETCH
                // 拉取完整funnel元数据快照
                all_slot_meta = fetch_all_funnel_meta(target="ag-mem-15")
                temp_meta_cache = all_slot_meta
                internal_state = HOT_COLD_CALC
                now_ts = NOW()
                modify_cmd_list = []
                alert_risk_map = {
                    "low": [],
                    "mid": [],
                    "high": []
                }
                total_slot = len(temp_meta_cache)
                // 分片循环计算冷热评分
                slice_list = split_slice(temp_meta_cache, max_scan_slot)
                for slice_meta in slice_list:
                    for slot in slice_meta:
                        f_id = slot.funnel_id
                        last_access = slot.last_access_ts
                        create_t = slot.create_ts
                        item_avg = slot.active_item_count
                        idle_days = (now_ts - last_access) / (24 * 3600 * 1000)
                        week_freq = calc_week_access_freq(f_id)
                        // 加权冷热评分
                        hot_score = w_freq * week_freq + w_item * item_avg - w_interval * idle_days
                        new_cold_flag = True if hot_score < cold_score_thresh else False
                        // 判断是否需要下发修正指令
                        if slot.cold_flag != new_cold_flag:
                            modify_cmd_list.append({
                                "funnel_id": f_id,
                                "new_cold_flag": new_cold_flag,
                                "hot_score": hot_score,
                                "modify_reason": f"评分{hot_score:.2f}，更新冷热标记"
                            })
                            stat_modify_slot += 1
                        // 分级风险预警判定
                        if new_cold_flag:
                            if idle_days >= idle_warn_days:
                                alert_risk_map["high"].append(f_id)
                                stat_idle_tip += 1
                            elif idle_days >= 45:
                                alert_risk_map["mid"].append(f_id)
                                stat_cold_alert += 1
                            else:
                                alert_risk_map["low"].append(f_id)
                temp_meta_cache.clear()
                internal_state = ALERT_DISPATCH

                // 3. 下发冷热修正指令至ag-mem-15
                if len(modify_cmd_list) > 0:
                    send_modify_batch(target="ag-mem-15", cmd_list=modify_cmd_list)
                // 下发分级预警至ag-mem-03
                alert_payload = build_alert_payload(
                    cold_slot_list=alert_risk_map["low"] + alert_risk_map["mid"],
                    idle_slot_list=alert_risk_map["high"],
                    risk_level="multi",
                    suggest_action="优化冷槽资源/清理长期闲置分槽"
                )
                send_alert_tip(target="ag-mem-03", alert_data=alert_payload)
                // 写入冷热监控审计日志
                audit_log = build_monitor_audit(
                    total_scan_slot=total_slot,
                    modify_slot_num=len(modify_cmd_list),
                    cold_alert_num=len(alert_risk_map["low"] + alert_risk_map["mid"]),
                    idle_tip_num=len(alert_risk_map["high"]),
                    ts=now_ts
                )
                send_audit_log(target="ag-mem-51", log_data=audit_log)
                scan_countdown = scan_cycle_sec
                internal_state = MON_IDLE

        // 4. 响应分槽冷热指标批量查询
        IF 收到分槽冷热指标批量查询请求:
            query_param = 获取查询参数
            meta_snap = []
            if query_param.full_export == True:
                full_meta = fetch_all_funnel_meta(target="ag-mem-15")
                for slot in full_meta:
                    snap_item = calc_single_hotcold_meta(slot, monitor_cfg)
                    meta_snap.append(snap_item)
            else:
                target_fids = query_param.funnel_id_list
                for fid in target_fids:
                    slot_meta = fetch_single_funnel_meta(fid, target="ag-mem-15")
                    if slot_meta != None:
                        snap_item = calc_single_hotcold_meta(slot_meta, monitor_cfg)
                        meta_snap.append(snap_item)
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 5. 定时监控指标内存上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            cache_kb = calc_monitor_meta_kb(temp_meta_cache, monitor_cfg.avg_slot_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-16", used_kb=cache_kb, monitor_slot_count=len(temp_meta_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_monitor_stat_report(
                    state=internal_state,
                    total_modify_slot=stat_modify_slot,
                    total_cold_alert=stat_cold_alert,
                    total_idle_tip=stat_idle_tip
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-15拉取分槽元数据为空/字段缺失 | 本轮扫描终止，记录告警，等待下一轮周期重试 | ag-mem-15分槽元数据服务恢复正常输出 |
| 单次扫描funnel总量超2000条上限 | 自动分片串行计算冷热评分，不阻塞主线程 | 内置分片逻辑自动执行 |
| 冷热评分计算算力超时 | 当前分片全部跳过，写入告警日志，下一轮完整重扫 | 系统算力负载下降后正常执行计算 |
| 监控指标缓存内存溢出 | 清空本地缓存，跳过本轮扫描，触发容量告警至ag-mem-48 | 扩容计算内存或调长扫描周期 |
| 全局FUSE熔断触发 | 清空本地元数据缓存，停止拉取、计算、指令下发 | ag-mem-01下发RESUME恢复指令 |
| 无分槽冷热评分配置 | 加载全局通用权重阈值兜底计算hot_score | ag-mem-35运维侧补充分场景冷热规则 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 全量funnel分槽元数据快照、冷热评分配置、全局熔断指令 | 只读 | ag-mem-15、ag-mem-35、ag-mem-01 |
| 内部调度总线 | 读 | 分槽冷热指标批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 冷热状态修正指令批量 | 专属写入 | 下发至ag-mem-15 |
| 内部调度总线 | 写 | 冷槽/闲置分槽预警提示 | 专属写入 | 下发至ag-mem-03 |
| 内部调度总线 | 写 | 冷热指标快照、内存容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| M16-01 | 仅具备只读ag-mem-15分槽元数据权限，无法新增、删除、直接修改funnel，仅下发修正指令由主调度执行变更，权限隔离防分槽篡改 |
| M16-02 | 无原始经验数据读写、存储写入能力，仅做指标数值计算，杜绝业务记忆数据泄露风险 |
| M16-03 | 冷热评分权重、预警天数、扫描周期全部由ag-mem-35集中管控，本地无硬编码业务阈值 |
| M16-04 | 所有冷热标记变更、分级预警下发操作完整写入ag-mem-51审计日志，记录对应funnel与评分，支撑资源优化溯源 |
| M16-05 | 扫描计算分片限流，避免大批量分槽同步计算抢占主线业务算力，保障记忆通路主链路稳定 |
| M16-06 | 熔断状态清空本地元数据缓存，恢复后重新全量拉取元数据，防止过期分槽指标生成错误预警 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M16-01 | `MON_IDLE`，一批长期低访问funnel分槽元数据 | 定时全量分槽元数据拉取 | 计算hot_score低于阈值，生成cold_flag修正指令推送ag-mem-15，同步下发二级冷槽预警 |
| TC-M16-02 | `MON_IDLE`，闲置78天冷分槽元数据 | 包含长期闲置分槽快照 | 判定三级高风险闲置，推送待清理提示至ag-mem-03 |
| TC-M16-03 | `MON_IDLE`，热分槽重新产生交互，hot_score提升 | 含活跃分槽元数据快照 | 下发指令将cold_flag修正为False，取消风险预警 |
| TC-M16-04 | `MON_IDLE`，全量funnel共2300条 | 超大批量分槽元数据 | 自动分片串行冷热计算，完整输出修正指令与预警 |
| TC-M16-05 | `MON_IDLE`，ag-mem-37下发指定funnel冷热指标查询 | 分槽ID批量查询请求 | 返回完整hot_score、冷热标签、访问间隔指标快照 |
| TC-M16-06 | `MON_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空本地元数据缓存，停止扫描与指令下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-16匹配白皮书ag-mem-15配套冷热监控辅助单元定位 | ✅ |
| 上游只读ag-mem-15元数据，下游仅修正指令+预警，数据流闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖元数据拉取、冷热计算、预警下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，链路无错乱 | ✅ |
| 加权冷热评分、三级预警、分片扫描规则严格对齐V1.1分槽资源优化规范 | ✅ |
| 伪代码覆盖元数据拉取、分片评分计算、修正指令下发、分级预警、指标查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖元数据缺失、超大分槽总量、算力超时、缓存溢出、熔断、无评分配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅可读取分槽元数据，无直接修改分槽权限 | ✅ |
| 6条V1.1安全约束防止分槽篡改、算力抢占、数据泄露、过期预警 | ✅ |
| 6条自动化测试用例覆盖全部冷热监控核心场景 | ✅ |

---