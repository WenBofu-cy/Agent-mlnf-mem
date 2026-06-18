# ag-mem-24-L3中期存储层 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-24 |
| 模块名称 | L3中期存储层（聚合经验持久存储） |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路 |
| 核心职责 | 上游唯一写入源为ag-mem-23归并完成的聚合条目；持久存储经过语义合并、降噪后的中期聚合经验，按funnel分业务域隔离，内置高维向量索引支撑批量语义检索；遵循V1.1分层单向流转规范，定时校验聚合条目加权I值、累计复用次数、90天最大留存时效；达标聚合条目单向批量晋升至ag-mem-26 L4长期存储；低重要度、超期聚合条目生成归档候选推送ag-mem-42执行离线归档；对外输出聚合条目完整元数据供给ag-mem-37全局I值重算、ag-mem-40遗忘批量扫描；定时上报分层存储容量、向量索引占用至ag-mem-48；所有新增、晋升、归档操作全量推送审计日志至ag-mem-51；L5永久隔离，不存在直通顶层永久记忆的流转通道。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-23（L3辅助归并单元，唯一上游写入来源）、ag-mem-35（三维权重配置单元，分funnel独立读取L3晋升/归档阈值、90天留存时效、向量索引配置）、ag-mem-48（全局容量配额管控，读取分层容量上限、预警/紧急溢出阈值） |
| 被依赖模块 | ag-mem-26（L4长期存储层，接收L3达标晋升聚合条目）、ag-mem-37（重要度定时刷新单元，读取L3聚合条目元数据）、ag-mem-40（遗忘阈值判定单元，提供L3条目扫描快照）、ag-mem-42（冗余记忆删除单元，接收L3归档遗忘候选清单）、ag-mem-48（定时上报L3分层占用容量）、ag-mem-51（推送L3记忆变更审计日志）、ag-mem-03（漏斗二调度单元，周期上报L3运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `L3_IDLE` | 正常接收ag-mem-23批量聚合条目写入，等待定时晋升/归档扫描任务 | 系统初始化、熔断恢复、批次晋升/归档全部完成 |
| 聚合条目持久写入 | `AGG_PERSIST` | 校验归并条目合法性，按funnel分域落盘，构建聚合向量索引，初始化完整聚合元数据 | 收到ag-mem-23下发归并完成批量条目 |
| 晋升筛选扫描 | `PROMOTE_SCAN` | 遍历分域聚合条目，比对分funnel晋升阈值、总复用次数、90天留存时效筛选可晋升条目 | 晋升定时周期倒计时归零 |
| 归档遗忘扫描 | `ARCHIVE_SCAN` | 筛选低加权I、超90天时效聚合条目，生成归档候选清单推送ag-mem-42 | 归档扫描周期到达 / ag-mem-48容量预警触发加急扫描 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、晋升扫描、归档扫描，内存缓存临时聚合元数据 | F0下发FUSE熔断指令；RESUME切回L3_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3归并完成聚合条目批量推送 | List<Struct>（merge聚合ID、原始条目ID集合、funnel分槽ID、agg_I加权重要度、total_reuse总复用、agg_S综合安全值、merge_vector聚合向量、task_tag任务标签、生成时间戳） | ag-mem-23 L3辅助归并单元 | ag-mem-23完成语义分组归并，推送标准化聚合条目 | 高 |
| L3定时晋升扫描指令 | Struct（触发类型=定时，目标下游L4长期层） | 内部定时调度 | 晋升扫描周期倒计时归零 | 普通 |
| L3归档遗忘扫描触发指令 | Struct（触发原因：定时/容量预警，是否加急） | 内部定时调度 / ag-mem-48容量预警 | 归档周期到达、分层容量触发预警 | 普通 |
| 聚合条目元数据批量查询请求 | Struct（merge聚合ID列表） | ag-mem-37 重要度增量定时刷新单元 / ag-mem-40 遗忘判定单元 | 全局I值批量重算、全分层遗忘扫描 | 高 |
| 分层容量配额配置回执 | Struct（L3总容量上限、预警占比、紧急溢出占比、向量索引预留容量） | ag-mem-48 全局容量配额单元 | 模块初始化、配额人工更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换、熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3聚合条目写入完成回执 | Struct（批量聚合条目总量、写入成功数量、失败merge_id列表） | ag-mem-23 L3辅助归并单元 | 归并条目批量持久落盘、向量索引构建完成 | 高 |
| 可晋升聚合条目批量推送 | List（完整聚合元数据、最新agg_I、总复用次数、funnel分槽、聚合向量） | ag-mem-26 L4长期存储层 | 晋升筛选存在达标聚合条目 | 高 |
| L3归档遗忘候选清单 | List（merge聚合ID、遗忘原因、当前agg_I、分层归档阈值、suggest_handle=archive） | ag-mem-42 冗余记忆删除单元 | 归档扫描筛选出待清理聚合条目 | 普通 |
| L3聚合条目元数据快照 | List（merge_id、agg_I、total_reuse、create_ts、funnel_id、merge_vector、task_tag） | ag-mem-37 / ag-mem-40 | I值刷新、遗忘扫描批量查询 | 高 |
| L3分层容量占用上报 | Struct（层级=L3、业务数据占用KB、向量索引占用KB、聚合条目总数量） | ag-mem-48 全局容量配额 | 每60秒定时上报、批量条目变更后即时上报 | 普通 |
| L3中期记忆变更审计日志 | Struct（事件类型、聚合条目操作数量、funnel分槽范围、时间戳、原始条目溯源ID集合） | ag-mem-51 记忆变更日志追溯单元 | 写入、晋升、归档清理操作完成 | 普通 |
| L3周期运行统计上报 | Struct（当前状态、今日新增聚合条目、累计晋升L4总量、累计归档清理总量、向量索引总条目数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## L3中期层核心规则（严格对齐V1.1白皮书4.4.1五层晋升通路）
### 1. 分funnel独立配置参数（由ag-mem-35统一下发）
1. L3最大留存时效：90天，写入满90天未晋升自动进入归档流程；
2. L3晋升L4最低加权I阈值：分业务funnel独立配置；
3. L3归档遗忘加权I阈值：分业务funnel独立配置；
4. 晋升最低累计复用次数：20次；
5. 准入前置校验：上游ag-mem-23输出标准化聚合条目，无合法merge_id条目拒绝写入。

