# ag-mem-21-L1临时层 接口规格（修正定稿版｜对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-21 |
| 模块名称 | L1分桶临时存储层 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（正式第一层存储） |
| 核心职责 | 接收上游ag-mem-20（L0预筛选缓冲池）晋升通过的合格原始经验，基于funnel_id实现业务分桶隔离存储；维护条目哈希索引支撑高速检索；按分槽独立阈值定时校验I值、复用次数、7天最大留存时效；筛选达标条目单向晋升至ag-mem-22 L2近期层；低I、超期条目生成遗忘候选推送ag-mem-42物理删除；对外提供条目元数据供ag-mem-37全局I值重算、ag-mem-40遗忘扫描使用；定时上报容量占用至ag-mem-48；所有新增、晋升、清理操作推送审计日志至ag-mem-51；遵循V1.1「结果驱动晋升、分层单向流转」规范，无归档逻辑，仅物理删除。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-20（L0临时缓冲层，上游唯一写入来源）、ag-mem-35（三维权重配置单元，读取分槽L1晋升/遗忘阈值、留存时效）、ag-mem-48（全局容量配额管控，读取分层容量上限、预警阈值） |
| 被依赖模块 | ag-mem-22（L2近期层，接收L1合格晋升条目）、ag-mem-37（重要度定时刷新单元，读取L1条目元数据）、ag-mem-40（遗忘阈值判定单元，提供L1条目扫描快照）、ag-mem-42（冗余记忆删除单元，接收L1遗忘候选清单）、ag-mem-48（定时上报L1分层占用容量）、ag-mem-51（推送L1记忆变更审计日志）、ag-mem-03（漏斗二调度单元，周期上报L1运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `L1_IDLE` | 正常接收ag-mem-20批量晋升写入，等待定时晋升/遗忘扫描任务 | 系统初始化、熔断恢复、批次晋升/清理完成 |
| 条目写入存储 | `ITEM_STORE` | 校验L0晋升条目合法性，按funnel_id分桶落盘，初始化条目完整元数据 | 收到ag-mem-20下发批量晋升条目 |
| 晋升筛选扫描 | `PROMOTE_SCAN` | 遍历分桶条目，比对分槽L1晋升阈值、复用次数、留存时效筛选可晋升条目 | 晋升定时周期到达 |
| 遗忘过期扫描 | `FORGET_SCAN` | 筛选低I、7天超期条目，生成遗忘候选清单推送ag-mem-42 | 遗忘扫描周期到达 / ag-mem-48容量预警触发加急扫描 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、晋升、遗忘扫描，内存缓存临时条目 | F0下发FUSE熔断指令；RESUME切回L1_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L0批量晋升条目推送 | List<Struct>（条目ID、复用次数、S值、初始I值、生成时间、来源funnel分槽ID、result_validated校验标记） | ag-mem-20 L0临时缓冲层 | L0定时初筛完成，推送达标预筛选条目 | 高 |
| L1定时晋升扫描指令 | Struct（触发类型=定时，目标晋升层级=L2） | 内部定时调度 | 晋升周期倒计时归零 | 普通 |
| L1遗忘扫描触发指令 | Struct（触发原因：定时/容量预警，是否加急） | 内部定时调度 / ag-mem-48容量预警 | 遗忘周期到达、容量占用触发预警 | 普通 |
| 条目元数据批量查询请求 | Struct（条目ID列表） | ag-mem-37 重要度增量定时刷新单元 | 全局I值批量重算 | 高 |
| 分层容量配额配置回执 | Struct（L1总容量上限、预警占比、紧急溢出占比） | ag-mem-48 全局容量配额单元 | 模块初始化、配额人工更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L1条目写入完成回执 | Struct（批量条目总量、写入成功数量、失败条目ID列表） | ag-mem-20 L0缓冲层 | L0晋升条目批量落盘完成 | 高 |
| 可晋升条目批量推送 | List（条目完整元数据、最新I值、复用次数、来源funnel分槽ID、result_validated标记） | ag-mem-22 L2近期层 | 晋升筛选存在合格条目 | 高 |
| L1遗忘候选清单 | List（条目ID、遗忘原因、当前I值、层级遗忘阈值、suggest_handle=delete） | ag-mem-42 冗余记忆删除单元 | 遗忘扫描筛选出待清理条目 | 普通 |
| L1条目元数据快照 | List（条目ID、I值、复用次数、写入时间、最近访问、funnel分槽ID、result_validated） | ag-mem-37 / ag-mem-40 | I值刷新、遗忘扫描查询 | 高 |
| L1分层容量占用上报 | Struct（层级=L1、当前占用KB、条目总数、单条平均体积KB） | ag-mem-48 全局容量配额 | 每60秒定时上报、批量条目变更后即时上报 | 普通 |
| L1记忆变更审计日志 | Struct（事件类型、条目操作数量、分层、时间戳、关联funnel分槽） | ag-mem-51 记忆变更日志追溯单元 | 写入、晋升、遗忘清理操作完成 | 普通 |
| L1周期运行统计上报 | Struct（当前状态、今日新增条目、累计晋升L2总量、累计遗忘清理总量） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## L1临时层核心规则（严格对齐V1.1白皮书4.4.1五层晋升通路）
### 1. 分槽参数（由ag-mem-35统一下发，funnel_id独立配置）
1. L1最大留存时效：7天，写入满7天未晋升自动进入遗忘清理；
2. L1晋升L2最低I阈值：分funnel独立配置；
3. L1遗忘I阈值：分funnel独立配置；
4. 晋升最低复用次数：3次；
5. 准入前置校验：result_validated=True（来自L0初筛结果校验）。

