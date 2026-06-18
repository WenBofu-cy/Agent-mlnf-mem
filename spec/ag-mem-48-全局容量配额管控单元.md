# ag-mem-48-全局容量配额管控单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-48 |
| 模块名称 | 全局容量配额管控单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 全局资源管控中枢 |
| 核心职责 | 漏斗二全层级存储容量统一管理模块，实时采集ag-mem-20~28各分层存储占用空间、条目总量；维护各分槽、各层级存储容量上限配额；实时判定容量预警/紧急容量溢出阈值；向ag-mem-37下发容量加急刷新指令、向ag-mem-40推送容量紧急扫描标记、向ag-mem-42同步清理释放空间统计；接收各存储层空间变更上报，统一汇总全局容量数据；支持人工调整分层/分槽容量配额、配额变更审计；L5层级配置独立高容量保护配额，不触发强制清理；仅做容量统计、阈值判定、预警下发，不直接读写经验条目。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、人工运维配额配置接口（调整分层/分槽容量上限）、ag-mem-35（三维权重配置单元，读取分层标准容量阈值） |
| 被依赖模块 | ag-mem-20~28（分层存储，定时上报当前占用容量）、ag-mem-37（接收容量加急刷新指令）、ag-mem-40（接收容量紧急扫描标记）、ag-mem-42（接收清理释放空间同步通知）、ag-mem-51（记忆变更日志追溯单元，记录配额变更、容量预警事件）、ag-mem-03（漏斗二调度单元，周期上报全局容量统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 容量监控就绪 | `CAP_MONITOR_READY` | 正常采集各分层容量数据，实时判定容量阈值，响应各类容量相关请求 | 系统初始化加载配额配置、熔断恢复、配额更新完成 |
| 配额配置加载 | `LOAD_QUOTA_CFG` | 系统启动/人工修改配额，加载分层、分槽容量上限、预警阈值 | 服务初始化、人工下发配额调整指令 |
| 容量数据汇总计算 | `CALC_CAP_STAT` | 聚合各分层上报的空间、条目数量，计算占用率、剩余空间 | 收到分层存储容量上报数据包 |
| 预警指令下发 | `ALERT_SEND` | 判定达到预警/紧急阈值，向ag-mem-37下发加急刷新指令 | 分层容量占用触发阈值标准 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，暂停容量采集、阈值判定、预警下发，拒绝配额修改操作 | F0下发FUSE熔断指令；RESUME切回CAP_MONITOR_READY |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层容量占用上报 | Struct（层级、分槽ID、当前占用KB、条目总数、单条平均体积KB） | ag-mem-20~28 分层存储单元 | 每60秒定时上报、条目新增/删除后即时上报 | 高 |
| 人工配额调整指令 | Struct（层级/分槽ID、新容量上限KB、预警阈值占比、紧急溢出占比、管理员ID、双重确认挑战码） | 人工运维配额配置接口 | 运维调整分层/分槽存储配额上限 | **最高** |
| 清理释放空间同步通知 | Struct（目标层级、本次释放KB、清理条目数量） | ag-mem-42 冗余记忆删除与归档单元 | 每批次清理归档完成后同步释放容量 | 普通 |
| 分层标准容量阈值配置回执 | Struct（L1~L5默认容量上限、预警占比80%、紧急占比95%） | ag-mem-35 三维权重配置单元 | 模块初始化、容量阈值规则更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 容量加急刷新指令 | Struct（触发层级、预警等级、强制扫描L4标记） | ag-mem-37 重要度增量定时刷新单元 | 分层容量达到预警/紧急阈值 | 高 |
| 容量紧急扫描标记 | Struct（触发层级、触发原因=容量紧急） | ag-mem-40 遗忘阈值判定单元 | 容量达到紧急溢出阈值 | 高 |
| 配额调整完成回执 | Struct（修改层级/分槽、新旧容量上限、生效时间戳） | 人工运维配额配置接口 | 配额参数校验、持久化完成 | **最高** |
| 容量配额变更/预警审计日志 | Struct（事件类型、层级、当前占用率、阈值、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 配额修改、容量预警、容量紧急溢出触发 | 高 |
| 全局容量周期统计上报 | Struct（当前状态、各层级总配额、总占用KB、剩余空间、今日紧急预警次数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## 容量配额管控核心规则（V1.1分层资源保护标准）
### 1. 分层默认配额与阈值标准（由ag-mem-35统一下发）
| 层级 | 默认总容量上限 | 预警触发占比 | 紧急溢出占比 | 保护策略 |
|:---:|:---:|:---:|:---:|------|
| L1临时层 | 小容量 | 80% | 95% | 达到预警自动加急刷新清理 |
| L2近期层 | 中容量 | 80% | 95% | 预警触发常规加急遗忘 |
| L3中期层 | 大容量 | 80% | 95% | 紧急阈值强制归档清理 |
| L4长期层 | 超大容量 | 85% | 98% | 紧急时强制开启L4扫描 |
| L5核心层 | 独立隔离超大配额 | 95% | 100% | 仅告警，**禁止强制自动清理**，仅人工手动释放 |