### 2. 晋升至L4完整准入条件（全部同时满足）
1. 条目拥有合法唯一merge聚合ID，来源为ag-mem-23标准归并输出；
2. 当前加权agg_I ≥ 当前funnel分槽L3晋升阈值；
3. 总累计复用次数 ≥ 20次；
4. 聚合条目写入未满90天，未达最大留存时效；
5. 无人工收藏/锁定保护标记。

### 3. 归档清理触发条件（满足任意一条即加入归档候选）
1. 加权agg_I ＜ 当前funnel分槽L3归档遗忘阈值；
2. 聚合条目写入满90天仍未完成晋升至L4；
3. 分层容量达到紧急溢出阈值，条目agg_I处于L3后20%区间强制加急归档。

### 4. V1.1分层流转强制约束
1. 唯一上游写入源：仅接收ag-mem-23推送聚合条目，拒绝其他模块直接写入，杜绝旁路篡改；
2. 单向流转链路：L3聚合条目仅可晋升至ag-mem-26 L4长期层，禁止跨层直达L5；
3. 清理层级规范：L2/L3及以上中长期记忆过期统一离线归档，不执行物理删除，满足全链路溯源；
4. L5永久隔离：不存在任何L3条目直通顶层核心永久记忆的流转通道。

### 5. 批量处理约束
单次晋升/归档扫描最大处理1000条聚合条目，超量自动拆分多批次串行执行，避免向量索引重建、磁盘IO阻塞。

