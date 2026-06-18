# ag-mem-03 漏斗二调度单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书任务经验漏斗业务调度中枢）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-03 |
| 模块名称 | 漏斗二调度单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 业务调度中枢（串联前置分槽组+五层记忆存储全链路） |
| 核心职责 | 承接ag-mem-01总控F0全局调度指令，统筹漏斗二全链路业务流程；统一接收ag-mem15~19前置分槽大盘统计、冷热/负载/生命周期告警、ag-mem20~30各分层存储运行指标；驱动分层记忆定时晋升、归档扫描、I值刷新、遗忘淘汰批量任务；接收运维人工记忆操作指令（人工归档、冻结、分槽清理）并分发至对应执行模块；汇总全漏斗业务指标、资源风险报表供给运维面板；定时上报自身调度内存开销至ag-mem-48；所有批量任务启停、人工运维操作、链路风险告警全量写入ag-mem-51审计日志；仅做流程调度与任务分发，无原始记忆读写、无向量计算、无持久存储能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度，接收全局PAUSE/RESUME/FUSE指令、全局熔断统计大盘）、ag-mem-35（三维权重配置单元，读取分层晋升周期、归档扫描频率、批量任务分片上限、遗忘策略参数）、ag-mem-48（全局容量配额管控，上报调度缓存内存占用）、前置分槽组ag-mem15/16/17/18/19（接收分槽负载、冷热、生命周期、全局汇总报表）、五层存储配套单元ag-mem20~30（读取分层容量、条目存量、运行状态）、ag-mem37/40/42/45（接收I刷新、遗忘扫描、冗余删除、安全校验运行统计） |
| 被依赖模块 | ag-mem15~19（下发分槽资源优化处置提示）、ag-mem20~30（下发分层晋升、归档扫描、人工归档/冻结任务指令）、ag-mem37（下发全局I值批量刷新调度信号）、ag-mem40（下发全分层遗忘扫描调度信号）、ag-mem42（下发批量冗余记忆清理调度信号）、运维面板（输出漏斗二全链路业务大盘、资源风险告警）、ag-mem48（接收调度缓存内存定时上报）、ag-mem-51（记录全漏斗任务调度、人工操作审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 业务调度待机就绪 | `FUNNEL_IDLE` | 无批量调度任务，等待定时周期或人工指令，全链路正常运行 | 系统初始化完成、熔断恢复、上一轮批量任务全部执行完毕 |
| 链路指标采集缓存 | `METRIC_FETCH` | 同步拉取前置分槽、五层存储、配套辅助单元全量运行指标存入本地缓存 | 定时调度周期倒计时归零 |
| 批量任务生成计算 | `TASK_GEN_CALC` | 根据指标与配置生成晋升、归档、I刷新、遗忘清理、分槽优化多类批量任务清单 | 全链路指标采集完成 |
| 任务批量分发执行 | `TASK_DISPATCH` | 分片下发各类调度任务至对应业务模块，同步推送资源风险告警至运维面板 | 全部批量任务计算生成完毕 |
| 调度暂停降级 | `FUNNEL_PAUSE` | 收到ag-mem-01下发PAUSE指令，暂停所有非核心定时批量任务，仅保留指标采集与日志上报 | F0下发半熔断PAUSE指令；RESUME切回FUNNEL_IDLE |
| 调度完全冻结 | `FUNNEL_FUSE` | 收到ag-mem-01下发FUSE全熔断指令，停止所有指标拉取、任务生成、任务分发 | F0下发全熔断FUSE指令；RESUME恢复待机 |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| F0全局调度控制指令 | Enum（PAUSE/RESUME/FUSE）、全局熔断统计报表 | ag-mem-01 总控F0全局熔断调度 | 全局熔断等级切换、定时全局统计上报 | 紧急 |
| 前置分槽全套指标报表 | 分槽负载告警、冷热预警、闲置分槽回收提示、全局分槽汇总大盘 | ag-mem15/16/17/18/19 | 各分槽辅助单元180s周期统计上报 | 高 |
| 五层存储分层运行指标 | L0~L5容量占用、条目总量、归档进度、写入吞吐量统计 | ag-mem20~30 | 各存储层180s周期运行统计上报 | 高 |
| 辅助计算单元运行统计 | I值刷新进度、遗忘扫描进度、冗余删除执行量、安全校验拦截统计 | ag-mem37/40/42/45 | 配套单元周期统计上报 | 高 |
| 漏斗二调度业务配置回执 | Struct（分层晋升周期、归档扫描间隔、单任务最大分片数量、遗忘批量上限、分槽风险处置阈值） | ag-mem-35 三维权重配置单元 | 模块初始化、调度策略运维更新 | 普通 |
| 运维人工记忆操作指令 | Struct（操作类型：批量晋升/人工归档/分槽清理/顶层条目冻结、目标funnel/abs_id清单、执行优先级） | 运维后台面板 | 人工发起记忆批量运维操作 | 高 |
| 漏斗调度状态批量查询请求 | Struct（指定模块/全漏斗链路指标导出） | 运维面板、各记忆业务模块 | 运维查看业务大盘、模块读取调度全局任务状态 | 普通 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层记忆批量晋升调度指令 | List<Struct>（目标分层、分片数量、扫描范围、执行优先级） | ag-mem20~28 | 到达分层晋升定时周期，分槽/分层存量达到晋升阈值 | 高 |
| 分层归档扫描调度指令 | List<Struct>（funnel范围、归档倍率、冷数据优先标记） | ag-mem26、ag-mem28 | 分层容量预警、长期冷条目占比超标 | 高 |
| 全局I值刷新/遗忘扫描调度信号 | Struct（全量/分funnel局部扫描、批量处理上限） | ag-mem37、ag-mem40 | 到达定时权重刷新周期、分槽冷热指标大幅变动 | 高 |
| 冗余记忆批量清理调度信号 | Struct（分层范围、待清理条目预估数量） | ag-mem42 | 遗忘扫描输出大量淘汰候选条目 | 普通 |
| 分槽资源优化处置提示 | Struct（冷分槽限流、闲置分槽清理建议） | ag-mem15 | 接收ag-mem16/17/18分槽风险告警 | 普通 |
| 漏斗二全链路业务大盘&风险告警报表 | Struct（分层条目总量、容量占用、待晋升/待归档数量、高风险分槽清单、调度任务执行统计） | 运维告警面板 | 每一轮指标采集与任务分发完成 | 普通 |
| 调度缓存内存占用上报 | Struct（单元标识ag-mem-03、任务缓存KB、指标缓存KB、待执行任务总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量任务分发后即时上报 | 普通 |
| 漏斗调度审计日志 | Struct（事件类型、调度任务类型、分片执行数量、人工操作账号、受影响funnel/分层、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 批量任务下发、人工运维操作、风险告警推送完成 | 普通 |
| 漏斗周期运行统计上报 | Struct（当前调度状态、今日晋升总批次、归档扫描总次数、人工运维操作总量、高风险资源分组数量） | ag-mem-01 总控F0 | 每180秒周期性上报漏斗业务大盘给全局调度底座 | 普通 |

## 漏斗二调度核心规则（V1.1任务经验漏斗全链路调度规范）
### 1. 全局调度配置参数（ag-mem-35统一分发）
1. L0→L1晋升周期：60s；L1→L2：300s；L2→L3：900s；L3→L4：1800s；L4→L5抽象提炼周期：3600s
2. 分层归档扫描基础间隔：3600s，容量预警自动倍率提升至2~5倍；
3. 单批次调度任务分片上限：1000条，超量自动拆分串行下发；
4. 全局I值重算周期：7200s；全分层遗忘扫描周期：14400s；
5. 高风险分槽判定复用ag-mem-19聚合统计风险阈值。

### 2. 多类型调度任务触发条件
1. 分层晋升任务：到达定时周期 + 当前分层条目存量超过晋升触发阈值；
2. 分层归档扫描：分层容量占用≥80%预警阈值 或 冷分槽占比≥70%；
3. I值刷新任务：到达定时刷新周期、新增大量顶层抽象单元、分槽冷热负载指标大幅变动；
4. 遗忘淘汰任务：定时全链路扫描、分层容量紧急溢出、运维手动发起清理；
5. 分槽优化提示：接收冷热/负载/生命周期高风险告警，推送资源优化建议至ag-mem-15。

### 3. 熔断下任务降级规则
1. FUNNEL_PAUSE（半熔断PAUSE）：暂停定时晋升、归档、遗忘扫描，仅保留指标采集、人工紧急归档、日志上报；
2. FUNNEL_FUSE（全熔断FUSE）：停止全部指标拉取、任务生成、任务下发，仅维持心跳与审计日志写入。

### 4. 流转强制约束
1. 仅接收ag-mem-01全局调度指令，所有业务任务调度不可绕过F0全局熔断管控；
2. 无任何记忆条目读写、向量蒸馏、持久存储能力，仅做任务分发与指标聚合；
3. 单向任务分发：仅向对应业务模块下发调度指令，不直接修改分层存储/分槽元数据；
4. 全链路闭环：串联前置分槽→五层存储→配套计算单元，统一汇总所有业务指标对外输出大盘。

### 5. 批量约束
单类调度任务单次最大分片1000条，超量自动拆分多轮下发，避免单模块瞬时IO/算力冲击。

## 核心处理逻辑
```
FUNCTION funnel_two_scheduler_main_loop():
    STATE_IDLE = FUNNEL_IDLE
    STATE_FETCH = METRIC_FETCH
    STATE_GEN = TASK_GEN_CALC
    STATE_DISPATCH = TASK_DISPATCH
    STATE_PAUSE = FUNNEL_PAUSE
    STATE_FUSE = FUNNEL_FUSE

    internal_state = STATE_IDLE
    // 加载漏斗调度全局配置
    schedule_cfg = query_funnel_schedule_config(from_m35="ag-mem-35")
    max_task_slice = schedule_cfg.max_batch_task
    promote_cycle_map = schedule_cfg.layer_promote_cycle
    archive_base_cycle = schedule_cfg.archive_scan_interval
    refresh_I_cycle = schedule_cfg.global_I_refresh_cycle
    forget_scan_cycle = schedule_cfg.full_forget_cycle

    metric_cache = {}
    pending_task_list = []
    stat_promote_batch = 0
    stat_archive_scan = 0
    stat_manual_op = 0
    last_metric_scan_ts = NOW()
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：处理F0全局熔断调度指令
        IF 收到F0全局调度控制指令:
            fuse_cmd = 获取全局指令
            old_state = internal_state
            if fuse_cmd == "FUSE":
                internal_state = STATE_FUSE
                metric_cache.clear()
                pending_task_list.clear()
                send_audit_log(target="ag-mem-51", log_data=build_schedule_state_audit(old_state, internal_state, "F0全局全熔断冻结调度", now_ts))
                CONTINUE
            elif fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSE
                send_audit_log(target="ag-mem-51", log_data=build_schedule_state_audit(old_state, internal_state, "F0半熔断暂停批量任务", now_ts))
            elif fuse_cmd == "RESUME":
                internal_state = STATE_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_schedule_state_audit(old_state, internal_state, "F0恢复漏斗调度", now_ts))

        // 全熔断状态直接跳过所有业务逻辑
        IF internal_state == STATE_FUSE:
            SLEEP 10ms
            CONTINUE

        // 2. 接收并缓存全链路各类指标上报
        IF 收到前置分槽/分层存储/辅助单元周期统计报表:
            metric = 获取指标报表
            metric_cache[metric.module_id] = metric

        // 3. 接收运维人工记忆操作指令
        IF 收到运维人工记忆操作指令:
            manual_op = 获取人工操作参数
            internal_state = STATE_GEN
            manual_task = build_manual_schedule_task(op=manual_op, slice_limit=max_task_slice)
            pending_task_list.append(manual_task)
            stat_manual_op += 1
            internal_state = STATE_DISPATCH
            // 分片下发人工操作任务
            slice_tasks = split_slice(pending_task_list, max_task_slice)
            for slice in slice_tasks:
                dispatch_task_batch(task_slice=slice)
            send_audit_log(target="ag-mem-51", log_data=build_manual_op_audit(op=manual_op, ts=now_ts))
            pending_task_list.clear()
            internal_state = STATE_IDLE

        // 4. 定时全链路指标采集+自动批量任务生成（仅待机状态执行）
        IF internal_state == STATE_IDLE and (now_ts - last_metric_scan_ts) >= schedule_cfg.metric_scan_interval:
            internal_state = STATE_FETCH
            // 主动拉取全链路完整指标补齐缓存
            full_metric_set = fetch_all_funnel_metrics()
            metric_cache.update(full_metric_set)
            internal_state = STATE_GEN
            // 自动生成各类定时调度任务
            auto_task_batch = generate_all_schedule_tasks(metric_cache, schedule_cfg, now_ts)
            pending_task_list.extend(auto_task_batch)
            // 统计任务类型数量
            for task in auto_task_batch:
                if task.task_type == "layer_promote":
                    stat_promote_batch += 1
                elif task.task_type == "archive_scan":
                    stat_archive_scan += 1
            internal_state = STATE_DISPATCH
            // 分片下发自动批量任务
            slice_tasks = split_slice(pending_task_list, max_task_slice)
            for slice in slice_tasks:
                dispatch_task_batch(task_slice=slice)
            // 组装全漏斗业务大盘与风险告警
            funnel_overview = build_funnel_overview_report(metric_cache, schedule_cfg)
            send_overview_report(target="运维告警面板", report=funnel_overview)
            // 写入自动调度审计日志
            audit_log = build_auto_schedule_audit(task_total=len(pending_task_list), ts=now_ts)
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            pending_task_list.clear()
            last_metric_scan_ts = now_ts
            internal_state = STATE_IDLE

        // 5. 响应漏斗调度状态批量查询请求
        IF 收到漏斗调度状态批量查询请求:
            query_param = 获取查询条件
            state_snap = build_scheduler_full_snapshot(metric_cache, internal_state, pending_task_list)
            send_snapshot(target_list=query_param.requester, snapshot_data=state_snap)

        // 6. 60s定时内存上报 + 180s周期统计上报至ag-mem-01
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_schedule_cache_kb(metric_cache, pending_task_list, schedule_cfg.avg_metric_kb)
            cap_report = build_cap_report(layer="ag-mem-03", used_kb=cache_kb, pending_task_count=len(pending_task_list))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s向F0全局调度输出漏斗业务统计大盘
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                stat_report = build_funnel_runtime_stat(
                    state=internal_state,
                    total_promote_batch=stat_promote_batch,
                    total_archive_scan=stat_archive_scan,
                    total_manual_op=stat_manual_op
                )
                send_stat_report(target="ag-mem-01", report=stat_report)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 某业务模块指标上报缺失、字段不全 | 本轮自动任务跳过依赖该模块的调度逻辑，写入告警审计，等待下一周期完整指标 | 对应业务模块恢复正常周期统计上报 |
| 单次生成调度任务总量超过1000分片上限 | 自动拆分多批次串行下发，控制单模块并发任务压力 | 内置分片逻辑自动执行 |
| 目标模块接收调度任务无响应 | 记录任务下发失败告警，下一轮指标扫描重新生成补发任务 | 下游业务模块服务恢复正常接收指令 |
| 调度指标缓存内存溢出 | 清理超过3轮周期的过期指标缓存，向ag-mem-48上报容量风险告警 | 扩容调度内存或缩短指标扫描周期 |
| ag-mem-35调度配置拉取失败 | 加载内置通用晋升/归档周期兜底执行调度，输出配置缺失告警 | ag-mem-35恢复下发完整调度策略 |
| 半熔断PAUSE状态下触发自动批量任务 | 直接丢弃自动任务，仅保留人工紧急运维任务可下发 | ag-mem-01下发RESUME恢复正常调度 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 全局调度总线 | 读 | F0全局熔断指令、全链路业务指标、调度配置、调度状态查询、人工运维指令 | 只读 | ag-mem-01、全漏斗业务模块、ag-mem-35、运维面板 |
| 内部业务调度总线 | 写 | 分层晋升/归档/I刷新/遗忘清理/分槽优化调度任务指令 | 专属分发写入 | ag-mem15~30、ag-mem37/40/42 |
| 运维告警总线 | 写 | 漏斗二全链路业务大盘、资源风险告警报表 | 专属写入 | 运维告警面板 |
| 内部调度总线 | 写 | 调度内存容量上报、调度审计日志、周期业务统计报表 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-01 |

## 安全边界（V1.1漏斗调度强制规范）
| 规则编号 | 内容 |
|:---:|------|
| SCH03-01 | 所有批量任务调度必须校验ag-mem-01下发的全局熔断状态，熔断降级策略硬约束，禁止无视熔断强行下发业务任务，防止故障扩散 |
| SCH03-02 | 无任何记忆条目读写、分槽元数据修改、持久存储操作权限，仅下发调度指令交由对应业务模块执行，操作权限分层隔离 |
| SCH03-03 | 分层晋升周期、归档频率、批量分片上限全部由ag-mem-35集中管控，本地无硬编码调度业务参数，策略统一运维管控 |
| SCH03-04 | 自动定时任务、人工运维操作、风险告警下发全量写入ag-mem-51审计日志，记录任务类型、作用分层/分槽、操作人员，支撑业务全链路溯源 |
| SCH03-05 | 批量任务分片限流，单次最多下发1000条任务分片，规避瞬时大批量指令抢占总线与下游模块算力IO资源 |
| SCH03-06 | 全熔断状态清空指标与待执行任务缓存，恢复后重新采集全链路指标生成新任务，杜绝基于过期指标下发错误调度指令 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-SCH03-01 | `FUNNEL_IDLE`，到达L3→L4定时晋升周期，L3容量达到晋升阈值 | 全分层存储容量指标快照 | 生成L3晋升批量调度任务分片下发至ag-mem25，输出业务大盘报表，写入自动调度审计日志 |
| TC-SCH03-02 | `FUNNEL_IDLE`，L4存储占用85%容量预警阈值 | L4分层容量指标上报 | 下发加急归档扫描指令至ag-mem26、ag-mem28，风险告警写入运维大盘 |
| TC-SCH03-03 | `FUNNEL_IDLE`，运维下发批量人工归档顶层条目指令 | 人工运维操作指令 | 拆分分片下发归档任务至ag-mem30，记录人工操作审计日志 |
| TC-SCH03-04 | `FUNNEL_IDLE`，收到ag-mem-01下发PAUSE半熔断指令 | 全局半熔断调度指令 | 切换`FUNNEL_PAUSE`，停止所有自动晋升/归档定时任务，仅保留人工紧急操作通路 |
| TC-SCH03-05 | `FUNNEL_IDLE`，单次自动任务生成1300条待处理条目 | 超大批量分层存量指标 | 自动拆分为2个分片串行下发调度任务，无下游模块IO阻塞 |
| TC-SCH03-06 | `FUNNEL_PAUSE`，到达定时I值刷新周期 | 定时指标扫描触发自动任务生成 | 自动任务直接丢弃，不向ag-mem37下发刷新调度信号 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-03匹配白皮书漏斗二专属业务调度中枢定位 | ✅ |
| 上下游串联前置分槽、五层存储、配套计算单元、F0全局调度，全漏斗链路闭环无冲突 | ✅ |
| 5种完整调度状态，覆盖待机、指标采集、任务生成、任务分发、熔断降级全场景 | ✅ |
| 输入输出完整标注收发模块、数据结构体、优先级，全链路数据流无错乱 | ✅ |
| 分层晋升周期、归档倍率、熔断降级、分片限流规则严格对齐V1.1任务经验漏斗调度规范 | ✅ |
| 伪代码覆盖全局熔断处理、指标缓存、自动定时任务、人工运维任务、状态查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖指标缺失、超大批量任务、下游无响应、缓存溢出、配置缺失、半熔断拦截自动任务共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅具备任务分发权限，无直接修改记忆/分槽数据权限 | ✅ |
| 6条V1.1安全约束约束熔断执行、隔离数据操作、统一策略管控、全流程审计、限流防风暴、规避过期任务 | ✅ |
| 6条自动化测试用例覆盖全部漏斗调度核心业务场景 | ✅ |

---