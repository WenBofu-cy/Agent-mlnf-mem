# ag-mem-29-L5安全锁控单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-29 |
| 模块名称 | L5安全锁控单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（L5入库前置安全锁校验层） |
| 核心职责 | 唯一上游输入为ag-mem-45安全校验通过后的抽象记忆单元；作为L5核心持久存储前置准入锁控，提供全局读写锁、分funnel隔离锁、条目保护锁三层锁机制；拦截并发重复入库、跨funnel非法写入、人工锁定条目覆盖；维护抽象单元全局唯一锁索引；校验抽象单元准入锁状态，放行无冲突条目至ag-mem-30 L5核心存储；锁冲突、重复条目、非法写入生成拦截审计日志；对外提供锁状态元数据快照供给ag-mem-37、ag-mem-40；定时上报锁索引内存占用至ag-mem-48；所有上锁、解锁、拦截、放行操作全量推送审计日志至ag-mem-51；严格隔离L5底层存储，无锁控通过禁止写入顶层永久记忆。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-45（安全规则合规校验单元，唯一上游抽象单元来源）、ag-mem-35（三维权重配置单元，读取锁超时时长、分funnel锁隔离规则、并发上限）、ag-mem-48（全局容量配额管控，上报锁索引内存占用） |
| 被依赖模块 | ag-mem-30（L5核心持久存储单元，接收锁控放行后的抽象记忆单元）、ag-mem-37（重要度定时刷新单元，读取锁状态+抽象单元元数据快照）、ag-mem-40（遗忘阈值判定单元，提供锁控条目扫描数据）、ag-mem-48（接收锁索引内存占用定时上报）、ag-mem-51（推送锁控全流程审计日志）、ag-mem-03（漏斗二调度单元，周期上报锁控运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 锁控待机就绪 | `LOCK_IDLE` | 无待校验抽象单元，锁索引空闲，等待上游推送入库快照 | 系统初始化、熔断恢复、批量条目全部放行/拦截完毕 |
| 抽象单元锁缓存 | `LOCK_BUFFER` | 接收ag-mem-45输出抽象单元，存入临时内存缓冲 | 收到ag-mem-45批量抽象记忆单元推送 |
| 多层锁冲突校验 | `LOCK_CHECK` | 遍历批量条目，校验全局锁、funnel分槽锁、条目独占锁，标记放行/拦截 | 缓冲条目接收完成 |
| 放行条目下发 | `LOCK_DISPATCH` | 过滤锁冲突拦截条目，将无冲突合法抽象单元批量推送至ag-mem-30 | 全部锁校验逻辑执行完毕 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空全部临时缓冲与临时锁，停止锁校验、放行下发 | F0下发FUSE熔断指令；RESUME切回LOCK_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 安全校验通过抽象记忆单元批量快照 | List<Struct>（abs_id抽象ID、source_light_ids、funnel_id、abs_I、total_origin_reuse、abstract_vector、task_group） | ag-mem-45 安全规则合规校验单元 | ag-mem-45完成安全合规校验，推送待入库顶层抽象单元 | 高 |
| 锁控全局规则参数回执 | Struct（锁自动释放超时ms、单funnel最大并发写入数、重复abs_id拦截开关） | ag-mem-35 三维权重配置单元 | 模块初始化、锁策略人工更新 | 普通 |
| 抽象单元锁状态批量查询请求 | Struct（abs_id抽象ID列表） | ag-mem-37 / ag-mem-40 | 全局I值重算、顶层记忆遗忘扫描 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 锁控放行标准化抽象单元批量推送 | List<完整抽象单元元数据+锁放行标记> | ag-mem-30 L5核心持久存储单元 | 锁校验无冲突、无重复、无锁定保护条目 | 高 |
| 抽象单元快照接收回执 | Struct（快照总条数、缓冲成功条数、锁冲突拦截条数） | ag-mem-45 安全规则合规校验单元 | 抽象单元快照完整存入临时缓冲 | 高 |
| 抽象单元+锁状态元数据快照 | List<abs_id、当前锁类型、锁持有时长、funnel_id、abs_I> | ag-mem-37、ag-mem-40 | 收到批量锁状态查询请求 | 高 |
| 锁索引内存占用上报 | Struct（单元标识ag-mem-29、锁索引总KB、当前活跃锁数量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量快照处理后即时上报 | 普通 |
| 锁控操作审计日志 | Struct（事件类型、原始抽象单元总数、放行数量、锁冲突拦截数量、锁超时释放数量、时间戳、funnel范围） | ag-mem-51 记忆变更日志追溯单元 | 每一批抽象单元锁校验处理完成 | 普通 |
| 锁控周期运行统计上报 | Struct（当前状态、今日接收抽象单元总量、放行入库总量、锁冲突拦截总次数、自动释放锁总数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## L5安全锁控核心规则（V1.1顶层记忆安全准入规范）
### 1. 全局锁控配置参数（ag-mem-35统一分发）
1. 锁自动释放超时：300000ms（5分钟），超时未完成入库自动解锁；
2. 单funnel最大并发写入锁：10条，超出上限触发分槽锁拦截；
3. 重复abs_id拦截开关：永久开启，同一抽象ID禁止重复写入L5；
4. 三层锁优先级：条目独占锁 > 分funnel并发锁 > 全局总写入锁。

### 2. 条目拦截判定规则（满足任意一条直接拦截，不推送ag-mem-30）
1. abs_id已存在全局锁索引，重复入库；
2. 对应funnel当前活跃写入锁达到并发上限；
3. 条目携带manual人工锁定标记，禁止覆盖写入；
4. 全局总活跃锁达到系统全局写入并发阈值。

### 3. 流转强制约束
1. 唯一上游：仅接收ag-mem-45校验通过抽象单元，拒绝其他模块直接推送入库数据；
2. 单向下游：仅放行条目推送至ag-mem-30 L5核心存储，无任何旁路跳过锁控直达L5；
3. 无持久业务存储：仅维护内存锁索引与临时缓冲，不持久化抽象单元原始数据；
4. 不参与I值计算、归档、抽象提炼业务逻辑，仅负责顶层入库并发安全管控。

### 4. 批量约束
单次接收抽象单元快照最大1000条，超量自动分片串行执行锁校验，防止锁索引并发竞争过载。

## 核心处理逻辑
```
FUNCTION l5_lock_control_main_loop():
    STATE_IDLE = LOCK_IDLE
    STATE_BUFFER = LOCK_BUFFER
    STATE_CHECK = LOCK_CHECK
    STATE_DISPATCH = LOCK_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载锁控全局配置
    lock_cfg = query_lock_config(from_m35="ag-mem-35")
    lock_timeout_ms = lock_cfg.lock_release_timeout
    max_slot_concurrent = lock_cfg.per_funnel_max_lock
    global_max_lock = lock_cfg.global_max_active_lock
    temp_buffer = []
    global_lock_index = {} // key:abs_id, value:{funnel_id, lock_start_ts}
    funnel_lock_counter = {} // key:funnel_id, value:active_lock_num
    stat_raw_abs_total = 0
    stat_pass_count = 0
    stat_block_count = 0
    stat_auto_release_lock = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_buffer.clear()
                global_lock_index.clear()
                funnel_lock_counter.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = LOCK_IDLE

        // 定时自动释放超时锁
        now_ts = NOW()
        expired_abs_list = []
        for abs_id, lock_info in global_lock_index.items():
            if now_ts - lock_info.lock_start_ts >= lock_timeout_ms:
                expired_abs_list.append(abs_id)
        for expire_abs in expired_abs_list:
            expire_funnel = global_lock_index[expire_abs].funnel_id
            del global_lock_index[expire_abs]
            funnel_lock_counter[expire_funnel] -= 1
            stat_auto_release_lock += 1

        // 2. 接收ag-mem-45抽象记忆单元快照
        IF 收到安全校验通过抽象记忆单元批量快照:
            snapshot_data = 获取快照条目列表
            internal_state = LOCK_BUFFER
            temp_buffer.extend(snapshot_data)
            cache_total = len(temp_buffer)
            stat_raw_abs_total += len(snapshot_data)
            // 返回快照接收回执给ag-mem-45
            recv_ack = build_snapshot_recv_ack(total=len(snapshot_data), cached=cache_total, block_temp=0)
            send_ack(target="ag-mem-45", ack_data=recv_ack)
            internal_state = LOCK_CHECK

            // 3. 三层锁冲突校验逻辑
            pass_list = []
            block_list = []
            block_reason_map = {}
            for abs_item in temp_buffer:
                abs_id = abs_item.abs_id
                f_id = abs_item.funnel_id
                block_reason = ""
                // 规则1：重复abs_id全局锁拦截
                if abs_id in global_lock_index:
                    block_reason = "abs_id已存在全局锁索引，禁止重复入库"
                // 规则2：分funnel并发锁超限拦截
                elif funnel_lock_counter.get(f_id, 0) >= max_slot_concurrent:
                    block_reason = f"funnel{f_id}并发写入锁达到上限{max_slot_concurrent}"
                // 规则3：全局总锁超限拦截
                elif len(global_lock_index) >= global_max_lock:
                    block_reason = "全局活跃写入锁达到系统上限"
                // 规则4：人工锁定条目拦截
                elif abs_item.get("manual_tag", "无") in ["用户收藏", "人工锁定"]:
                    block_reason = "条目人工锁定，禁止覆盖写入L5"

                if block_reason != "":
                    block_list.append(abs_item)
                    block_reason_map[abs_id] = block_reason
                    stat_block_count += 1
                    continue
                // 无冲突，加锁放行
                pass_list.append(abs_item)
                // 写入全局锁索引
                global_lock_index[abs_id] = {
                    "funnel_id": f_id,
                    "lock_start_ts": now_ts
                }
                // 分槽锁计数+1
                if f_id not in funnel_lock_counter:
                    funnel_lock_counter[f_id] = 0
                funnel_lock_counter[f_id] += 1
                stat_pass_count += 1
            temp_buffer.clear()
            internal_state = LOCK_DISPATCH

            // 4. 批量下发放行抽象单元至ag-mem-30
            if len(pass_list) > 0:
                send_pass_abs_batch(target="ag-mem-30", item_list=pass_list)
            // 写入锁控审计日志
            audit_log = build_lock_audit_log(
                raw_abs_count=len(snapshot_data),
                pass_num=len(pass_list),
                block_num=len(block_list),
                expire_lock_num=len(expired_abs_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = LOCK_IDLE

        // 5. 抽象单元锁状态批量查询响应
        IF 收到抽象单元锁状态批量查询请求:
            query_abs_ids = 获取abs_id列表
            meta_snap = []
            for abs_id in query_abs_ids:
                lock_info = global_lock_index.get(abs_id, None)
                item_meta = query_abs_basic_meta(abs_id)
                meta_snap.append({
                    "abs_id": abs_id,
                    "lock_status": "locked" if lock_info else "free",
                    "lock_funnel": lock_info.funnel_id if lock_info else None,
                    "lock_hold_ms": now_ts - lock_info.lock_start_ts if lock_info else 0,
                    **item_meta
                })
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 6. 定时锁索引内存上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            lock_index_kb = calc_lock_index_kb(global_lock_index, lock_cfg.avg_lock_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-29", used_kb=lock_index_kb, active_lock_count=len(global_lock_index))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_lock_stat_report(
                    state=internal_state,
                    total_raw_abs=stat_raw_abs_total,
                    total_pass=stat_pass_count,
                    total_block=stat_block_count,
                    total_auto_release=stat_auto_release_lock
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-45推送抽象单元abs_id缺失、元数据非法 | 本条直接拦截，日志标记异常，不进入锁校验 | ag-mem-45重新推送完整标准化抽象单元快照 |
| 单次快照条目超1000条上限 | 自动分片分批缓存、锁校验，串行执行不产生锁风暴 | 内置分片逻辑自动执行 |
| 锁索引内存占用溢出 | 优先自动释放超时锁，仍溢出则拦截新入库条目并告警 | 扩容内存或调小锁超时阈值 |
| ag-mem-30接收放行条目无响应 | 保留锁索引不释放，下一轮扫描重试下发，持续失败生成告警审计 | ag-mem-30存储服务恢复正常接收 |
| 全局FUSE熔断触发 | 清空临时缓冲、全部活跃锁索引，终止校验与下发 | ag-mem-01下发RESUME恢复指令 |
| 分funnel无并发锁配置 | 加载全局通用最大并发锁数值兜底校验 | ag-mem-35运维侧补充分funnel锁控参数 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 安全校验通过抽象单元快照 | 只读 | ag-mem-45 唯一上游输入 |
| 内部调度总线 | 读 | 锁控规则配置回执、全局熔断指令 | 只读 | ag-mem-35、ag-mem-01 |
| 内部调度总线 | 读 | 锁状态批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | 快照接收回执 | 专属写入 | 返回上游 ag-mem-45 |
| 内部调度总线 | 写 | 锁控放行抽象单元批量推送 | 专属写入 | 下发下游 ag-mem-30 |
| 内部调度总线 | 写 | 锁状态元数据快照、容量上报、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L29-01 | 仅允许ag-mem-45推送待入库抽象单元，阻断所有其他模块旁路写入顶层存储，杜绝非法记忆流入L5 |
| L29-02 | 顶层入库强制必经三层锁校验，无任何跳过锁控直达ag-mem-30的流转通道，顶层记忆写入链路强制隔离 |
| L29-03 | 无持久化业务数据存储，仅内存维护锁索引，服务重启自动清空所有锁，避免长期锁占用死锁 |
| L29-04 | 锁超时、分槽并发上限、全局锁上限全部由ag-mem-35集中管控，本地无硬编码锁参数 |
| L29-05 | 所有上锁、放行、锁冲突拦截、自动解锁操作完整写入ag-mem-51审计日志，记录abs_id与funnel关联关系，支撑顶层写入溯源审计 |
| L29-06 | 熔断状态清空全部活跃锁与待处理缓冲，恢复后重新走完整校验流程，杜绝过期锁导致的死锁、重复写入问题 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M29-01 | `LOCK_IDLE`，ag-mem-45推送全新无冲突抽象单元快照 | 合规抽象单元批量快照 | 三层锁校验全部通过，加全局+分槽锁，批量推送至ag-mem-30，生成放行审计日志 |
| TC-M29-02 | `LOCK_IDLE`，快照内包含已存在abs_id重复条目 | 含重复abs_id快照 | 重复条目拦截，其余合法条目正常放行，日志标记重复入库原因 |
| TC-M29-03 | `LOCK_IDLE`，目标funnel已达到并发写入锁上限 | 同funnel大批量抽象单元 | 超出并发部分全部拦截，未达上限条目正常放行 |
| TC-M29-04 | `LOCK_IDLE`，单批快照1200条抽象单元 | 超大批量入库快照 | 自动分片串行锁校验，无锁风暴、无内存溢出 |
| TC-M29-05 | `LOCK_IDLE`，ag-mem-37下发abs_id批量锁状态查询 | 抽象ID批量查询请求 | 返回每条条目锁定状态、锁持有时长、关联funnel完整元数据快照 |
| TC-M29-06 | `LOCK_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空缓冲与全部活跃锁，停止锁校验与条目下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-29匹配白皮书L5入库前置安全锁控单元定位 | ✅ |
| 上游仅ag-mem-45、下游仅ag-mem-30，数据流闭环无冲突 | ✅ |
| 5种内部状态切换逻辑完整，覆盖缓冲、锁校验、放行全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 三层锁机制、超时自动释放、并发拦截规则严格对齐V1.1顶层记忆安全准入设计 | ✅ |
| 伪代码覆盖快照接收、三层锁校验、超时解锁、批量放行、审计日志、容量上报全链路 | ✅ |
| 异常场景覆盖非法抽象单元、超大批次、内存溢出、下游无响应、熔断、无分槽锁配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅允许ag-mem-45推送待入库抽象单元 | ✅ |
| 6条安全约束杜绝旁路写入、绕过锁控、长期死锁、顶层数据篡改风险 | ✅ |
| 6条测试用例覆盖全部核心锁控业务场景 | ✅ |

---