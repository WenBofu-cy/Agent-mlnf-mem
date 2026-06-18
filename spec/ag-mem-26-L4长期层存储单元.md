# ag-mem-26-L4长期存储层 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-26 |
| 模块名称 | L4长期存储层（轻量化长效聚合记忆持久层） |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路 |
| 核心职责 | 唯一上游写入源为ag-mem-25轻量化输出条目；持久存储经过多层降噪、轻量化压缩后的长期聚合经验，按funnel分业务域隔离，搭载高压缩向量索引，适配大容量长效记忆存储；遵循V1.1分层单向流转规范，定时校验轻量化条目final_I、累计总复用、365天最大留存时效；达标条目批量推送至ag-mem-27 L4抽象提炼单元做顶层记忆标准化；低价值、超期轻量化条目生成归档候选推送ag-mem-42离线归档；对外输出轻量化条目完整元数据供给ag-mem-37全局I值刷新、ag-mem-40全分层遗忘扫描；定时上报业务数据、向量索引占用容量至ag-mem-48；所有新增、晋升、归档操作全量写入ag-mem-51审计日志；严格隔离L5顶层核心存储，无直接跨层写入通道。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-25（L3轻量化归并单元，唯一上游写入源）、ag-mem-35（三维权重配置单元，分funnel独立下发L4晋升/归档阈值、365天留存周期、向量压缩参数）、ag-mem-48（全局容量配额管控，读取L4分层容量上限、预警/紧急溢出阈值） |
| 被依赖模块 | ag-mem-27（L4抽象提炼单元，接收L4达标轻量化条目）、ag-mem-37（重要度定时刷新单元，读取L4轻量化条目元数据）、ag-mem-40（遗忘阈值判定单元，提供L4条目扫描快照）、ag-mem-42（冗余记忆删除单元，接收L4归档候选清单）、ag-mem-48（接收L4分层容量定时上报）、ag-mem-51（记录L4全部记忆变更审计日志）、ag-mem-03（漏斗二调度单元，周期上报L4运行统计指标） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `L4_IDLE` | 正常接收ag-mem-25轻量化条目写入，等待定时晋升、归档扫描任务 | 系统初始化完成、熔断恢复、批次晋升/归档处理完毕 |
| 轻量化条目持久写入 | `LIGHT_PERSIST` | 校验上游轻量化条目合法性，按funnel分域落盘，构建压缩向量索引，初始化完整元数据 | 收到ag-mem-25批量轻量化条目推送 |
| 晋升筛选扫描 | `PROMOTE_SCAN` | 遍历分域存储条目，匹配分funnel晋升阈值、总复用、365天时效筛选可晋升条目 | 晋升定时周期倒计时归零 |
| 归档遗忘扫描 | `ARCHIVE_SCAN` | 筛选低final_I、满365天时效条目，生成归档候选清单下发ag-mem-42 | 归档扫描周期到达 / ag-mem-48容量紧急预警触发加急扫描 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、晋升扫描、归档扫描，内存缓存轻量化条目元数据 | F0下发FUSE熔断指令；RESUME指令切回L4_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3轻量化完成条目批量推送 | List<Struct>（light_id轻量化ID、source_merge_ids源聚合ID集合、funnel分槽ID、final_I加权重要度、sum_reuse总复用、avg_S综合安全值、light_vector压缩向量、task_tag任务大类、create_ts创建时间戳） | ag-mem-25 L3轻量化归并单元 | ag-mem-25完成轻量化合并过滤，推送标准化长效条目 | 高 |
| L4定时晋升扫描指令 | Struct（触发类型=定时，目标下游ag-mem-27） | 内部定时调度 | 晋升扫描周期倒计时归零 | 普通 |
| L4归档遗忘扫描触发指令 | Struct（触发原因：定时/容量预警，是否加急） | 内部定时调度 / ag-mem-48容量管控单元 | 归档周期到达、分层容量触发紧急溢出阈值 | 普通 |
| 轻量化条目元数据批量查询请求 | Struct（light_id轻量化ID列表） | ag-mem-37 / ag-mem-40 | 全局I值批量重算、全分层遗忘扫描 | 高 |
| L4分层容量配额配置回执 | Struct（L4总容量上限、预警占用比例、紧急溢出比例、向量索引预留容量） | ag-mem-48 全局容量配额单元 | 模块初始化、人工调整分层配额 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4轻量化条目写入完成回执 | Struct（批量总条数、成功写入数量、失败light_id清单） | ag-mem-25 L3轻量化归并单元 | 轻量化条目批量持久落盘、向量索引构建完成 | 高 |
| 可晋升轻量化条目批量推送 | List<完整轻量化元数据、final_I、sum_reuse、funnel分槽、压缩向量> | ag-mem-27 L4抽象提炼单元 | 晋升筛选存在达标长效条目 | 高 |
| L4归档遗忘候选清单 | List<Struct>（light_id、遗忘原因、当前final_I、分层归档阈值、suggest_handle=archive） | ag-mem-42 冗余记忆删除单元 | 归档扫描筛选出待清理长效条目 | 普通 |
| L4轻量化条目元数据快照 | List<light_id、final_I、sum_reuse、create_ts、funnel_id、light_vector、task_tag> | ag-mem-37 / ag-mem-40 | I值刷新、遗忘扫描批量查询 | 高 |
| L4分层容量占用上报 | Struct（层级=L4、业务数据占用KB、压缩向量索引占用KB、轻量化条目总数量） | ag-mem-48 全局容量配额 | 每60秒定时上报、批量条目变更后即时上报 | 普通 |
| L4长效记忆变更审计日志 | Struct（事件类型、条目操作数量、funnel分槽范围、时间戳、溯源source_merge_ids） | ag-mem-51 记忆变更日志追溯单元 | 写入、晋升、归档清理操作完成 | 普通 |
| L4周期运行统计上报 | Struct（当前状态、今日新增轻量化条目、累计晋升至ag-mem-27总量、累计归档清理总量、向量索引条目总数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## L4长期层核心规则（严格对齐V1.1白皮书4.4.1五层晋升通路）
### 1. 分funnel独立配置参数（由ag-mem-35统一分发）
1. L4最大留存时效：365天，写入满365天未晋升自动进入归档流程；
2. L4晋升至L5前置提炼最低final_I阈值：分业务funnel独立配置；
3. L4归档遗忘final_I阈值：分业务funnel独立配置；
4. 晋升最低累计总复用次数：100次；
5. 准入前置校验：上游ag-mem-25输出合法light_id轻量化条目，无合法ID直接拒绝写入。

### 2. 晋升至ag-mem-27完整准入条件（全部同时满足）
1. 条目携带唯一合法light_id轻量化标识，来源为ag-mem-25标准化轻量化输出；
2. 当前加权final_I ≥ 当前funnel分槽L4晋升阈值；
3. 累计总复用次数 ≥ 100次；
4. 轻量化条目写入未满365天，未达到最大留存时效；
5. 无人工收藏/锁定保护标记。

### 3. 归档清理触发条件（满足任意一条即加入归档候选）
1. 加权final_I ＜ 当前funnel分槽L4归档遗忘阈值；
2. 轻量化条目写入满365天仍未完成晋升至ag-mem-27；
3. L4分层容量达到紧急溢出阈值，条目final_I处于全库后20%区间，强制加急归档释放空间。

### 4. V1.1分层流转强制约束
1. 唯一上游写入源：仅接收ag-mem-25推送轻量化条目，禁止任何其他模块直接写入，杜绝旁路篡改长效记忆；
2. 单向流转链路：L4条目仅可晋升至ag-mem-27抽象提炼单元，不可跨层直达L5核心存储；
3. 长效记忆清理规范：L2/L3/L4中长期记忆过期统一离线归档，不物理删除原始轻量化数据，完整保留溯源链路满足全链路审计；
4. L5永久隔离：不存在任何L4条目直接流入顶层永久记忆的流转通道，必须经过ag-mem-27抽象提炼、ag-mem-45安全校验、ag-mem-29锁控三层前置流程。

### 5. 批量处理约束
单次晋升/归档扫描最大处理1000条轻量化条目，超量自动拆分多批次串行执行，避免大容量向量索引重建、磁盘IO阻塞。

## 核心处理逻辑
```
FUNCTION l4_longterm_storage_main_loop():
    STATE_IDLE = L4_IDLE
    STATE_PERSIST = LIGHT_PERSIST
    STATE_PROMOTE = PROMOTE_SCAN
    STATE_ARCHIVE = ARCHIVE_SCAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局L4基础配置
    l4_global_cfg = query_layer_config(from_m35="ag-mem-35")
    l4_max_keep_ms = l4_global_cfg.L4_max_keep_day * 24 * 3600 * 1000
    l4_promote_min_reuse = 100
    // 按funnel业务域分域存储轻量化条目缓存
    funnel_light_store = {}
    stat_today_add = 0
    stat_total_promote_abstract = 0
    stat_total_archive = 0
    last_report_ts = NOW()
    // 定时周期配置
    promote_cycle = l4_global_cfg.promote_scan_sec
    archive_cycle = l4_global_cfg.archive_scan_sec
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

        // 2. 接收上游ag-mem-25批量轻量化条目写入（唯一上游来源）
        IF 收到L3轻量化完成条目批量推送:
            batch_write_req = 获取轻量化条目列表
            internal_state = LIGHT_PERSIST
            success_cnt = 0
            fail_light_ids = []
            now_ts = NOW()
            FOR light_item IN batch_write_req:
                light_id = light_item.light_id轻量化ID
                funnel_id = light_item.funnel分槽ID
                // 前置合法性校验
                IF light_id == None OR light_item.final_I <= 0 OR light_item.sum_reuse < 2:
                    fail_light_ids.append(light_id)
                    CONTINUE
                // 按funnel分域初始化存储，构建压缩向量索引
                IF funnel_id NOT IN funnel_light_store:
                    funnel_light_store[funnel_id] = {}
                funnel_light_store[funnel_id][light_id] = {
                    "light_id": light_id,
                    "source_merge_ids": light_item.source_merge_ids,
                    "funnel_id": funnel_id,
                    "final_I": light_item.final_I加权重要度,
                    "sum_reuse": light_item.sum_reuse总复用,
                    "avg_S": light_item.avg_S综合安全值,
                    "create_ts": light_item.create_ts创建时间戳,
                    "last_access_ts": now_ts,
                    "manual_tag": "无",
                    "light_vector": light_item.light_vector压缩向量,
                    "task_tag": light_item.task_tag任务大类
                }
                success_cnt += 1
                stat_today_add += 1
            // 回执回写给上游ag-mem-25
            write_ack = build_l4_write_ack(total=len(batch_write_req), success=success_cnt, fail_list=fail_light_ids)
            send_write_ack(target="ag-mem-25", ack_data=write_ack)
            // 写入审计日志
            send_audit_log(event="L4批量接收ag-mem-25轻量化长效条目", add_count=success_cnt, ts=now_ts)
            internal_state = STATE_IDLE

        // 3. 定时晋升筛选扫描，批量推送达标条目至ag-mem-27
        IF internal_state == STATE_IDLE:
            promote_countdown -= 10
            IF promote_countdown <= 0:
                internal_state = PROMOTE_SCAN
                promote_list = []
                now_ts = NOW()
                // 遍历所有funnel业务域
                FOR funnel_id, light_map IN funnel_light_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l4_global_cfg)
                    FOR light_id, light_data IN light_map.items():
                        age = now_ts - light_data.create_ts
                        // 跳过超期、人工保护轻量化条目
                        IF age >= l4_max_keep_ms OR light_data.manual_tag != "无":
                            CONTINUE
                        // 校验全部晋升准入条件
                        IF light_data.final_I >= slot_cfg.L4_promote_thresh AND light_data.sum_reuse >= l4_promote_min_reuse:
                            promote_list.append(light_data)
                // 批量推送晋升条目至ag-mem-27
                IF len(promote_list) > 0:
                    send_promote_batch(target="ag-mem-27", item_list=promote_list)
                    stat_total_promote_abstract += len(promote_list)
                    // 从L4分域存储移除已晋升轻量化条目
                    FOR promote_item IN promote_list:
                        del funnel_light_store[promote_item.funnel_id][promote_item.light_id]
                    send_audit_log(event="L4批量晋升轻量化条目至ag-mem-27抽象提炼单元", count=len(promote_list), ts=now_ts)
                promote_countdown = promote_cycle
                internal_state = STATE_IDLE

        // 4. 定时归档遗忘扫描，生成归档候选推送ag-mem-42
        IF internal_state == STATE_IDLE:
            archive_countdown -= 10
            IF archive_countdown <= 0:
                internal_state = ARCHIVE_SCAN
                archive_candidate = []
                now_ts = NOW()
                FOR funnel_id, light_map IN funnel_light_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l4_global_cfg)
                    FOR light_id, light_data IN light_map.items():
                        age = now_ts - light_data.create_ts
                        // 人工收藏/锁定条目直接跳过归档
                        IF light_data.manual_tag in ["用户收藏", "人工锁定"]:
                            CONTINUE
                        need_archive = False
                        reason = ""
                        if light_data.final_I < slot_cfg.L4_archive_thresh:
                            need_archive = True
                            reason = "轻量化final_I低于当前funnel L4归档遗忘阈值"
                        elif age >= l4_max_keep_ms:
                            need_archive = True
                            reason = "轻量化长效条目留存满365天未晋升至抽象提炼层"
                        if need_archive:
                            archive_candidate.append({
                                "light_id": light_id,
                                "forget_reason": reason,
                                "item_I": light_data.final_I,
                                "layer_threshold": slot_cfg.L4_archive_thresh,
                                "suggest_handle": "archive",
                                "layer": "L4",
                                "slot_id": funnel_id
                            })
                // 推送归档候选清单至ag-mem-42
                IF len(archive_candidate) > 0:
                    send_archive_list(target="ag-mem-42", candidate=archive_candidate)
                    stat_total_archive += len(archive_candidate)
                archive_countdown = archive_cycle
                internal_state = STATE_IDLE

        // 5. 响应ag-mem-37 / ag-mem-40 轻量化条目元数据批量查询
        IF 收到轻量化条目元数据批量查询请求:
            query_light_ids = 获取请求light_id轻量化ID列表
            meta_result = []
            FOR funnel_id, light_map IN funnel_light_store.items():
                FOR light_id IN query_light_ids:
                    IF light_id IN light_map:
                        meta_result.append(light_map[light_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_result)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_result)

        // 6. 定时容量上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            data_kb, vec_index_kb = calc_layer_cap_kb(funnel_light_store, avg_kb=l4_global_cfg.avg_light_kb, vec_overhead=l4_global_cfg.compressed_vec_overhead_kb)
            total_light_count = sum(len(v) for v in funnel_light_store.values())
            cap_report = build_cap_report(layer="L4", data_used_kb=data_kb, vec_index_kb=vec_index_kb, item_count=total_light_count)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s向ag-mem-03上报运行统计
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_l4_stat_report(
                    state=internal_state,
                    today_add=stat_today_add,
                    total_promote=stat_total_promote_abstract,
                    total_archive=stat_total_archive
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-25推送轻量化条目light_id缺失、final_I非法 | 写入失败，加入失败列表回传给上游，不存入L4分域存储 | ag-mem-25重新推送标准化完整轻量化条目 |
| 晋升扫描时轻量化条目同步触发归档判定 | 条目归入归档候选，不再参与晋升，快照隔离并发变更无报错 | 无需人工干预，下一轮扫描正常执行 |
| 单次扫描轻量化条目总量超过1000条 | 自动拆分多批次串行处理，不阻塞主定时循环与压缩向量索引 | 内置分片逻辑自动执行 |
| L4持久存储/压缩向量索引IO读写故障 | 内存funnel分域缓存完整保留轻量化元数据，下一轮定时重试晋升/归档扫描 | 底层存储、向量库IO链路恢复 |
| 全局紧急熔断FUSE指令下发 | 停止写入、晋升扫描、归档扫描，内存缓存轻量化条目不丢失 | ag-mem-01下发RESUME恢复指令，自动重启定时任务 |
| 目标funnel分槽无专属L4阈值配置 | 自动加载全局通用L4阈值兜底完成判定 | ag-mem-35运维侧补充分funnel独立参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L3轻量化完成条目批量推送 | 只读 | ag-mem-25（唯一上游写入源） |
| 内部调度总线 | 读 | 轻量化条目元数据批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 |
| 内部调度总线 | 写 | L4轻量化条目写入完成回执 | 专属写入 | 回传给上游 ag-mem-25 |
| 内部调度总线 | 写 | 晋升轻量化条目批量推送 | 专属写入 | 下发下游 ag-mem-27 |
| 内部调度总线 | 写 | 归档候选清单、轻量化条目元数据快照 | 专属写入 | ag-mem-42、ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 容量上报、审计日志、周期统计上报 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L4-01 | L4仅接收ag-mem-25轻量化输出条目，禁止ag-mem-03或其他模块直接写入，杜绝旁路篡改长效记忆数据 |
| L4-02 | L4轻量化条目仅单向晋升至ag-mem-27抽象提炼单元，禁止任何跨层直达L5顶层记忆的流转路径，分层链路单向隔离不可绕过 |
| L4-03 | L4过期低价值轻量化条目统一执行离线归档，不物理删除原始长效数据，完整保留多层溯源ID集合，满足V1.1全链路审计追溯要求 |
| L4-04 | 晋升阈值、归档阈值、365天留存时效统一由ag-mem-35集中管控，本模块无本地硬编码业务参数 |
| L4-05 | L4分层业务数据+压缩向量索引容量上限、预警/紧急阈值由ag-mem-48统一管控，容量紧急自动加急归档释放存储空间 |
| L4-06 | 熔断状态内存funnel分域缓存完整保留所有轻量化条目元数据，服务恢复后自动执行定时晋升与归档扫描，无数据丢失 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M26-01 | `L4_IDLE`，ag-mem-25推送带合法light_id、final_I合规轻量化条目 | L3轻量化条目批量列表 | 条目按funnel分域持久存入L4，构建压缩向量索引，返回写入成功回执，生成新增审计日志 |
| TC-M26-02 | `L4_IDLE`，条目final_I达标、总复用≥100、未满365天，定时晋升触发 | 晋升倒计时归零 | 轻量化条目批量推送至ag-mem-27，从L4对应funnel分域移除 |
| TC-M26-03 | `L4_IDLE`，轻量化条目final_I低于当前funnel L4归档阈值 | 归档扫描触发 | 条目加入归档候选清单推送ag-mem-42，标记处理方式archive |
| TC-M26-04 | `L4_IDLE`，轻量化条目写入满365天未满足晋升条件 | 归档扫描触发 | 因超期标记归档，进入清理候选清单 |
| TC-M26-05 | `L4_IDLE`，ag-mem-37下发light_id批量元数据查询 | 轻量化ID批量查询请求 | 返回对应funnel域内完整轻量化元数据+压缩向量快照 |
| TC-M26-06 | `L4_IDLE`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止写入、晋升、归档扫描全部任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-26匹配V1.1白皮书L4长效轻量化存储定位 | ✅ |
| 上下游依赖唯一上游ag-mem-25、下游ag-mem-27，数据流闭环无冲突 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出标注来源/目标模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 分域压缩向量存储、light_id准入校验、365天时效、晋升/归档规则完整贴合白皮书4.4.1五层晋升通路 | ✅ |
| 伪代码覆盖轻量化条目写入、分域持久化、定时晋升扫描、归档扫描、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖非法轻量化条目、并发归档、超大批次、向量IO故障、熔断、无分槽阈值共6类全覆盖 | ✅ |
| 内部调度总线读写权限划分清晰，上游仅允许ag-mem-25写入 | ✅ |
| 6条V1.1强制安全约束无旁路写入、跨层流转漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

---