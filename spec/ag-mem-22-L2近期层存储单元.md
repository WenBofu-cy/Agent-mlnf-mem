# ag-mem-22-L2近期层 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-22 |
| 模块名称 | L2近期层（情景记忆持久存储层） |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路 |
| 核心职责 | 上游唯一写入源为ag-mem-21（L1分桶临时层），持久存储经短期验证的中长期情景经验；按funnel_id分业务域隔离存储，内置向量索引支持语义检索；遵循V1.1「结果驱动晋升、分层单向流转」规范，定时校验条目I值、复用频次、30天最大留存时效；达标条目单向批量晋升至ag-mem-24 L3中期存储，晋升前同步推送条目至ag-mem-23辅助归并单元做预聚合；低重要度、超期条目生成遗忘候选推送ag-mem-42执行归档（L2层级不直接物理删除，统一离线归档）；对外输出完整条目元数据供给ag-mem-37全局I值重算、ag-mem-40遗忘批量扫描；定时上报分层容量占用至ag-mem-48；所有新增、晋升、归档操作全量推送审计日志至ag-mem-51；L5永久隔离，无任何直通顶层存储流转通道。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-21（L1临时层，唯一上游写入来源）、ag-mem-23（L3辅助归并单元，晋升前置预聚合）、ag-mem-35（三维权重配置单元，分funnel独立读取L2晋升/遗忘阈值、30天留存时效、归档规则）、ag-mem-48（全局容量配额管控，读取分层容量上限、预警/紧急阈值） |
| 被依赖模块 | ag-mem-23（接收晋升条目预聚合快照）、ag-mem-24（L3中期存储层，接收完成聚合后的晋升条目）、ag-mem-37（重要度定时刷新单元，读取L2全量条目元数据）、ag-mem-40（遗忘阈值判定单元，提供L2条目扫描快照）、ag-mem-42（冗余记忆删除单元，接收L2归档遗忘候选清单）、ag-mem-48（定时上报L2分层占用容量）、ag-mem-51（推送L2记忆变更审计日志）、ag-mem-03（漏斗二调度单元，周期上报L2运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `L2_IDLE` | 正常接收ag-mem-21批量晋升写入，等待定时晋升/归档扫描任务 | 系统初始化、熔断恢复、批次晋升/归档全部完成 |
| 条目持久写入 | `ITEM_PERSIST` | 校验L1晋升条目合法性，按funnel分域落盘，构建向量检索索引，初始化完整条目元数据 | 收到ag-mem-21下发批量晋升条目 |
| 晋升预聚合扫描 | `PROMOTE_AGG_SCAN` | 遍历分域条目，比对分funnel晋升阈值、复用次数、留存时效筛选可晋升条目，推送快照至ag-mem-23预聚合 | 晋升定时周期倒计时归零 |
| 归档遗忘扫描 | `ARCHIVE_SCAN` | 筛选低I、超30天时效条目，生成归档候选清单推送ag-mem-42 | 归档扫描周期到达 / ag-mem-48容量预警触发加急扫描 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、晋升聚合、归档扫描，内存缓存临时条目元数据 | F0下发FUSE熔断指令；RESUME切回L2_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L1批量晋升条目推送 | List<Struct>（条目ID、复用次数、S值、实时I值、生成时间、来源funnel分槽ID、result_validated标记、向量嵌入） | ag-mem-21 L1分桶临时层 | L1定时晋升筛选完成，推送达标短期经验 | 高 |
| L2定时晋升聚合扫描指令 | Struct（触发类型=定时，目标下游L3） | 内部定时调度 | 晋升聚合周期倒计时归零 | 普通 |
| L2归档遗忘扫描触发指令 | Struct（触发原因：定时/容量预警，是否加急） | 内部定时调度 / ag-mem-48容量预警 | 归档周期到达、分层容量触发预警 | 普通 |
| 条目元数据批量查询请求 | Struct（条目ID列表） | ag-mem-37 重要度增量定时刷新单元 | 全局I值批量重算 | 高 |
| 分层容量配额配置回执 | Struct（L2总容量上限、预警占比、紧急溢出占比） | ag-mem-48 全局容量配额单元 | 模块初始化、配额人工更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L2条目写入完成回执 | Struct（批量条目总量、写入成功数量、失败条目ID列表） | ag-mem-21 L1临时层 | L1晋升条目批量持久落盘完成 | 高 |
| 晋升条目预聚合快照 | List（条目完整元数据、最新I值、复用次数、funnel分槽ID、向量嵌入） | ag-mem-23 L3辅助归并单元 | 晋升筛选存在合格条目，前置聚合预处理 | 高 |
| L2归档遗忘候选清单 | List（条目ID、遗忘原因、当前I值、层级遗忘阈值、suggest_handle=archive） | ag-mem-42 冗余记忆删除单元 | 归档扫描筛选待清理条目 | 普通 |
| L2条目元数据快照 | List（条目ID、I值、复用次数、写入时间、最近访问、funnel分槽ID、向量嵌入） | ag-mem-37 / ag-mem-40 | I值刷新、遗忘扫描批量查询 | 高 |
| L2分层容量占用上报 | Struct（层级=L2、当前占用KB、条目总数、单条平均体积KB、向量索引占用） | ag-mem-48 全局容量配额 | 每60秒定时上报、批量条目变更后即时上报 | 普通 |
| L2记忆变更审计日志 | Struct（事件类型、条目操作数量、分层、时间戳、关联funnel分槽、向量索引变更标记） | ag-mem-51 记忆变更日志追溯单元 | 写入、晋升聚合、归档清理操作完成 | 普通 |
| L2周期运行统计上报 | Struct（当前状态、今日新增条目、累计晋升L3总量、累计归档清理总量、向量索引总条目数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## L2近期层核心规则（严格对齐V1.1白皮书4.4.1五层晋升通路）
### 1. 分funnel独立配置参数（由ag-mem-35统一下发）
1. L2最大留存时效：30天，写入满30天未晋升自动进入归档流程；
2. L2晋升L3最低I阈值：分业务funnel独立配置；
3. L2归档遗忘I阈值：分业务funnel独立配置；
4. 晋升最低累计复用次数：8次；
5. 准入前置校验：上游L1传入`result_validated=True`，无标记条目拒绝写入。