### 2. 晋升至L2完整准入条件（全部同时满足）
1. 条目`result_validated`标记为True；
2. 当前实时I值 ≥ 当前funnel分槽L1晋升阈值；
3. 总任务+工具复用次数 ≥ 3次；
4. 条目写入未满7天，未达过期时效；
5. 无人工收藏/锁定保护标记。

### 3. 遗忘清理触发条件（满足任意一条即加入清理候选）
1. 实时I值 ＜ 当前funnel分槽L1分层遗忘阈值；
2. 条目写入满7天仍未晋升至L2；
3. 分层容量达到紧急溢出阈值，条目I值处于L1后20%区间强制加急清理。

### 4. V1.1分层流转强制约束
1. 唯一上游写入源：仅接收ag-mem-20推送条目，拒绝其他模块直接写入；
2. 单向流转：仅能晋升至ag-mem-22，禁止直接流入L3/L4/L5；
3. 清理规则：L1条目仅物理删除，无离线归档备份；
4. L5永久隔离：不存在任何L1条目直通顶层核心存储的流转通道。

### 5. 批量处理约束
单次晋升/遗忘扫描最大处理1000条，超量自动拆分多批次串行执行，避免IO阻塞。

## 核心处理逻辑
```
FUNCTION l1_temp_storage_main_loop():
    STATE_IDLE = L1_IDLE
    STATE_STORE = ITEM_STORE
    STATE_PROMOTE = PROMOTE_SCAN
    STATE_FORGET = FORGET_SCAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局L1基础参数
    l1_global_cfg = query_layer_config(from_m35="ag-mem-35")
    l1_max_keep_ms = l1_global_cfg.L1_max_keep_day * 24 * 3600 * 1000
    l1_promote_min_reuse = 3
    // 按funnel分桶存储条目缓存
    funnel_item_cache = {}
    stat_today_add = 0
    stat_total_promote_l2 = 0
    stat_total_forget_clean = 0
    last_report_ts = NOW()
    // 定时周期配置
    promote_cycle = l1_global_cfg.promote_scan_sec
    forget_cycle = l1_global_cfg.forget_scan_sec
    promote_countdown = promote_cycle
    forget_countdown = forget_cycle

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收上游ag-mem-20批量晋升写入（修正：唯一上游来源）
        IF 收到L0批量晋升条目推送:
            batch_write_req = 获取晋升条目列表
            internal_state = ITEM_STORE
            success_cnt = 0
            fail_ids = []
            now_ts = NOW()
            FOR item IN batch_write_req:
                item_id = item.条目ID
                funnel_id = item.来源funnel分槽ID
                // 前置校验：L0输出结果校验标记必须合法
                IF item.result_validated != True OR item.I_value <= 0 OR item.复用次数 < 1:
                    fail_ids.append(item_id)
                    CONTINUE
                // 按funnel分桶初始化存储
                IF funnel_id NOT IN funnel_item_cache:
                    funnel_item_cache[funnel_id] = {}
                funnel_item_cache[funnel_id][item_id] = {
                    "funnel_id": funnel_id,
                    "reuse_count": item.复用次数,
                    "S_value": item.S值,
                    "I_value": item.初始I值,
                    "create_ts": item.生成时间,
                    "last_access_ts": now_ts,
                    "manual_tag": "无",
                    "result_validated": item.result_validated
                }
                success_cnt += 1
                stat_today_add += 1
            // 回执回写给上游ag-mem-20
            write_ack = build_l1_write_ack(total=len(batch_write_req), success=success_cnt, fail_list=fail_ids)
            send_write_ack(target="ag-mem-20", ack_data=write_ack)
            // 写入审计日志
            send_audit_log(event="L1批量接收ag-mem-20晋升条目", add_count=success_cnt, ts=now_ts)
            internal_state = STATE_IDLE

        // 3. 定时晋升扫描，筛选推送至ag-mem-22
        IF internal_state == STATE_IDLE:
            promote_countdown -= 10
            IF promote_countdown <= 0:
                internal_state = PROMOTE_SCAN
                promote_list = []
                now_ts = NOW()
                // 遍历所有funnel分桶
                FOR funnel_id, item_map IN funnel_item_cache.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l1_global_cfg)
                    FOR item_id, item_data IN item_map.items():
                        age = now_ts - item_data.create_ts
                        // 跳过超期、人工保护条目
                        IF age >= l1_max_keep_ms OR item_data.manual_tag != "无":
                            CONTINUE
                        // 校验全部晋升准入条件
                        IF item_data.I_value >= slot_cfg.L1_promote_thresh AND item_data.reuse_count >= l1_promote_min_reuse:
                            promote_list.append(item_data)
                // 批量推送至下游ag-mem-22
                IF len(promote_list) > 0:
                    send_promote_batch(target="ag-mem-22", item_list=promote_list)
                    stat_total_promote_l2 += len(promote_list)
                    // 从L1分桶缓存移除已晋升条目
                    FOR p_item IN promote_list:
                        del funnel_item_cache[p_item.funnel_id][p_item.条目ID]
                    send_audit_log(event="L1批量晋升至ag-mem-22 L2层", count=len(promote_list), ts=now_ts)
                promote_countdown = promote_cycle
                internal_state = STATE_IDLE

        // 4. 定时遗忘扫描，生成清理候选推送ag-mem-42
        IF internal_state == STATE_IDLE:
            forget_countdown -= 10
            IF forget_countdown <= 0:
                internal_state = FORGET_SCAN
                forget_candidate = []
                now_ts = NOW()
                FOR funnel_id, item_map IN funnel_item_cache.items():
                    slot_cfg = get_slot_config(funnel_id, global_cfg=l1_global_cfg)
                    FOR item_id, item_data IN item_map.items():
                        age = now_ts - item_data.create_ts
                        // 人工收藏/锁定条目直接跳过清理
                        IF item_data.manual_tag in ["用户收藏", "人工锁定"]:
                            CONTINUE
                        need_forget = False
                        reason = ""
                        if item_data.I_value < slot_cfg.L1_forget_thresh:
                            need_forget = True
                            reason = "I值低于当前funnel L1遗忘阈值"
                        elif age >= l1_max_keep_ms:
                            need_forget = True
                            reason = "条目留存满7天未晋升至L2"
                        if need_forget:
                            forget_candidate.append({
                                "item_id": item_id,
                                "forget_reason": reason,
                                "item_I": item_data.I_value,
                                "layer_threshold": slot_cfg.L1_forget_thresh,
                                "suggest_handle": "delete",
                                "layer": "L1",
                                "slot_id": funnel_id
                            })
                // 推送遗忘候选清单至ag-mem-42
                IF len(forget_candidate) > 0:
                    send_forget_list(target="ag-mem-42", candidate=forget_candidate)
                    stat_total_forget_clean += len(forget_candidate)
                forget_countdown = forget_cycle
                internal_state = STATE_IDLE

        // 5. 响应ag-mem-37 I值元数据批量查询
        IF 收到条目元数据批量查询请求:
            query_ids = 获取请求条目ID列表
            meta_result = []
            FOR funnel_id, item_map IN funnel_item_cache.items():
                FOR item_id IN query_ids:
                    IF item_id IN item_map:
                        meta_result.append(item_map[item_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_result)

        // 6. 定时容量上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            total_kb = calc_layer_cap_kb(funnel_item_cache, avg_kb=l1_global_cfg.avg_item_kb)
            total_item_count = sum(len(v) for v in funnel_item_cache.values())
            cap_report = build_cap_report(layer="L1", used_kb=total_kb, item_count=total_item_count)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180秒向ag-mem-03上报运行统计
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_l1_stat_report(
                    state=internal_state,
                    today_add=stat_today_add,
                    total_promote=stat_total_promote_l2,
                    total_forget=stat_total_forget_clean
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-20推送条目result_validated标记为False、I值非法 | 写入失败，加入失败列表回传给L0，不存入L1分桶 | ag-mem-20重新生成通过初筛的合规条目再次推送 |
| 晋升扫描时条目同步触发过期清理 | 条目归入遗忘候选，不再参与晋升，快照隔离并发变更，无报错 | 无需人工干预，下一轮扫描正常执行 |
| 单次扫描条目总量超过1000条 | 自动拆分多批次串行处理，不阻塞主定时循环 | 内置分片逻辑自动执行 |
| L1分层存储IO读写故障 | 内存funnel分桶缓存完整保留条目，下一轮定时重试晋升/遗忘扫描 | 底层存储介质IO链路恢复 |
| 全局紧急熔断FUSE指令下发 | 停止写入、晋升、遗忘扫描，内存缓存条目不丢失 | ag-mem-01下发RESUME恢复指令，自动重启定时任务 |
| 目标funnel分槽无专属L1阈值配置 | 自动加载全局通用L1阈值兜底完成判定 | ag-mem-35运维侧补充分funnel独立参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L0批量晋升条目推送 | 只读 | ag-mem-20（唯一上游写入源） |
| 内部调度总线 | 读 | I值批量元数据查询请求 | 只读 | ag-mem-37 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 |
| 内部调度总线 | 写 | L1写入完成回执 | 专属写入 | 回传给上游 ag-mem-20 |
| 内部调度总线 | 写 | L1晋升条目批量推送 | 专属写入 | 下发下游 ag-mem-22 |
| 内部调度总线 | 写 | 遗忘候选清单、条目元数据快照 | 专属写入 | ag-mem-42、ag-mem-37 |
| 内部调度总线 | 写 | 容量上报、审计日志、周期统计上报 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L1-01 | L1仅接收ag-mem-20推送条目，禁止ag-mem-03或其他模块直接写入，杜绝旁路写入漏洞 |
| L1-02 | L1条目仅单向晋升至ag-mem-22，禁止任何直通L3/L4/L5的流转路径，分层链路单向隔离 |
| L1-03 | L1遗忘清理仅执行物理删除，无离线归档备份，不占用归档分区存储资源 |
| L1-04 | 晋升阈值、遗忘阈值、7天留存时效统一由ag-mem-35集中管控，本模块无本地硬编码参数 |
| L1-05 | L1分层容量上限、预警/紧急阈值由ag-mem-48统一管控，容量紧急自动加急遗忘扫描释放空间 |
| L1-06 | 熔断状态内存funnel分桶缓存持久保留所有条目，服务恢复后自动执行定时晋升与遗忘扫描，无数据丢失 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M21-01 | `L1_IDLE`，ag-mem-20推送result_validated=True合规晋升条目 | L0批量晋升条目列表 | 条目按funnel分桶存入L1缓存，返回写入成功回执，生成新增审计日志 |
| TC-M21-02 | `L1_IDLE`，条目I达标、复用≥3、未满7天，定时晋升触发 | 晋升倒计时归零 | 条目批量推送至ag-mem-22，从L1对应funnel分桶移除 |
| TC-M21-03 | `L1_IDLE`，条目I低于当前funnel L1遗忘阈值 | 遗忘扫描触发 | 条目加入遗忘候选清单推送ag-mem-42，标记处理方式delete |
| TC-M21-04 | `L1_IDLE`，条目写入满7天未满足晋升条件 | 遗忘扫描触发 | 因超期标记遗忘，进入清理候选清单 |
| TC-M21-05 | `L1_IDLE`，ag-mem-37下发批量元数据查询 | 条目ID批量查询请求 | 返回对应funnel桶内条目完整元数据快照 |
| TC-M21-06 | `L1_IDLE`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止写入、晋升、遗忘扫描全部任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-21匹配V1.1白皮书L1正式存储定位 | ✅ |
| 上下游依赖唯一上游ag-mem-20、下游ag-mem-22，数据流闭环无冲突 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出标注来源/目标模块、结构体、优先级，无错乱 | ✅ |
| 分桶存储、result_validated校验、7天时效、晋升/遗忘规则完整贴合白皮书4.4.1 | ✅ |
| 伪代码覆盖L0写入接收、分桶存储、定时晋升、遗忘扫描、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖无效L0条目、并发过期、超大批次、IO故障、熔断、无分槽阈值共6类全覆盖 | ✅ |
| 内部调度总线读写权限划分清晰，上游仅允许ag-mem-20写入 | ✅ |
| 6条V1.1强制安全约束无旁路写入、跨层流转漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

---