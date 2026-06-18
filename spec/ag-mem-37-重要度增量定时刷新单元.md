# ag-mem-37-重要度增量定时刷新单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-37 |
| 模块名称 | 重要度增量定时刷新单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制 |
| 核心职责 | 全漏斗定时调度核心，按固定周期批量重新计算L1~L4所有经验条目综合重要度I值；从ag-mem-35拉取分槽专属三维权重完成I值重算，将更新后的I值回写至ag-mem-20~26分层存储；I值刷新完成后自动发起遗忘扫描请求推送至ag-mem-40；同时向ag-mem-25、ag-mem-26推送I值变更数据用于L3归并、L4抽象提炼；支持定时周期动态配置、手动强制刷新、容量预警触发临时加急刷新；全量I值变更记录推送ag-mem-51审计日志；L5条目跳过I值重算，永久固定I值不参与刷新。不负责遗忘判定、存储读写以外的业务逻辑。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断）、ag-mem-35（三维权重配置单元，读取I计算权重）、ag-mem-20~26（分层存储，读取条目元数据、回写新I值）、ag-mem-48（全局容量配额单元，接收容量预警加急指令） |
| 被依赖模块 | ag-mem-40（遗忘阈值判定单元，定时推送遗忘扫描请求）、ag-mem-25（L3归并单元，接收I值更新快照）、ag-mem-26（L4长期存储单元，同步I变更用于抽象提炼）、ag-mem-51（记忆变更日志追溯单元，记录批量I刷新事件）、ag-mem-03（漏斗二调度单元，周期上报刷新统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 定时待机 | `TIMER_IDLE` | 定时计时器倒计时，等待下一轮刷新周期，可接收手动/加急刷新指令 | 系统初始化、批量刷新全部完成、熔断恢复 |
| 元数据批量拉取 | `FETCH_META` | 按分层、分槽批量读取条目复用、S值、时效元数据 | 定时倒计时归零、手动强制刷新、容量预警加急 |
| I值批量重算 | `RECALC_I` | 调用ag-mem-35权重，逐条重新计算条目综合重要度I | 条目元数据快照拉取完成 |
| 数据回写与下游推送 | `WRITE_SYNC` | 将新I值回写分层存储，同步快照至L3/L4归并单元，下发遗忘扫描请求 | 整批条目I值重算完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，终止正在执行的刷新批次，清空待执行定时任务 | F0下发FUSE熔断指令；RESUME切回TIMER_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 定时周期配置查询回执 | Struct（默认刷新周期秒、容量加急周期秒） | ag-mem-35 三维权重配置单元 | 模块初始化、周期参数更新 | 普通 |
| 批量条目元数据快照 | List（条目ID、层级、分槽ID、复用次数、S值、新鲜度系数、旧I值） | ag-mem-20~26 分层存储单元 | 启动刷新任务后批量拉取 | 高 |
| 手动强制刷新指令 | Struct（目标分层/分槽范围、管理员ID、双重确认码） | 人工运维接口 | 人工主动触发全量/局部I值刷新 | **最高** |
| 容量加急刷新指令 | Struct（触发层级、容量预警等级） | ag-mem-48 全局容量配额单元 | 存储占用接近阈值，需要提前执行遗忘清理 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| I值批量更新回写指令 | List（条目ID、新I值、旧I值、层级、分槽） | ag-mem-20~26 分层存储单元 | 单批次I值重算完成 | 高 |
| I变更同步快照 | List（条目ID、新I值、层级） | ag-mem-25 L3归并单元、ag-mem-26 L4长期存储单元 | I值回写完成 | 高 |
| 遗忘扫描触发请求 | Struct（目标层级、全量扫描、触发原因=定时刷新） | ag-mem-40 遗忘阈值判定单元 | 整轮I值刷新同步完成 | 高 |
| I刷新审计事件日志 | Struct（批次ID、刷新条目总量、分层刷新数量、耗时、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮完整刷新任务结束 | 高 |
| 定时刷新周期状态上报 | Struct（当前状态、今日刷新总批次、刷新条目总数、加急触发次数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## I值重算核心规则（V1.1统一计算公式）
1. 计算公式
`I = W_reuse × reuse_cnt + W_safe × S_value + W_time × fresh_coeff`
- W_reuse：复用权重、W_safe：安全S权重、W_time：时效新鲜度权重
- 三组权重总和恒等于1，由ag-mem-35分槽独立下发
2. 分层过滤规则
- L5：永久跳过I值刷新，不参与批量计算
- L1/L2/L3/L4：全部纳入定时重算范围
3. 刷新触发类型区分
| 触发类型 | 扫描范围 | 刷新周期 | 下游动作 |
|------|:---:|:---:|------|
| 常规定时刷新 | L1~L4全分槽全条目 | 默认3600秒 | 自动发起全层级遗忘扫描 |
| 容量加急刷新 | 预警对应层级 | 加急600秒 | 强制扫描对应层级L4 |
| 人工强制刷新 | 指定分槽/分层/全量 | 立即执行，不等待计时器 | 可选择是否触发遗忘扫描 |
4. 批量分片约束
单次计算分片上限1000条，超量自动拆分串行执行，避免内存与IO过载。