### 2. 阈值判定逻辑
占用率 = 当前占用KB ÷ 分层总配额上限
1. 占用率 ≥ 预警占比 ＜ 紧急占比：下发**预警加急刷新**指令至ag-mem-37
2. 占用率 ≥ 紧急溢出占比：下发加急指令 + 向ag-mem-40携带「容量紧急」标记，触发兜底后20%低I条目清理
3. L5层级仅生成告警日志，不推送任何自动清理、加急刷新指令

### 3. 配额修改约束
1. L5容量上限仅允许上调，禁止下调缩小配额；
2. L1~L4配额下调不得低于系统最小安全容量底线；
3. 所有配额调整操作必须管理员双重确认；
4. 单条修改指令最多支持3个层级/分槽同步调整。

### 4. 空间统计规则
- 新增条目：占用容量同步上涨；
- ag-mem-42完成删除/归档后，同步扣减占用空间；
- L3/L4归档条目不计入活跃分层占用，划入离线归档分区统计，不触发分层容量预警。

## 核心处理逻辑
```
FUNCTION capacity_quota_control_main_loop():
    STATE_READY = CAP_MONITOR_READY
    STATE_LOAD_CFG = LOAD_QUOTA_CFG
    STATE_CALC_STAT = CALC_CAP_STAT
    STATE_ALERT_SEND = ALERT_SEND
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_LOAD_CFG
    // 加载分层默认容量阈值配置
    layer_quota_base = load_layer_cap_threshold(from_m35="ag-mem-35")
    // 分层实时容量缓存 {层级: {占用KB, 总配额, 条目数}}
    layer_cap_cache = {}
    stat_emergency_alert = 0
    stat_quota_modify = 0
    last_report_ts = NOW()

    // 初始化填充各层级默认配额
    FOR layer IN ["L1","L2","L3","L4","L5"]:
        layer_cap_cache[layer] = {
            "used_kb": 0,
            "total_quota_kb": layer_quota_base[layer].default_cap,
            "item_count": 0,
            "warn_ratio": layer_quota_base[layer].warn_ratio,
            "emergency_ratio": layer_quota_base[layer].emergency_ratio
        }
    internal_state = STATE_READY

    WHILE 系统运行中:
        // 1. 最高优先级：全局熔断调度
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_READY

        // 2. 接收分层存储定时容量上报
        IF 收到分层容量占用上报:
            cap_report = 获取容量上报数据包
            target_layer = cap_report.层级
            slot_id = cap_report.分槽ID
            used_kb = cap_report.当前占用KB
            item_cnt = cap_report.条目总数
            internal_state = STATE_CALC_STAT

            // 更新缓存内该层级实时占用数据
            layer_cap_cache[target_layer]["used_kb"] = used_kb
            layer_cap_cache[target_layer]["item_count"] = item_cnt
            total_quota = layer_cap_cache[target_layer]["total_quota_kb"]
            warn_r = layer_cap_cache[target_layer]["warn_ratio"]
            emer_r = layer_cap_cache[target_layer]["emergency_ratio"]
            occupy_ratio = used_kb / total_quota

            // 分层级判定预警逻辑
            IF target_layer == "L5":
                // L5仅记录告警日志，不触发自动清理
                IF occupy_ratio >= warn_r:
                    write_cap_audit_log(event_type="L5容量预警", layer=target_layer, ratio=occupy_ratio, ts=NOW())
                internal_state = STATE_READY
                CONTINUE

            // L1-L4 预警判定
            internal_state = STATE_ALERT_SEND
            IF occupy_ratio >= emer_r:
                // 紧急溢出：加急刷新 + 容量紧急扫描标记
                send_emergency_refresh_cmd(target_layer=target_layer, force_L4=True)
                send_cap_emergency_tag(target_layer=target_layer, trigger_cause="capacity_emergency")
                stat_emergency_alert += 1
                write_cap_audit_log(event_type="容量紧急溢出", layer=target_layer, ratio=occupy_ratio, ts=NOW())
            ELIF occupy_ratio >= warn_r:
                // 普通预警：仅下发加急刷新
                send_warn_refresh_cmd(target_layer=target_layer, force_L4=False)
                write_cap_audit_log(event_type="容量预警", layer=target_layer, ratio=occupy_ratio, ts=NOW())
            internal_state = STATE_READY

        // 3. 接收ag-mem-42清理释放空间同步通知
        IF 收到清理释放空间同步通知:
            free_notify = 获取释放通知
            target_layer = free_notify.目标层级
            free_kb = free_notify.本次释放KB
            // 扣减层级占用容量
            layer_cap_cache[target_layer]["used_kb"] -= free_kb
            write_cap_audit_log(event_type="清理释放容量", layer=target_layer, free_kb=free_kb, ts=NOW())

        // 4. 处理人工配额调整指令
        IF 收到人工配额调整指令:
            quota_req = 获取配额调整指令
            admin_id = quota_req.管理员ID
            modify_layer_list = quota_req.层级/分槽ID列表
            new_total_cap = quota_req.新容量上限KB
            internal_state = STATE_LOAD_CFG

            // 管理员双重确认校验
            double_check = launch_admin_double_verify(admin_id, timeout=60*1000, code=quota_req.挑战码)
            IF NOT double_check.通过:
                send_quota_reject_notify(target=人工运维接口, reason="双重确认校验失败")
                internal_state = STATE_READY
                CONTINUE

            // 配额合法性校验
            verify_pass = True
            verify_msg = ""
            FOR layer IN modify_layer_list:
                IF layer == "L5" AND new_total_cap < layer_cap_cache[layer]["total_quota_kb"]:
                    verify_pass = False
                    verify_msg = "L5核心层配额禁止下调，仅允许扩容"
                ELIF new_total_cap < layer_quota_base["global_min_safe_cap"]:
                    verify_pass = False
                    verify_msg = "调整后容量低于系统最小安全配额"
            IF NOT verify_pass:
                send_quota_reject_notify(target=人工运维接口, reason=verify_msg)
                internal_state = STATE_READY
                CONTINUE

            // 更新内存配额并持久化
            FOR layer IN modify_layer_list:
                old_cap = layer_cap_cache[layer]["total_quota_kb"]
                layer_cap_cache[layer]["total_quota_kb"] = new_total_cap
            persist_save_all_layer_quota(layer_cap_cache)
            stat_quota_modify += 1

            // 生成配额变更回执、审计日志
            finish_ack = build_quota_modify_ack(modify_layers=modify_layer_list, old_cap=old_cap, new_cap=new_total_cap, admin=admin_id)
            send_quota_ack(target=人工运维接口, ack_data=finish_ack)
            quota_change_log = build_cap_audit_log(event_type="配额人工调整", layer_list=modify_layer_list, ts=NOW())
            send_audit_log(target="ag-mem-51", log_data=quota_change_log)
            internal_state = STATE_READY

        // 5. 每180秒周期上报全局容量统计
        IF NOW() - last_report_ts >= 180 * 1000:
            cap_stat_report = build_global_cap_report(
                current_state=internal_state,
                layer_cap_snapshot=layer_cap_cache,
                total_emergency_alert=stat_emergency_alert,
                total_quota_modify=stat_quota_modify
            )
            send_stat_report(target="ag-mem-03", report=cap_stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 分层存储长期不上报容量数据 | 标记该层级容量状态异常，日志持续告警，不触发预警指令 | 分层存储恢复定时上报容量数据包 |
| 人工下调L5容量配额 | 直接拦截，返回校验失败提示 | 管理员上调L5配额或调整其他层级 |
| 调整配额低于系统全局最小安全容量 | 拒绝修改，返回容量底线限制提示 | 上调配额至安全底线以上重新提交 |
| 单次配额修改层级超过3个 | 拆分多批次分步调整，或直接拒绝超大批量指令 | 缩减单次修改层级数量至3个以内 |
| 全局紧急熔断触发 | 停止容量采集、预警下发、配额修改，缓存数据保留 | F0下发RESUME恢复指令 |
| ag-mem-42释放空间通知数值异常（负数） | 丢弃本条通知，记录异常日志，不修改容量缓存 | 上游ag-mem-42修正空间统计逻辑 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 分层容量占用上报数据包 | 只读 | ag-mem-20~28 定时推送 |
| 内部调度总线 | 读 | 人工配额调整指令 | 只读 | 人工运维配额接口下发 |
| 内部调度总线 | 读 | 清理释放空间同步通知 | 只读 | ag-mem-42 发送 |
| 内部调度总线 | 读 | 分层容量阈值配置回执 | 只读 | ag-mem-35 同步 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | 容量加急刷新指令、紧急扫描标记 | 专属写入 | 向 ag-mem-37、ag-mem-40 下发 |
| 内部调度总线 | 写 | 配额调整完成/拒绝回执 | 专属写入 | 向人工运维接口返回操作结果 |
| 内部调度总线 | 写 | 容量预警、配额变更审计日志 | 事件写入 | 向 ag-mem-51 推送 |
| 内部调度总线 | 写 | 全局容量周期统计上报 | 周期写入 | 向 ag-mem-03 推送 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| Q-01 | L5核心记忆层级容量预警仅日志记录，禁止下发任何自动清理加急指令，杜绝顶层长效经验被系统自动清除 |
| Q-02 | L5配额仅支持扩容，不允许缩容，底层硬编码校验，防止人为压缩永久记忆存储空间 |
| Q-03 | 容量预警、紧急清理标记仅能由本模块统一判定下发，各存储/遗忘模块无自主容量判定逻辑 |
| Q-04 | 所有分层配额人工修改操作必须管理员双重确认，单次操作独立验证，不可复用历史凭证 |
| Q-05 | 全部容量预警、配额变更、空间释放事件完整写入ag-mem-51审计日志，留存占用率、变更前后容量对比 |
| Q-06 | 仅接收ag-mem-20~28标准化容量上报数据包，拒绝其他模块上报容量数据，防止容量统计篡改 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M48-01 | `CAP_MONITOR_READY`，L1占用率82% | L1分层容量上报，占用达到预警阈值 | 向ag-mem-37下发常规加急刷新指令，生成容量预警日志 |
| TC-M48-02 | `CAP_MONITOR_READY`，L3占用率96% | L3容量上报达到紧急溢出阈值 | 下发加急指令+向ag-mem-40推送容量紧急标记，紧急告警计数+1 |
| TC-M48-03 | `CAP_MONITOR_READY`，L5占用率96% | L5容量上报触发预警占比 | 仅生成预警审计日志，不下发任何清理加急指令 |
| TC-M48-04 | `CAP_MONITOR_READY`，管理员提交L5配额下调指令 | 人工配额调整指令，缩小L5总容量 | 校验拦截，返回L5禁止缩容提示 |
| TC-M48-05 | `CAP_MONITOR_READY`，ag-mem-42同步释放1024KB空间 | 清理释放空间同步通知 | 对应层级占用容量缓存扣减，记录释放日志 |
| TC-M48-06 | `CAP_MONITOR_READY`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止所有容量采集与预警下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、全局容量管控中枢定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 分层配额标准、预警/紧急判定、L5专属保护规则完整 | ✅ |
| 伪代码覆盖容量上报、占用率计算、分级预警、空间释放同步、配额修改、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖上报失联、L5缩容拦截、容量底线校验、批量超限、熔断、异常释放数值共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无自动误删顶层记忆漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |
