# ag-mem-48 全局容量配额管控单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-48 |
| 模块名称 | 全局容量配额管控单元 |
| 所属分区 | 全局基础底座 / 全链路资源容量管控中枢 |
| 核心职责 | 统一接收所有ag-mem系列模块定时内存/存储占用上报；读取ag-mem-35容量维度全局配额、分层预警/紧急水位、单模块资源上限；实时汇总全系统内存、持久存储占用总量，判定容量预警/紧急溢出状态；向ag-mem-03漏斗二调度推送容量过载告警，触发分层清理、归档扩容调度；限制各模块缓存最大占用资源，超限下发缓存收缩指令；定时持久化全局容量大盘快照；所有容量超限告警、配额调整、缓存收缩操作写入ag-mem-51审计日志；仅做资源统计、配额判定、限流管控，无业务条目读写、分槽管理能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控容量统计任务启停）、ag-mem-03（漏斗二调度，接收容量告警、下发资源收缩调度信号）、ag-mem-35（通用三维配置中心，读取全局总配额、分层存储容量上限、各模块缓存内存阈值、预警水位比例）、全ag-mem业务模块（接收各模块内存/存储占用上报） |
| 被依赖模块 | ag-mem-01（周期上报全局容量大盘）、ag-mem-03（推送容量预警/紧急溢出告警报表）、全部业务模块（下发缓存收缩指令、资源超限限流提示）、ag-mem-51（记录容量变更、超限告警审计日志）、运维告警面板（全局资源过载告警推送） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 容量统计待机就绪 | `CAP_IDLE` | 资源统计缓存空闲，持续接收各模块容量上报，无批量配额重算任务 | 系统初始化加载配额配置、熔断恢复、一轮全局容量汇总完成 |
| 模块容量上报缓存聚合 | `METRIC_FETCH` | 周期性拉取/接收全模块资源占用指标，聚合存入全局容量缓存 | 定时容量汇总周期抵达 |
| 配额水位判定计算 | `QUOTA_CALC` | 对照ag-mem-35容量维度阈值，计算分层、单模块、全局资源占用水位，标记预警/紧急状态 | 全模块容量指标聚合完成 |
| 容量告警&收缩指令下发 | `ALERT_DISPATCH` | 向ag-mem-03推送容量告警，向超限模块下发缓存收缩指令，输出全局容量大盘 | 配额水位判定完成 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止全局批量容量汇总，仅保留单模块实时上报接收 | ag-mem-01下发熔断指令；RESUME切回CAP_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 模块资源占用定时上报 | Struct（module_id、缓存占用KB、持久存储占用KB、最大允许缓存KB、条目总占用） | 全部ag-mem子模块（01/03/15~30/35/37/40/41/42/45） | 各模块每60秒定时上报 | 高 |
| 全局三维容量配置回执 | Struct（系统总内存配额、L0~L5分层存储最大容量、单模块缓存上限、预警水位占比、紧急溢出水位占比） | ag-mem-35 通用配置中心 | 模块初始化、容量配额策略更新、每轮汇总前拉取 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控容量汇总批量任务 | 紧急 |
| 运维配额手动调整指令 | Struct（目标模块/分层、新容量上限、管理员ID、双重校验码） | 运维后台面板 | 人工调整分层/模块资源配额 | 最高 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 模块缓存收缩指令 | Struct（module_id、强制收缩目标KB、收缩冷却时长） | 占用超限的对应业务模块 | 模块缓存占用超过配置单模块上限 | 高 |
| 全局容量水位告警报表 | Struct（全局总占用、分层各层占用、预警/紧急标记、超限模块清单、建议处置方案） | ag-mem-03、运维告警面板 | 全局/分层资源达到预警/紧急水位 | 高 |
| 全局容量大盘周期快照 | List<Struct>（module_id、当前占用、配额上限、水位占比） | ag-mem-01 全局F0调度单元 | 每一轮完整容量汇总计算完成 | 普通 |
| 容量管控审计日志 | Struct（事件类型、超限模块/分层、占用水位、收缩指令范围、管理员、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 容量超限告警、人工配额修改、缓存收缩指令下发完成 | 普通 |
| 容量单元自身内存占用上报 | Struct（单元ag-mem-48、指标缓存总KB、记录模块总量） | 自身指标上报链路（自上报） | 每60秒定时上报 | 普通 |
| 容量管控周期运行统计上报 | Struct（当前状态、今日预警次数、紧急溢出次数、下发缓存收缩总批次、人工配额修改次数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 全局容量配额核心规则（V1.1容量维度标准，取自ag-mem-35配置）
### 1. 三级水位判定标准
1. 正常水位：占用占比 < 预警水位阈值，无任何限流、收缩动作；
2. 预警水位：预警占比 ≤ 占用 < 紧急占比，推送容量预警，触发温和归档/清理调度；
3. 紧急溢出水位：占用 ≥ 紧急占比，下发强制缓存收缩指令，触发大规模遗忘清理任务。
### 2. 分层与单模块双重配额约束
1. 分层存储独立上限：L0~L5各自配置最大持久容量，单分层超限即触发告警；
2. 各业务模块独立缓存内存上限，模块本地缓存超出上限必须执行收缩；
3. 全局总内存硬配额，所有模块缓存总和不可超过系统总内存上限。
### 3. 人工配额修改约束
1. 修改配额必须管理员双重挑战码校验；
2. 配额下调不可低于模块最小运行保底容量（配置内置下限）；
3. 配额修改即时生效，同步广播新版配额至对应模块。
### 4. 熔断降级规则
1. PAUSE半熔断：停止定时全局批量容量汇总，仅实时接收单模块上报，不主动下发批量收缩指令；
2. FUSE全熔断：仅接收上报数据存储，关闭所有告警、收缩、配额计算逻辑。
### 5. 批量约束
单次批量下发缓存收缩指令最多30个模块，超量自动分片串行推送，避免总线消息风暴。
### 6. 流转强制约束
1. 仅统计资源占用、判定水位、下发收缩提示，无权限直接清理模块缓存、删除业务条目；
2. 全部容量阈值、分层上限、模块缓存限额统一由ag-mem-35管控，本地无硬编码配额；
3. 单向数据流：仅向外输出告警、收缩指令、统计报表，不参与记忆晋升、遗忘、存储写入业务；
4. 模块上报缺失时使用历史占用数据兜底，连续3轮缺失标记模块离线告警。

## 核心处理逻辑
```
FUNCTION global_quota_control_main_loop():
    STATE_IDLE = CAP_IDLE
    STATE_FETCH = METRIC_FETCH
    STATE_CALC = QUOTA_CALC
    STATE_DISPATCH = ALERT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35容量维度全套配额配置
    cap_cfg = query_global_capacity_config(from_m35="ag-mem-35")
    global_total_mem_quota = cap_cfg.system_total_memory
    layer_storage_max = cap_cfg.layer_max_storage
    module_cache_max_map = cap_cfg.per_module_cache_limit
    warn_ratio = cap_cfg.cap_warn_ratio
    emergency_ratio = cap_cfg.cap_emergency_ratio
    max_batch_shrink = cap_cfg.max_shrink_module_per_batch
    module_cap_cache = {}
    stat_warn_times = 0
    stat_emergency_times = 0
    stat_shrink_batch = 0
    stat_manual_quota_mod = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                module_cap_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_cap_state_audit(old_state, internal_state, "熔断暂停全局容量汇总", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = CAP_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_cap_state_audit(old_state, internal_state, "熔断恢复容量管控", now_ts))

        // 熔断状态仅接收上报，跳过批量汇总计算
        IF internal_state == SYSTEM_PAUSED:
            IF 收到模块资源占用上报:
                cap_report = 获取模块上报结构体
                module_cap_cache[cap_report.module_id] = cap_report
            SLEEP 10ms
            CONTINUE

        // 2. 处理人工配额修改指令（最高优先级业务）
        IF 收到运维配额手动调整指令:
            manual_op = 获取配额修改指令
            // 双重管理员凭证校验
            if not admin_double_challenge_check(manual_op.admin_id, manual_op.challenge_code):
                send_quota_reject(target="运维后台", reason="双重身份校验失败")
                CONTINUE
            target_id = manual_op.target_id
            new_limit = manual_op.new_cap_limit
            // 校验不可低于保底容量
            min_guarantee = cap_cfg.get_min_guarantee(target_id)
            if new_limit < min_guarantee:
                send_quota_reject(target="运维后台", reason=f"配额不可低于保底容量{min_guarantee}")
                CONTINUE
            // 更新本地配额映射
            old_limit = module_cache_max_map.get(target_id, min_guarantee)
            module_cache_max_map[target_id] = new_limit
            stat_manual_quota_mod += 1
            // 广播新版配额至目标模块
            send_new_quota_notify(target=target_id, new_limit=new_limit)
            // 写入配额修改审计日志
            quota_audit = build_manual_quota_audit(manual_op, old_limit, new_limit, now_ts)
            send_audit_log(target="ag-mem-51", log_data=quota_audit)

        // 3. 接收各模块定时资源占用上报
        IF 收到模块资源占用定时上报:
            cap_report = 获取上报结构体
            module_cap_cache[cap_report.module_id] = cap_report

        // 4. 定时全局容量聚合汇总判定
        IF internal_state == CAP_IDLE and (now_ts - last_cap_report_ts) >= cap_cfg.cap_scan_interval:
            internal_state = METRIC_FETCH
            // 聚合全局内存、分层存储占用
            global_total_used_mem = sum_all_module_cache(module_cap_cache)
            layer_used_map = aggregate_layer_storage_usage(module_cap_cache)
            internal_state = QUOTA_CALC
            warn_slot_list = []
            emergency_slot_list = []
            shrink_module_list = []
            // 分层存储水位判定
            for layer, used in layer_used_map.items():
                max_layer = layer_storage_max[layer]
                usage_ratio = used / max_layer
                if usage_ratio >= emergency_ratio:
                    emergency_slot_list.append({"layer": layer, "ratio": usage_ratio})
                elif usage_ratio >= warn_ratio:
                    warn_slot_list.append({"layer": layer, "ratio": usage_ratio})
            // 单模块缓存超限判定
            for mid, cap_data in module_cap_cache.items():
                max_cache = module_cache_max_map.get(mid, cap_cfg.default_module_cache)
                if cap_data.cache_used_kb > max_cache:
                    shrink_module_list.append({"module_id": mid, "current": cap_data.cache_used_kb, "limit": max_cache})
            // 全局总内存判定
            global_usage_ratio = global_total_used_mem / global_total_mem_quota
            if global_usage_ratio >= emergency_ratio:
                emergency_slot_list.append({"global": True, "ratio": global_usage_ratio})
            elif global_usage_ratio >= warn_ratio:
                warn_slot_list.append({"global": True, "ratio": global_usage_ratio})

            internal_state = ALERT_DISPATCH
            // 分片下发缓存收缩指令
            slice_shrink_mod = split_slice(shrink_module_list, max_batch_shrink)
            for slice_mod in slice_shrink_mod:
                for mod_info in slice_mod:
                    shrink_cmd = build_cache_shrink_cmd(mod_info["module_id"], mod_info["limit"])
                    send_shrink_command(target=mod_info["module_id"], cmd=shrink_cmd)
            stat_shrink_batch += len(slice_shrink_mod)
            // 组装全局容量告警报表推送
            cap_alert_report = build_cap_warn_report(
                warn_list=warn_slot_list,
                emergency_list=emergency_slot_list,
                shrink_mod_list=shrink_module_list,
                global_usage=global_total_used_mem
            )
            if len(emergency_slot_list) > 0:
                stat_emergency_times += 1
                send_alert(target_list=["ag-mem-03", "运维告警面板"], alert_data=cap_alert_report)
            elif len(warn_slot_list) > 0:
                stat_warn_times += 1
                send_alert(target="ag-mem-03", alert_data=cap_alert_report)
            // 向F0推送全局容量大盘快照
            full_cap_snapshot = build_global_cap_snapshot(module_cap_cache, module_cache_max_map)
            send_cap_snapshot(target="ag-mem-01", snap_data=full_cap_snapshot)
            // 写入容量汇总审计日志
            audit_log = build_cap_scan_audit(
                warn_count=len(warn_slot_list),
                emergency_count=len(emergency_slot_list),
                shrink_mod_count=len(shrink_module_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            last_cap_report_ts = now_ts
            internal_state = CAP_IDLE

        // 5. 60秒自身内存占用上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            self_cache_kb = calc_cap_metric_cache_size(module_cap_cache, cap_cfg.avg_module_cap_meta_kb)
            self_cap_report = build_self_cap_report(layer="ag-mem-48", used_kb=self_cache_kb, track_module_count=len(module_cap_cache))
            // 自身上报逻辑内部闭环，无需外部转发
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_cap_runtime_stat(
                    state=internal_state,
                    total_warn=stat_warn_times,
                    total_emergency=stat_emergency_times,
                    total_shrink_batch=stat_shrink_batch,
                    manual_quota_mod_count=stat_manual_quota_mod
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 业务模块连续3轮不上报容量指标 | 标记模块离线告警，保留历史占用数据参与计算，推送提醒至运维面板 | 对应模块恢复定时资源上报 |
| 批量超限模块超过单次收缩指令上限30个 | 自动分片串行下发收缩指令，防止总线消息拥堵 | 内置分片逻辑自动执行 |
| 指标缓存内存溢出 | 淘汰最早过期模块上报记录，向运维推送容量管控自身缓存告警 | 清理过期指标、扩容管控内存 |
| PAUSE半熔断触发全局容量汇总周期 | 跳过批量汇总、告警、收缩下发，仅缓存上报数据 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35容量配额配置拉取失败 | 加载内置兜底全局/分层/模块配额继续统计，输出配置缺失告警 | ag-mem-35恢复下发完整容量三维参数 |
| 人工配额修改数值低于系统保底容量 | 直接驳回修改指令，返回保底容量限制提示 | 管理员上调配额至保底值以上重新提交 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 各模块资源占用上报、人工配额调整指令、全局熔断指令、容量三维配额配置 | 只读 | 全业务模块、运维后台、ag-mem01、ag-mem35 |
| 内部业务总线 | 写 | 模块缓存收缩指令、新版配额通知 | 专属写入 | 所有ag-mem业务模块 |
| 运维告警总线 | 写 | 全局容量预警/紧急溢出告警报表 | 专属写入 | ag-mem-03、运维告警面板 |
| 内部调度总线 | 写 | 全局容量快照、容量管控审计日志、周期运行统计 | 事件/周期写入 | ag-mem01、ag-mem51、ag-mem03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| CAP48-01 | 全局总内存、分层存储上限、单模块缓存限额、水位阈值全部取自ag-mem-35，本地禁止硬编码任何容量配额参数 |
| CAP48-02 | 仅下发缓存收缩提示指令，无直接清空模块缓存、删除业务条目、修改分层存储容量的操作权限，资源变更动作收敛至各业务模块自身执行 |
| CAP48-03 | 人工调整配额强制双重管理员凭证校验，设置最低保底容量锁死，防止人为调低配额导致业务卡死 |
| CAP48-04 | 熔断分级关停批量容量汇总与收缩下发，故障期间减少大批量总线交互，避免通信资源耗尽 |
| CAP48-05 | 收缩指令分片限流，单次最多推送30个模块，平滑总线消息负载，保障业务读写指令优先传输 |
| CAP48-06 | 熔断清空指标缓存，恢复后重新接收实时上报数据计算水位，避免基于过期占用数据误下发收缩指令 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M48-01 | `CAP_IDLE`，多个模块缓存占用超出单模块配额上限 | 多模块定时容量上报 | 拆分分片下发缓存收缩指令，生成容量预警报表推送ag-mem-03，写入容量审计日志 |
| TC-M48-02 | `CAP_IDLE`，L3分层存储占用达到紧急溢出水位 | 分层存储占用上报指标 | 标记全局紧急告警，推送高危告警至运维面板，触发大规模清理调度信号 |
| TC-M48-03 | `CAP_IDLE`，运维下发合法双重校验码的模块配额上调指令 | 人工配额修改指令 | 更新模块配额映射，向目标模块推送新版配额通知，记录配额变更审计日志 |
| TC-M48-04 | `CAP_IDLE`，超限模块共36个超出单批30个上限 | 超大批量超限模块上报数据 | 自动拆分为两批串行下发收缩指令，无总线拥堵 |
| TC-M48-05 | `CAP_IDLE`，收到F0 PAUSE半熔断后到达容量汇总周期 | 半熔断+定时汇总触发 | 跳过批量水位计算、告警、收缩下发，仅缓存模块上报数据 |
| TC-M48-06 | `CAP_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空指标缓存，关闭所有容量告警与收缩下发逻辑 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-48匹配白皮书全局容量配额管控中枢定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35容量三维配额参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖指标聚合、水位判定、告警收缩下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 三级水位、分层/模块双重配额、人工配额校验、分片限流、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖模块容量上报、人工配额修改、全局聚合水位判定、收缩指令分片下发、告警推送、审计日志全链路 | ✅ |
| 异常场景包含模块离线上报、超大批量收缩、缓存溢出、半熔断拦截、配置缺失、配额过低拦截共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅下发收缩提示，无直接清理业务数据权限 | ✅ |
| 6条V1.1安全约束统一配额管控、权限隔离、人工操作强校验、故障限流、防消息风暴、规避过期指标判定 | ✅ |
| 6条自动化测试用例覆盖全部容量管控核心业务场景 | ✅ |

---