## 核心处理逻辑
```
FUNCTION importance_refresh_main_loop():
    STATE_IDLE = TIMER_IDLE
    STATE_FETCH = FETCH_META
    STATE_CALC = RECALC_I
    STATE_SYNC = WRITE_SYNC
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    # 初始化读取定时周期参数
    timer_cfg = query_timer_config(from_m35="ag-mem-35")
    normal_cycle = timer_cfg.normal_refresh_sec
    emergency_cycle = timer_cfg.emergency_refresh_sec
    timer_countdown = normal_cycle
    # 全局统计指标
    stat_total_batch = 0
    stat_total_item = 0
    stat_emergency_trigger = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 最高优先级：全局熔断调度
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                重置计时器倒计时，中断当前刷新分片
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE
                timer_countdown = normal_cycle

        // 2. 容量加急刷新指令处理（高优先级，跳过定时等待）
        IF 收到容量加急刷新指令:
            emergency_req = 获取加急指令
            target_layer = emergency_req.触发层级
            stat_emergency_trigger += 1
            EXECUTE_REFRESH_TASK(target_layer=target_layer, scan_all=False, force_L4=True)
            timer_countdown = normal_cycle

        // 3. 人工强制刷新指令处理（最高优先级）
        IF 收到手动强制刷新指令:
            manual_req = 获取手动刷新指令
            admin_id = manual_req.管理员ID
            target_range = manual_req.目标分层/分槽范围
            # 人工操作双重确认校验
            double_verify = launch_admin_double_verify(admin_id, timeout=60000, code=manual_req.双重确认码)
            IF NOT double_verify.通过:
                向人工运维接口返回刷新拒绝通知("双重确认校验失败")
                CONTINUE
            EXECUTE_REFRESH_TASK(target_layer=target_range, scan_all=True, force_L4=False)
            timer_countdown = normal_cycle

        // 4. 常规定时倒计时，归零启动全量刷新
        IF internal_state == STATE_IDLE:
            timer_countdown -= 10
            IF timer_countdown <= 0:
                EXECUTE_REFRESH_TASK(target_layer=["L1","L2","L3","L4"], scan_all=True, force_L4=False)
                stat_total_batch += 1
                timer_countdown = normal_cycle

        // 5. 周期上报运行统计
        IF NOW() - last_report_ts >= 180 * 1000:
            stat_report = build_refresh_stat_report(
                current_state=internal_state,
                total_batch=stat_total_batch,
                total_refresh_item=stat_total_item,
                emergency_times=stat_emergency_trigger
            )
            send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms

# 子函数：统一封装刷新任务全流程
FUNCTION EXECUTE_REFRESH_TASK(target_layer, scan_all, force_L4):
    global internal_state, stat_total_item
    batch_start_ts = NOW()
    internal_state = FETCH_META
    full_refresh_item = []
    # 1. 批量拉取目标分层条目元数据，过滤L5
    layer_meta_list = batch_fetch_layer_meta(target_layers=target_layer, skip_layer="L5")
    split_meta_batch = split_list(layer_meta_list, batch_size=1000)

    FOR slice_meta IN split_meta_batch:
        internal_state = RECALC_I
        i_update_batch = []
        # 逐条重算I值
        FOR item_meta IN slice_meta:
            slot_id = item_meta.分槽ID
            # 读取当前分槽三维权重
            slot_weight = query_slot_weight(slot_id=slot_id, from_m35="ag-mem-35")
            reuse_w = slot_weight.W_reuse
            safe_w = slot_weight.W_safe
            time_w = slot_weight.W_time
            # I计算公式
            new_I = reuse_w * item_meta.复用次数 + safe_w * item_meta.S值 + time_w * item_meta.新鲜度系数
            i_update_batch.append({
                "item_id": item_meta.条目ID,
                "old_I": item_meta.旧I值,
                "new_I": new_I,
                "layer": item_meta.层级,
                "slot_id": slot_id
            })
        full_refresh_item.extend(i_update_batch)
        stat_total_item += LEN(i_update_batch)

    # 2. 回写新I值至分层存储，同步快照至L3/L4归并单元
    internal_state = WRITE_SYNC
    send_i_write_batch(target="ag-mem-20~26", batch_data=full_refresh_item)
    send_i_sync_snapshot(target=["ag-mem-25","ag-mem-26"], snapshot=full_refresh_item)

    # 3. 生成遗忘扫描请求下发ag-mem-40
    forget_scan_req = build_forget_scan_request(
        target_layers=target_layer,
        trigger_cause="timer_refresh",
        force_scan_L4=force_L4
    )
    send_scan_request(target="ag-mem-40", req_data=forget_scan_req)

    # 4. 生成审计日志推送ag-mem-51
    audit_log = build_refresh_audit_log(
        batch_uuid=gen_uuid(),
        refresh_total=LEN(full_refresh_item),
        cost_ms=NOW() - batch_start_ts,
        trigger_type=IF force_L4 THEN "capacity_emergency" ELSE IF 人工触发 THEN "manual" ELSE "timer"
    )
    send_audit_log(target="ag-mem-51", log=audit_log)

    internal_state = TIMER_IDLE
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 拉取元数据时分层存储离线 | 跳过当前分层，记录告警日志，其余分层正常刷新 | 分层存储服务恢复，下一轮定时刷新补算 |
| 分槽权重查询返回失败 | 临时加载全局默认三维权重兜底计算I值 | ag-mem-35连通恢复，下一轮自动拉取最新配置 |
| 单分片条目超1000条 | 自动切分多批次串行计算、回写，不阻塞定时流程 | 内置分片逻辑无需人工干预 |
| I值回写存储IO故障 | 本条条目标记刷新失败，记录日志，不阻断同批次其他条目 | 存储IO恢复后下一轮刷新覆盖重算 |
| 全局紧急熔断触发 | 立即终止当前分片计算，未回写数据丢弃，计时器重置 | F0下发RESUME恢复指令 |
| L5条目混入待刷新列表 | 自动过滤剔除，不参与I值计算、回写、遗忘扫描 | 底层存储过滤逻辑永久生效 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 定时周期配置回执 | 只读 | ag-mem-35 下发 |
| 内部调度总线 | 读 | 分层条目元数据快照 | 只读 | ag-mem-20~26 返回 |
| 内部调度总线 | 读 | 人工强制刷新指令 | 只读 | 人工运维接口 |
| 内部调度总线 | 读 | 容量加急刷新指令 | 只读 | ag-mem-48 下发 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | I值批量更新回写指令 | 专属写入 | 向 ag-mem-20~26 同步 |
| 内部调度总线 | 写 | I变更同步快照 | 专属写入 | 向 ag-mem-25、ag-mem-26 推送 |
| 内部调度总线 | 写 | 遗忘扫描触发请求 | 专属写入 | 向 ag-mem-40 下发 |
| 内部调度总线 | 写 | I刷新审计日志、周期状态上报 | 事件/周期写入 | ag-mem-51、ag-mem-03 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| T-01 | L5层级条目永久屏蔽I值重算，任何刷新指令均自动过滤，顶层长效经验重要度固定不变 |
| T-02 | I值计算权重仅信任ag-mem-35统一配置，禁止模块内置自定义权重参数 |
| T-03 | 人工全量强制刷新必须完成管理员双重确认，防止误触发大规模数据重算 |
| T-04 | 容量加急刷新仅触发预警层级，不执行全分槽全量扫描，减少系统负载 |
| T-05 | 所有批量I值变更操作完整记录审计日志，留存条目新旧I值对比用于追溯 |
| T-06 | 仅允许ag-mem-40接收本模块发起的遗忘扫描请求，禁止其他模块篡改定时清理流程 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M37-01 | `TIMER_IDLE`，定时倒计时归零 | 无额外输入，计时器到期 | 全L1~L4条目重算I值，回写存储，下发常规遗忘扫描，生成审计日志 |
| TC-M37-02 | `TIMER_IDLE`，ag-mem-48下发L3容量加急指令 | 容量预警加急刷新请求（L3） | 仅刷新L3条目，强制扫描L4，加急统计计数+1 |
| TC-M37-03 | `TIMER_IDLE`，合法人工刷新指令+双重确认通过 | 人工全量刷新指令 | 立即启动全分层I重算，不等待定时倒计时 |
| TC-M37-04 | `TIMER_IDLE`，元数据列表包含L5条目 | 批量元数据快照携带L5条目ID | L5条目自动过滤，不参与I计算与回写 |
| TC-M37-05 | `TIMER_IDLE`，单次待刷新条目1200条 | 全分层元数据共1200条 | 自动拆分为2个分片串行执行，统计条目数累加正确 |
| TC-M37-06 | `TIMER_IDLE`，刷新中途接收FUSE熔断指令 | 全局紧急熔断指令 | 终止当前分片计算，切换SYSTEM_PAUSED，清空计时器 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、定时I刷新中枢定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| I值计算公式、三类刷新触发逻辑、分片约束完整 | ✅ |
| 伪代码覆盖定时倒计时、加急/人工/常规刷新、I重算、回写同步、遗忘扫描、审计日志全链路 | ✅ |
| 异常场景分层离线、权重兜底、分片拆分、IO故障、熔断、L5过滤共6类全覆盖 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无逻辑漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |
