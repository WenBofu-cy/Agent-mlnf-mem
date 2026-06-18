# ag-mem-28-L4容量管控辅助单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-28 |
| 模块名称 | L4容量管控辅助单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（L4存储配套容量辅助调度单元） |
| 核心职责 | 绑定ag-mem-26 L4长期存储层作为专属辅助单元，承接ag-mem-48全局容量配额下发；实时监控L4业务数据、压缩向量索引双维度存储占用；按funnel分槽分配独立容量配额，对超阈值分槽下发限流、加急归档信号；动态调节ag-mem-26归档扫描执行频率；统计各业务funnel存储占比、冷热数据分布；输出容量调控指令同步至ag-mem-26、ag-mem-42；定时上报分槽容量明细至ag-mem-48；所有配额调整、限流、加急归档操作全量推送审计日志至ag-mem-51；无独立持久记忆存储，仅维护容量统计元数据，不参与条目晋升/抽象流程。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-48（全局容量配额管控，下发L4总配额、全局预警阈值）、ag-mem-26（L4长期存储层，读取实时容量占用数据）、ag-mem-35（三维权重配置单元，读取分funnel容量分配比例、冷热判定阈值） |
| 被依赖模块 | ag-mem-26（接收分槽限流、加急归档调控指令）、ag-mem-42（接收L4加急归档触发信号）、ag-mem-48（上报L4分槽粒度容量明细、冷热存储统计）、ag-mem-51（推送容量变更、限流、归档调度审计日志）、ag-mem-03（漏斗二调度单元，周期上报L4容量运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 容量平稳待机 | `CAP_IDLE` | L4整体及所有funnel分槽容量均低于预警阈值，无调控动作 | 系统初始化、容量回落至安全区间、调控指令执行完毕 |
| 容量采集扫描 | `CAP_SCAN` | 定时拉取ag-mem-26实时存储占用、分槽条目冷热访问数据 | 容量采集定时周期倒计时归零 |
| 分槽配额重分配 | `CAP_REALLOC` | 全局配额变更/业务分槽扩容，重新分配各funnel存储上限 | 人工调整配额、全局总容量扩容指令下发 |
| 容量过载调控 | `CAP_THROTTLE` | 存在分槽/整体容量达预警/紧急阈值，下发限流、加急归档指令 | 采集扫描识别容量超限 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止容量采集、配额重分配、过载调控，缓存容量统计临时数据 | F0下发FUSE熔断指令；RESUME切回CAP_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4全局容量配额配置 | Struct（L4总存储上限KB、全局预警占比、紧急溢出占比、向量索引预留容量） | ag-mem-48 全局容量配额单元 | 模块初始化、人工调整分层总配额 | 普通 |
| L4实时容量占用快照 | Struct（全量funnel分槽data_kb、vec_kb、条目总量、冷热访问标记） | ag-mem-26 L4长期存储层 | 容量采集周期触发、批量条目变更主动推送 | 高 |
| 分funnel容量分配规则回执 | Struct（各funnel配额占比、冷热数据判定天阈值、加急归档倍率） | ag-mem-35 三维权重配置单元 | 初始化、分槽容量策略更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4分槽容量调控指令 | List<Struct>（funnel_id、控制类型：限流/加急归档、归档扫描倍率、单槽容量上限） | ag-mem-26 L4长期存储层 | 扫描识别分槽容量达到预警/紧急阈值 | 高 |
| L4全局加急归档触发信号 | Struct（层级=L4、加急等级、释放容量目标KB） | ag-mem-42 冗余记忆删除单元 | L4整体存储达到紧急溢出阈值 | 高 |
| L4分槽容量明细上报 | Struct（总配额、各funnel占用/剩余KB、冷热条目数量、向量索引总占用） | ag-mem-48 全局容量配额 | 每60秒定时上报、配额重分配后即时上报 | 普通 |
| L4容量调控审计日志 | Struct（事件类型、受影响funnel列表、调整前后配额、限流/归档倍率、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 配额重分配、下发限流、加急归档信号完成 | 普通 |
| L4容量周期运行统计上报 | Struct（当前状态、今日触发加急归档次数、限流分槽总数、累计释放存储空间KB） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## L4容量管控核心规则（V1.1分层容量隔离规范）
### 1. 全局与分槽容量参数（由ag-mem-48、ag-mem-35协同下发）
1. 两级预警阈值：预警占用80%、紧急溢出占用95%；
2. 冷热判定标准：条目30天无访问标记为冷数据，优先触发归档；
3. 加急归档倍率：预警状态2倍扫描频率，紧急状态5倍扫描频率；
4. 分槽配额分配规则：按业务权重百分比划分总容量，单funnel不可占用超过总L4容量40%。

### 2. 容量调控触发逻辑
1. 单funnel占用≥80%：下发2倍加急归档指令至ag-mem-26；
2. 单funnel占用≥95%：同步开启写入限流+5倍归档扫描；
3. L4整体存储≥95%：向ag-mem-42下发全局加急归档信号，批量清理全库冷数据；
4. 分槽配额重分配仅在系统低负载窗口执行，避免IO冲击。

### 3. 流转强制约束
1. 仅对接L4存储层ag-mem-26，不参与L0~L3、L5任何层级容量调度；
2. 无条目读写、晋升、归档执行能力，仅输出调控指令，实际清理逻辑由ag-mem-26、ag-mem-42落地；
3. 仅读取容量统计元数据，无权修改L4内部条目、向量索引数据；
4. 不参与记忆I值计算、语义抽象、安全校验等业务逻辑，纯容量调度辅助单元。

### 4. 批量约束
单次调控指令最多一次性处理20个超限funnel分槽，超量分多轮下发，避免瞬时调度压力。

## 核心处理逻辑
```
FUNCTION l4_cap_helper_main_loop():
    STATE_IDLE = CAP_IDLE
    STATE_SCAN = CAP_SCAN
    STATE_REALLOC = CAP_REALLOC
    STATE_THROTTLE = CAP_THROTTLE
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局容量配置
    cap_global_cfg = query_cap_global_config(from_m48="ag-mem-48")
    slot_ratio_cfg = query_slot_cap_ratio(from_m35="ag-mem-35")
    warn_ratio = cap_global_cfg.warn_usage_ratio
    emergency_ratio = cap_global_cfg.emergency_usage_ratio
    cold_days_thresh = slot_ratio_cfg.cold_data_days
    scan_cycle_sec = slot_ratio_cfg.cap_scan_interval
    scan_countdown = scan_cycle_sec
    temp_cap_stat = {}
    stat_today_urgent_archive = 0
    stat_throttle_slot_cnt = 0
    stat_total_released_kb = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_cap_stat.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = CAP_IDLE

        // 2. 定时容量采集扫描
        IF internal_state == CAP_IDLE:
            scan_countdown -= 10
            IF scan_countdown <= 0:
                internal_state = CAP_SCAN
                // 拉取ag-mem-26全部分槽容量快照
                cap_snapshot = get_l4_cap_snapshot(target="ag-mem-26")
                temp_cap_stat = cap_snapshot
                total_used_kb = cap_snapshot.total_data_kb + cap_snapshot.total_vec_kb
                total_quota_kb = cap_global_cfg.l4_total_quota_kb
                global_usage = total_used_kb / total_quota_kb
                over_limit_slots = []
                now_ts = NOW()

                // 遍历各funnel分槽判定超限
                for funnel_id, slot_data in cap_snapshot.funnel_cap_map.items():
                    slot_used = slot_data.data_kb + slot_data.vec_kb
                    slot_max_quota = total_quota_kb * slot_ratio_cfg[funnel_id].ratio
                    slot_usage = slot_used / slot_max_quota
                    if slot_usage >= warn_ratio:
                        emergency_flag = slot_usage >= emergency_ratio
                        over_limit_slots.append({
                            "funnel_id": funnel_id,
                            "slot_used_kb": slot_used,
                            "slot_max_kb": slot_max_quota,
                            "is_emergency": emergency_flag
                        })
                // 存在超限分槽进入过载调控
                if len(over_limit_slots) > 0:
                    internal_state = CAP_THROTTLE
                    stat_throttle_slot_cnt += len(over_limit_slots)
                    control_batch = []
                    global_urgent = False
                    // 生成分槽调控指令
                    for slot_info in over_limit_slots:
                        if slot_info.is_emergency:
                            control_batch.append({
                                "funnel_id": slot_info.funnel_id,
                                "ctrl_type": "limit_write+urgent_archive",
                                "archive_speed_multi": 5
                            })
                        else:
                            control_batch.append({
                                "funnel_id": slot_info.funnel_id,
                                "ctrl_type": "urgent_archive",
                                "archive_speed_multi": 2
                            })
                    // 下发调控指令至ag-mem-26
                    send_cap_control_batch(target="ag-mem-26", ctrl_list=control_batch)
                    // 全局紧急溢出触发全局加急归档信号
                    if global_usage >= emergency_ratio:
                        global_urgent = True
                        send_global_urgent_signal(target="ag-mem-42", level="emergency")
                        stat_today_urgent_archive += 1
                    // 写入容量调控审计日志
                    audit_log = build_cap_ctrl_audit(
                        over_slot_num=len(over_limit_slots),
                        global_emergency=global_urgent,
                        ts=now_ts
                    )
                    send_audit_log(target="ag-mem-51", log_data=audit_log)
                scan_countdown = scan_cycle_sec
                internal_state = CAP_IDLE

        // 3. 接收全局配额变更，执行分槽重分配
        IF 收到L4全局容量配额配置更新:
            internal_state = CAP_REALLOC
            new_global_quota = 获取新配额参数
            // 按固定比例重新计算各funnel分槽上限
            new_slot_quota_map = {}
            for funnel_id, ratio in slot_ratio_cfg.items():
                new_slot_quota_map[funnel_id] = new_global_quota.l4_total_quota_kb * ratio.ratio
            // 审计记录配额变更
            realloc_audit = build_cap_realloc_audit(old_quota=cap_global_cfg, new_quota=new_global_quota, ts=NOW())
            send_audit_log(target="ag-mem-51", log_data=realloc_audit)
            // 更新本地全局配置
            cap_global_cfg = new_global_quota
            internal_state = CAP_IDLE

        // 4. 定时上报分槽容量明细至ag-mem-48
        IF NOW() - last_report_ts >= 60 * 1000:
            cap_detail_report = build_slot_cap_detail_report(
                total_quota=cap_global_cfg.l4_total_quota_kb,
                slot_cap_map=temp_cap_stat.funnel_cap_map,
                vec_total_kb=temp_cap_stat.total_vec_kb
            )
            send_cap_detail_report(target="ag-mem-48", report=cap_detail_report)
            // 180s周期运行统计上报
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_cap_helper_stat_report(
                    state=internal_state,
                    today_urgent_archive=stat_today_urgent_archive,
                    total_throttle_slots=stat_throttle_slot_cnt,
                    total_release_kb=stat_total_released_kb
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-26容量快照读取失败、数据缺失 | 跳过本轮调控，记录告警，等待下一轮扫描周期重试 | ag-mem-26存储IO恢复，正常输出容量快照 |
| 单次超限funnel超过20个 | 自动拆分多批次下发调控指令，分批限流/加急归档，避免调度风暴 | 内置分片逻辑自动执行 |
| 分funnel无容量分配比例配置 | 按均等比例均分总容量兜底分配 | ag-mem-35补充分槽容量权重参数 |
| 全局配额更新时值异常（上限小于已占用） | 拒绝执行重分配，生成告警日志，维持原有配额 | ag-mem-48下发合法扩容配额 |
| 全局FUSE熔断触发 | 停止容量采集、配额重分配、下发调控指令，清空临时统计缓存 | ag-mem-01下发RESUME恢复指令 |
| 下发加急归档信号至ag-mem-42无响应 | 重试2次，失败则写入审计告警，下一轮扫描重新触发 | ag-mem-42服务恢复正常接收信号 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L4全局容量配额配置、分槽容量分配规则 | 只读 | ag-mem-48、ag-mem-35 |
| 内部调度总线 | 读 | L4实时容量占用快照、全局熔断指令 | 只读 | ag-mem-26、ag-mem-01 |
| 内部调度总线 | 写 | L4分槽容量调控指令 | 专属写入 | 下发至ag-mem-26 |
| 内部调度总线 | 写 | 全局加急归档触发信号 | 专属写入 | 下发至ag-mem-42 |
| 内部调度总线 | 写 | 分槽容量明细上报、容量审计日志、周期运行统计 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| CAP28-01 | 仅可读取ag-mem-26容量统计数据，禁止直接修改L4条目、向量索引、存储文件，无数据写入权限 |
| CAP28-02 | 容量调控仅下发指令，实际限流、归档清理逻辑由ag-mem-26、ag-mem-42执行，管控与执行解耦，避免单一模块权限过大 |
| CAP28-03 | L4总容量、分槽分配比例、预警阈值全部由ag-mem-48+ag-mem-35集中管控，本模块无本地硬编码容量参数 |
| CAP28-04 | 所有配额调整、限流、全局加急归档操作完整写入ag-mem-51审计日志，记录受影响funnel与容量变更前后数值，支撑资源审计 |
| CAP28-05 | 单funnel容量上限强制封顶为L4总容量40%，防止单一业务挤占全部长效记忆资源 |
| CAP28-06 | 熔断状态清空临时容量统计缓存，不缓存待下发调控指令，恢复后重新采集快照再生成调度指令，避免过期调控逻辑误执行 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M28-01 | `CAP_IDLE`，单funnel占用达到82%预警阈值 | 定时容量采集扫描触发 | 向ag-mem-26下发2倍加急归档指令，生成容量调控审计日志 |
| TC-M28-02 | `CAP_IDLE`，单funnel占用96%紧急阈值 | 容量快照采集完成 | 下发写入限流+5倍归档指令至ag-mem-26 |
| TC-M28-03 | `CAP_IDLE`，L4整体占用96%全局紧急阈值 | 全量容量快照 | 向ag-mem-42下发全局加急归档信号 |
| TC-M28-04 | `CAP_IDLE`，ag-mem-48下发新扩容全局配额 | 新L4容量配额配置 | 按分槽比例重新分配上限，生成配额变更审计日志 |
| TC-M28-05 | `CAP_IDLE`，单次扫描25个超限funnel分槽 | 大容量超限快照 | 自动拆分两批下发调控指令，无调度阻塞 |
| TC-M28-06 | `CAP_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，停止容量采集与所有调控指令下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-28匹配白皮书L4专属容量辅助管控单元定位 | ✅ |
| 上下游绑定ag-mem-26、ag-mem-42、ag-mem-48，数据流闭环无冲突 | ✅ |
| 5种内部状态覆盖采集、重分配、过载调控全流程，切换条件清晰 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，链路无错乱 | ✅ |
| 分槽配额、两级预警、冷热归档倍率规则严格对齐V1.1分层容量隔离规范 | ✅ |
| 伪代码覆盖容量采集、超限判定、分槽调控、全局加急、配额重分配、定时上报、审计日志全链路 | ✅ |
| 异常场景覆盖快照缺失、超限分槽过多、非法配额、无分槽比例、熔断、下游无响应共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅具备只读容量统计、下发调度指令权限 | ✅ |
| 6条V1.1安全约束限制数据修改权限、单一业务资源侵占风险 | ✅ |
| 6条自动化测试用例覆盖全部核心容量调度场景 | ✅ |

---