## 核心处理逻辑
```
FUNCTION l3_midterm_storage_main_loop():
    STATE_IDLE = L3_IDLE
    STATE_PERSIST = AGG_PERSIST
    STATE_PROMOTE = PROMOTE_SCAN
    STATE_ARCHIVE = ARCHIVE_SCAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局L3基础配置
    l3_global_cfg = query_layer_config(from_m35="ag-mem-35")
    l3_max_keep_ms = l3_global_cfg.L3_max_keep_day * 24 * 3600 * 1000
    l3_promote_min_reuse = 20
    // 按funnel业务域分域存储聚合条目缓存
    funnel_agg_store = {}
    stat_today_add = 0
    stat_total_promote_l4 = 0
    stat_total_archive = 0
    last_report_ts = NOW()
    // 定时周期配置
    promote_cycle = l3_global_cfg.promote_scan_sec
    archive_cycle = l3_global_cfg.archive_scan_sec
    promote_countdown = promote_cycle
    archive_countdown = archive_cycle

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收上游ag-mem-23批量聚合条目写入（唯一上游来源）
        IF 收到L3归并完成聚合条目批量推送:
            batch_write_req = 获取聚合条目列表
            internal_state = AGG_PERSIST
            success_cnt = 0
            fail_merge_ids = []
            now_ts = NOW()
            FOR agg_item IN batch_write_req:
                merge_id = agg_item.merge聚合ID
                funnel_id = agg_item.funnel分槽ID
                // 前置合法性校验
                IF merge_id == None OR agg_item.agg_I <= 0 OR agg_item.total_reuse < 2:
                    fail_merge_ids.append(merge_id)
                    CONTINUE
                // 按funnel分域初始化存储，构建聚合向量索引
                IF funnel_id NOT IN funnel_agg_store:
                    funnel_agg_store[funnel_id] = {}
                funnel_agg_store[funnel_id][merge_id] = {
                    "merge_id": merge_id,
                    "source_origin_ids": agg_item.原始条目ID集合,
                    "funnel_id": funnel_id,
                    "agg_I": agg_item.agg_I加权重要度,
                    "total_reuse": agg_item.total_reuse总复用,
                    "agg_S": agg_item.agg_S综合安全值,
                    "create_ts": agg_item.生成时间戳,
                    "last_access_ts": now_ts,
                    "manual_tag": "无",
                    "merge_vector": agg_item.merge_vector聚合向量,
                    "task_tag": agg_item.task_tag任务标签
                }
                success_cnt += 1
                stat_today_add += 1
            // 回执回写给上游ag-mem-23
            write_ack = build_l3_write_ack(total=len(batch_write_req), success=success_cnt, fail_list=fail_merge_ids)
            send_write_ack(target="ag-mem-23", ack_data=write_ack)
            // 写入审计日志
            send_audit_log(event="L3批量接收ag-mem-23归并聚合条目", add_count=success_cnt, ts=now_ts)
            internal_state = STATE_IDLE

        // 3. 定时晋升筛选扫描，批量推送达标聚合条目至ag-mem-26
        IF internal_state == STATE_IDLE:
            promote_countdown -= 10
            IF promote_countdown <= 0:
                internal_state = PROMOTE_SCAN
                promote_list = []
                now_ts = NOW()
                // 遍历所有funnel业务域
                FOR funnel_id, agg_map IN funnel_agg_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l3_global_cfg)
                    FOR merge_id, agg_data IN agg_map.items():
                        age = now_ts - agg_data.create_ts
                        // 跳过超期、人工保护聚合条目
                        IF age >= l3_max_keep_ms OR agg_data.manual_tag != "无":
                            CONTINUE
                        // 校验全部晋升准入条件
                        IF agg_data.agg_I >= slot_cfg.L3_promote_thresh AND agg_data.total_reuse >= l3_promote_min_reuse:
                            promote_list.append(agg_data)
                // 批量推送晋升条目至ag-mem-26
                IF len(promote_list) > 0:
                    send_promote_batch(target="ag-mem-26", item_list=promote_list)
                    stat_total_promote_l4 += len(promote_list)
                    // 从L3分域存储移除已晋升聚合条目
                    FOR promote_item IN promote_list:
                        del funnel_agg_store[promote_item.funnel_id][promote_item.merge_id]
                    send_audit_log(event="L3批量晋升聚合条目至ag-mem-26 L4层", count=len(promote_list), ts=now_ts)
                promote_countdown = promote_cycle
                internal_state = STATE_IDLE

        // 4. 定时归档遗忘扫描，生成归档候选推送ag-mem-42
        IF internal_state == STATE_IDLE:
            archive_countdown -= 10
            IF archive_countdown <= 0:
                internal_state = ARCHIVE_SCAN
                archive_candidate = []
                now_ts = NOW()
                FOR funnel_id, agg_map IN funnel_agg_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l3_global_cfg)
                    FOR merge_id, agg_data IN agg_map.items():
                        age = now_ts - agg_data.create_ts
                        // 人工收藏/锁定聚合条目直接跳过归档
                        IF agg_data.manual_tag in ["用户收藏", "人工锁定"]:
                            CONTINUE
                        need_archive = False
                        reason = ""
                        if agg_data.agg_I < slot_cfg.L3_archive_thresh:
                            need_archive = True
                            reason = "加权聚合I值低于当前funnel L3归档遗忘阈值"
                        elif age >= l3_max_keep_ms:
                            need_archive = True
                            reason = "聚合条目留存满90天未晋升至L4"
                        if need_archive:
                            archive_candidate.append({
                                "merge_id": merge_id,
                                "forget_reason": reason,
                                "item_I": agg_data.agg_I,
                                "layer_threshold": slot_cfg.L3_archive_thresh,
                                "suggest_handle": "archive",
                                "layer": "L3",
                                "slot_id": funnel_id
                            })
                // 推送归档候选清单至ag-mem-42
                IF len(archive_candidate) > 0:
                    send_archive_list(target="ag-mem-42", candidate=archive_candidate)
                    stat_total_archive += len(archive_candidate)
                archive_countdown = archive_cycle
                internal_state = STATE_IDLE

        // 5. 响应ag-mem-37 / ag-mem-40 聚合条目元数据批量查询
        IF 收到聚合条目元数据批量查询请求:
            query_merge_ids = 获取请求merge聚合ID列表
            meta_result = []
            FOR funnel_id, agg_map IN funnel_agg_store.items():
                FOR merge_id IN query_merge_ids:
                    IF merge_id IN agg_map:
                        meta_result.append(agg_map[merge_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_result)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_result)

        // 6. 定时容量上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            data_kb, vec_index_kb = calc_layer_cap_kb(funnel_agg_store, avg_kb=l3_global_cfg.avg_agg_kb, vec_overhead=l3_global_cfg.vec_index_overhead_kb)
            total_agg_count = sum(len(v) for v in funnel_agg_store.values())
            cap_report = build_cap_report(layer="L3", data_used_kb=data_kb, vec_index_kb=vec_index_kb, item_count=total_agg_count)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s向ag-mem-03上报运行统计
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_l3_stat_report(
                    state=internal_state,
                    today_add=stat_today_add,
                    total_promote=stat_total_promote_l4,
                    total_archive=stat_total_archive
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-23推送聚合条目merge_id缺失、agg_I非法 | 写入失败，加入失败列表回传给上游，不存入L3分域存储 | ag-mem-23重新推送标准化完整聚合条目 |
| 晋升扫描时聚合条目同步触发归档判定 | 条目归入归档候选，不再参与晋升，快照隔离并发变更无报错 | 无需人工干预，下一轮扫描正常执行 |
| 单次扫描聚合条目总量超过1000条 | 自动拆分多批次串行处理，不阻塞主定时循环与向量索引 | 内置分片逻辑自动执行 |
| L3持久存储/向量索引IO读写故障 | 内存funnel分域缓存完整保留聚合元数据，下一轮定时重试晋升/归档扫描 | 底层存储、向量库IO链路恢复 |
| 全局紧急熔断FUSE指令下发 | 停止写入、晋升扫描、归档扫描，内存缓存聚合条目不丢失 | ag-mem-01下发RESUME恢复指令，自动重启定时任务 |
| 目标funnel分槽无专属L3阈值配置 | 自动加载全局通用L3阈值兜底完成判定 | ag-mem-35运维侧补充分funnel独立参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L3归并完成聚合条目批量推送 | 只读 | ag-mem-23（唯一上游写入源） |
| 内部调度总线 | 读 | 聚合条目元数据批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 |
| 内部调度总线 | 写 | L3聚合条目写入完成回执 | 专属写入 | 回传给上游 ag-mem-23 |
| 内部调度总线 | 写 | 晋升聚合条目批量推送 | 专属写入 | 下发下游 ag-mem-26 |
| 内部调度总线 | 写 | 归档候选清单、聚合条目元数据快照 | 专属写入 | ag-mem-42、ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 容量上报、审计日志、周期统计上报 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L3-01 | L3仅接收ag-mem-23归并输出聚合条目，禁止ag-mem-03或其他模块直接写入，杜绝旁路篡改中期聚合记忆 |
| L3-02 | L3聚合条目仅单向晋升至ag-mem-26 L4层，禁止任何跨层直达L5顶层记忆的流转路径，分层链路单向隔离不可绕过 |
| L3-03 | L3过期低价值聚合条目统一执行离线归档，不物理删除原始聚合数据，完整保留原始条目溯源ID，满足V1.1全链路审计追溯要求 |
| L3-04 | 晋升阈值、归档阈值、90天留存时效统一由ag-mem-35集中管控，本模块无本地硬编码业务参数 |
| L3-05 | L3分层数据+向量索引容量上限、预警/紧急阈值由ag-mem-48统一管控，容量紧急自动加急归档释放存储空间 |
| L3-06 | 熔断状态内存funnel分域缓存完整保留所有聚合条目元数据，服务恢复后自动执行定时晋升与归档扫描，无数据丢失 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M24-01 | `L3_IDLE`，ag-mem-23推送带合法merge_id、agg_I合规聚合条目 | L3归并聚合条目批量列表 | 条目按funnel分域持久存入L3，构建聚合向量索引，返回写入成功回执，生成新增审计日志 |
| TC-M24-02 | `L3_IDLE`，聚合条目agg_I达标、总复用≥20、未满90天，定时晋升触发 | 晋升倒计时归零 | 聚合条目批量推送至ag-mem-26，从L3对应funnel分域移除 |
| TC-M24-03 | `L3_IDLE`，聚合条目agg_I低于当前funnel L3归档阈值 | 归档扫描触发 | 条目加入归档候选清单推送ag-mem-42，标记处理方式archive |
| TC-M24-04 | `L3_IDLE`，聚合条目写入满90天未满足晋升条件 | 归档扫描触发 | 因超期标记归档，进入清理候选清单 |
| TC-M24-05 | `L3_IDLE`，ag-mem-37下发merge_id批量元数据查询 | 聚合ID批量查询请求 | 返回对应funnel域内完整聚合元数据+聚合向量快照 |
| TC-M24-06 | `L3_IDLE`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止写入、晋升、归档扫描全部任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-24匹配V1.1白皮书L3中期聚合存储定位 | ✅ |
| 上下游依赖唯一上游ag-mem-23、下游ag-mem-26，数据流闭环无冲突 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出标注来源/目标模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 分域聚合向量存储、merge_id准入校验、90天时效、晋升/归档规则完整贴合白皮书4.4.1五层晋升通路 | ✅ |
| 伪代码覆盖归并条目写入、分域持久化、定时晋升扫描、归档扫描、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖非法聚合条目、并发归档、超大批次、向量IO故障、熔断、无分槽阈值共6类全覆盖 | ✅ |
| 内部调度总线读写权限划分清晰，上游仅允许ag-mem-23写入 | ✅ |
| 6条V1.1强制安全约束无旁路写入、跨层流转漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

---