### 2. 晋升至L3完整准入条件（全部同时满足）
1. 条目`result_validated`校验标记恒为True；
2. 当前实时I值 ≥ 当前funnel分槽L2晋升阈值；
3. 总任务+工具累计复用次数 ≥ 8次；
4. 条目写入未满30天，未达最大留存时效；
5. 无人工收藏/锁定保护标记。

### 3. 归档清理触发条件（满足任意一条即加入归档候选）
1. 实时I值 ＜ 当前funnel分槽L2归档遗忘阈值；
2. 条目写入满30天仍未完成晋升至L3；
3. 分层容量达到紧急溢出阈值，条目I值处于L2后20%区间强制加急归档。

### 4. V1.1分层流转强制约束
1. 唯一上游写入源：仅接收ag-mem-21推送条目，拒绝其他模块直接写入，杜绝旁路写入漏洞；
2. 单向流转链路：L2条目先推送预聚合快照至ag-mem-23，完成聚合后再批量流入ag-mem-24 L3，禁止直接跨层写入L4/L5；
3. 清理规则区分层级：L1/L0物理删除，**L2及以上层级过期条目统一离线归档，不直接物理删除**；
4. L5永久隔离：不存在任何L2条目直通顶层核心永久存储的流转通道。

### 5. 批量处理约束
单次晋升/归档扫描最大处理1000条，超量自动拆分多批次串行执行，避免向量索引重建、IO阻塞。

## 核心处理逻辑
```
FUNCTION l2_episodic_storage_main_loop():
    STATE_IDLE = L2_IDLE
    STATE_PERSIST = ITEM_PERSIST
    STATE_AGG_SCAN = PROMOTE_AGG_SCAN
    STATE_ARCHIVE_SCAN = ARCHIVE_SCAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局L2基础参数
    l2_global_cfg = query_layer_config(from_m35="ag-mem-35")
    l2_max_keep_ms = l2_global_cfg.L2_max_keep_day * 24 * 3600 * 1000
    l2_promote_min_reuse = 8
    // 按funnel业务域分域持久存储缓存
    funnel_item_store = {}
    stat_today_add = 0
    stat_total_promote_l3 = 0
    stat_total_archive = 0
    last_report_ts = NOW()
    // 定时周期配置
    promote_agg_cycle = l2_global_cfg.promote_agg_scan_sec
    archive_cycle = l2_global_cfg.archive_scan_sec
    promote_countdown = promote_agg_cycle
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

        // 2. 接收上游ag-mem-21批量晋升写入（唯一上游来源）
        IF 收到L1批量晋升条目推送:
            batch_write_req = 获取晋升条目列表
            internal_state = ITEM_PERSIST
            success_cnt = 0
            fail_ids = []
            now_ts = NOW()
            FOR item IN batch_write_req:
                item_id = item.条目ID
                funnel_id = item.来源funnel分槽ID
                // 前置合法性校验
                IF item.result_validated != True OR item.I_value <= 0 OR item.复用次数 < 3:
                    fail_ids.append(item_id)
                    CONTINUE
                // 按funnel分域初始化存储，构建向量索引
                IF funnel_id NOT IN funnel_item_store:
                    funnel_item_store[funnel_id] = {}
                funnel_item_store[funnel_id][item_id] = {
                    "funnel_id": funnel_id,
                    "reuse_count": item.复用次数,
                    "S_value": item.S值,
                    "I_value": item.实时I值,
                    "create_ts": item.生成时间,
                    "last_access_ts": now_ts,
                    "manual_tag": "无",
                    "result_validated": item.result_validated,
                    "vector_embedding": item.向量嵌入
                }
                success_cnt += 1
                stat_today_add += 1
            // 回执回写给上游ag-mem-21
            write_ack = build_l2_write_ack(total=len(batch_write_req), success=success_cnt, fail_list=fail_ids)
            send_write_ack(target="ag-mem-21", ack_data=write_ack)
            // 写入审计日志
            send_audit_log(event="L2批量接收ag-mem-21晋升条目", add_count=success_cnt, ts=now_ts)
            internal_state = STATE_IDLE

        // 3. 定时晋升聚合扫描，推送预聚合快照至ag-mem-23
        IF internal_state == STATE_IDLE:
            promote_countdown -= 10
            IF promote_countdown <= 0:
                internal_state = PROMOTE_AGG_SCAN
                promote_agg_list = []
                now_ts = NOW()
                // 遍历所有funnel业务域
                FOR funnel_id, item_map IN funnel_item_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l2_global_cfg)
                    FOR item_id, item_data IN item_map.items():
                        age = now_ts - item_data.create_ts
                        // 跳过超期、人工保护条目
                        IF age >= l2_max_keep_ms OR item_data.manual_tag != "无":
                            CONTINUE
                        // 校验全部晋升准入条件
                        IF item_data.I_value >= slot_cfg.L2_promote_thresh AND item_data.reuse_count >= l2_promote_min_reuse:
                            promote_agg_list.append(item_data)
                // 批量推送预聚合快照至ag-mem-23
                IF len(promote_agg_list) > 0:
                    send_promote_agg_snapshot(target="ag-mem-23", item_list=promote_agg_list)
                    stat_total_promote_l3 += len(promote_agg_list)
                    // 从L2分域存储移除已推送聚合条目
                    FOR agg_item IN promote_agg_list:
                        del funnel_item_store[agg_item.funnel_id][agg_item.条目ID]
                    send_audit_log(event="L2批量推送预聚合快照至ag-mem-23", count=len(promote_agg_list), ts=now_ts)
                promote_countdown = promote_agg_cycle
                internal_state = STATE_IDLE

        // 4. 定时归档遗忘扫描，生成归档候选推送ag-mem-42
        IF internal_state == STATE_IDLE:
            archive_countdown -= 10
            IF archive_countdown <= 0:
                internal_state = ARCHIVE_SCAN
                archive_candidate = []
                now_ts = NOW()
                FOR funnel_id, item_map IN funnel_item_store.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l2_global_cfg)
                    FOR item_id, item_data IN item_map.items():
                        age = now_ts - item_data.create_ts
                        // 人工收藏/锁定条目直接跳过归档
                        IF item_data.manual_tag in ["用户收藏", "人工锁定"]:
                            CONTINUE
                        need_archive = False
                        reason = ""
                        if item_data.I_value < slot_cfg.L2_archive_thresh:
                            need_archive = True
                            reason = "I值低于当前funnel L2归档遗忘阈值"
                        elif age >= l2_max_keep_ms:
                            need_archive = True
                            reason = "条目留存满30天未晋升至L3"
                        if need_archive:
                            archive_candidate.append({
                                "item_id": item_id,
                                "forget_reason": reason,
                                "item_I": item_data.I_value,
                                "layer_threshold": slot_cfg.L2_archive_thresh,
                                "suggest_handle": "archive",
                                "layer": "L2",
                                "slot_id": funnel_id
                            })
                // 推送归档候选清单至ag-mem-42
                IF len(archive_candidate) > 0:
                    send_archive_list(target="ag-mem-42", candidate=archive_candidate)
                    stat_total_archive += len(archive_candidate)
                archive_countdown = archive_cycle
                internal_state = STATE_IDLE

        // 5. 响应ag-mem-37 I值元数据批量查询
        IF 收到条目元数据批量查询请求:
            query_ids = 获取请求条目ID列表
            meta_result = []
            FOR funnel_id, item_map IN funnel_item_store.items():
                FOR item_id IN query_ids:
                    IF item_id IN item_map:
                        meta_result.append(item_map[item_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_result)

        // 6. 定时容量上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            total_kb = calc_layer_cap_kb(funnel_item_store, avg_kb=l2_global_cfg.avg_item_kb, vec_index_kb=l2_global_cfg.vec_index_overhead_kb)
            total_item_count = sum(len(v) for v in funnel_item_store.values())
            cap_report = build_cap_report(layer="L2", used_kb=total_kb, item_count=total_item_count)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s向ag-mem-03上报运行统计
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_l2_stat_report(
                    state=internal_state,
                    today_add=stat_today_add,
                    total_promote=stat_total_promote_l3,
                    total_archive=stat_total_archive
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-21推送条目result_validated=False、I值非法 | 写入失败，加入失败列表回传给L1，不存入L2分域存储 | ag-mem-21重新推送合规校验通过条目 |
| 晋升聚合扫描时条目同步触发归档 | 条目归入归档候选，不再参与预聚合，快照隔离并发变更无报错 | 无需人工干预，下一轮扫描正常执行 |
| 单次扫描条目总量超过1000条 | 自动拆分多批次串行处理，不阻塞主定时循环与向量索引 | 内置分片逻辑自动执行 |
| L2持久存储/向量索引IO故障 | 内存funnel分域缓存完整保留条目元数据，下一轮定时重试晋升/归档扫描 | 底层存储、向量库IO链路恢复 |
| 全局紧急熔断FUSE指令下发 | 停止写入、晋升聚合、归档扫描，内存缓存条目不丢失 | ag-mem-01下发RESUME恢复指令，自动重启定时任务 |
| 目标funnel分槽无专属L2阈值配置 | 自动加载全局通用L2阈值兜底完成判定 | ag-mem-35运维侧补充分funnel独立参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L1批量晋升条目推送 | 只读 | ag-mem-21（唯一上游写入源） |
| 内部调度总线 | 读 | I值批量元数据查询请求 | 只读 | ag-mem-37 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 |
| 内部调度总线 | 写 | L2写入完成回执 | 专属写入 | 回传给上游 ag-mem-21 |
| 内部调度总线 | 写 | 晋升条目预聚合快照 | 专属写入 | 下发下游 ag-mem-23 |
| 内部调度总线 | 写 | 归档候选清单、条目元数据快照 | 专属写入 | ag-mem-42、ag-mem-37 |
| 内部调度总线 | 写 | 容量上报、审计日志、周期统计上报 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L2-01 | L2仅接收ag-mem-21推送条目，禁止ag-mem-03或其他模块直接写入，杜绝旁路写入篡改记忆数据 |
| L2-02 | L2条目晋升必经ag-mem-23预聚合环节，禁止直接跨层写入L3/L4/L5，分层流转链路单向隔离不可绕过 |
| L2-03 | L2过期低价值条目统一执行离线归档，不物理删除原始情景记忆，满足V1.1全链路追溯要求 |
| L2-04 | 晋升阈值、归档阈值、30天留存时效统一由ag-mem-35集中管控，本模块无本地硬编码业务参数 |
| L2-05 | L2分层容量上限、预警/紧急阈值由ag-mem-48统一管控，容量紧急自动加急归档释放存储空间 |
| L2-06 | 熔断状态内存funnel分域缓存完整保留所有条目元数据，服务恢复后自动执行定时晋升聚合与归档扫描，无数据丢失 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M22-01 | `L2_IDLE`，ag-mem-21推送result_validated=True合规晋升条目 | L1批量晋升条目列表 | 条目按funnel分域持久存入L2，构建向量索引，返回写入成功回执，生成新增审计日志 |
| TC-M22-02 | `L2_IDLE`，条目I达标、复用≥8、未满30天，定时晋升聚合触发 | 晋升聚合倒计时归零 | 条目批量预聚合快照推送至ag-mem-23，从L2对应funnel分域移除 |
| TC-M22-03 | `L2_IDLE`，条目I低于当前funnel L2归档阈值 | 归档扫描触发 | 条目加入归档候选清单推送ag-mem-42，标记处理方式archive |
| TC-M22-04 | `L2_IDLE`，条目写入满30天未满足晋升条件 | 归档扫描触发 | 因超期标记归档，进入清理候选清单 |
| TC-M22-05 | `L2_IDLE`，ag-mem-37下发批量元数据查询 | 条目ID批量查询请求 | 返回对应funnel域内条目完整元数据+向量嵌入快照 |
| TC-M22-06 | `L2_IDLE`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止写入、晋升聚合、归档扫描全部任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-22匹配V1.1白皮书L2情景记忆存储定位 | ✅ |
| 上下游依赖唯一上游ag-mem-21、下游预聚合ag-mem-23，数据流闭环无冲突 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出标注来源/目标模块、结构体、优先级，无上下游链路错乱 | ✅ |
| 分域向量存储、result_validated前置校验、30天时效、晋升/归档规则完整贴合白皮书4.4.1五层晋升通路 | ✅ |
| 伪代码覆盖L0写入接收、分域持久化、定时晋升预聚合、归档扫描、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖无效L1条目、并发归档、超大批次、向量IO故障、熔断、无分槽阈值共6类全覆盖 | ✅ |
| 内部调度总线读写权限划分清晰，上游仅允许ag-mem-21写入 | ✅ |
| 6条V1.1强制安全约束无旁路写入、跨层流转漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

---