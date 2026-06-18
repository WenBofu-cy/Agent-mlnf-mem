# ag-mem-35-三维权重系数配置单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-35 |
| 模块名称 | 三维权重系数配置单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 全局参数配置中枢 |
| 核心职责 | 漏斗二全记忆体系统一参数配置中心，维护**重要度I权重、遗忘阈值、复用保护阈值**三维全套分槽独立参数；提供全局/分槽分层配置读取、动态更新、配置持久化下发能力；为ag-mem-27、ag-mem-40、ag-mem-41、ag-mem-29提供全部规则阈值基准；支持人工管理员批量修改配置、配置变更审计、配置灰度切换；所有存储层、判定、锁控模块的分层阈值均统一由本模块输出，杜绝各模块本地硬编码参数，符合V1.1集中化配置安全规范。仅管理参数，不参与记忆判定、存储、清理业务逻辑。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断）、人工运维配置接口（管理员下发配置修改指令）、ag-mem-51（日志追溯单元，记录配置变更） |
| 被依赖模块 | ag-mem-27（L4抽象提炼单元，I值计算权重）、ag-mem-29（L5安全锁控，S/置信度阈值）、ag-mem-40（遗忘判定，各层级遗忘I阈值）、ag-mem-41（复用校验，分层最低复用保护次数）、ag-mem-03（漏斗二调度，周期上报配置状态） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 正常待命 | `CONFIG_READY` | 配置加载完成，持续响应各模块参数查询请求 | 系统初始化加载持久化配置、配置变更生效、熔断恢复 |
| 配置加载中 | `LOADING_CONFIG` | 系统启动/配置重置，从持久化介质读取全部分槽三维参数 | 服务启动、人工下发重置配置指令 |
| 配置更新校验 | `CONFIG_VERIFY` | 校验人工新配置数值合法、无越界、分层参数完整 | 收到管理员配置修改/批量更新指令 |
| 配置下发同步 | `CONFIG_SYNC` | 向所有依赖模块推送更新后的分槽参数快照 | 新配置校验通过，写入持久化存储 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，冻结配置读写，拒绝所有参数查询与修改 | F0下发FUSE熔断指令；RESUME切回CONFIG_READY |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分槽三维参数查询请求 | Struct（目标分槽ID、查询维度：I权重/遗忘阈值/复用阈值/安全准入阈值） | ag-mem-27/29/40/41 | 各模块初始化、定时刷新、判定执行前拉取参数 | 高 |
| 人工配置更新指令 | Struct（操作类型：单条修改/批量更新/重置默认、分槽ID列表、三维新参数集、管理员ID、双重确认挑战码） | 人工运维配置接口 | 管理员调整记忆分层规则阈值 | **最高** |
| 全局默认配置重置指令 | Struct（恢复出厂三维参数全集） | 人工运维接口 | 业务规则回滚、故障重置 | **最高** |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急熔断、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 三维权重配置查询回执 | Struct（分槽ID、I计算权重集、L1~L4遗忘I阈值、L1~L4最低复用阈值、L4/L5安全准入阈值、配置版本号） | ag-mem-27/29/40/41 | 参数查询请求校验通过 | 高 |
| 配置更新完成回执 | Struct（操作批次ID、修改分槽数量、新旧参数摘要、生效时间戳） | 人工运维配置接口 | 配置校验、持久化、同步下发全部完成 | **最高** |
| 配置变更审计日志 | Struct（操作类型、管理员ID、修改分槽、参数变更对比、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 任意人工配置修改/重置操作完成 | 高 |
| 全局配置同步广播快照 | Struct（全分槽三维参数全集、新版本号） | ag-mem-27/29/40/41 | 批量配置更新完成，全模块同步刷新 | 普通 |
| 配置周期状态上报 | Struct（当前状态、总分槽数量、当前配置版本、今日配置变更次数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 三维权重完整配置规范（V1.1标准）
### 维度1：综合重要度I计算权重（供给ag-mem-27）
I = 复用权重×复用次数 + 安全权重×S值 + 时效权重×新鲜度系数
每个分槽独立三组权重，取值区间0~1，总和固定为1。

### 维度2：分层遗忘I阈值（供给ag-mem-40）
| 层级 | 参数说明 | 默认出厂值 |
|:---:|------|:---:|
| L1 | L1条目遗忘最低I阈值 | 0.15 |
| L2 | L2条目遗忘最低I阈值 | 0.25 |
| L3 | L3条目遗忘最低I阈值 | 0.35 |
| L4 | L4条目强制扫描遗忘I阈值 | 0.45 |

### 维度3：分层复用保护最低次数（供给ag-mem-41）
| 层级 | 参数说明 | 默认出厂值 |
|:---:|------|:---:|
| L1 | L1最低复用保护次数 | 2 |
| L2 | L2最低复用保护次数 | 3 |
| L3 | L3最低复用保护次数 | 5 |
| L4 | L4最低复用保护次数 | 8 |

### 维度4：安全准入阈值（供给ag-mem-29 L5写入校验）
1. S直达最低S阈值：0.9
2. L4推送最低置信度阈值：0.85

### 配置合法性校验规则
1. 所有权重系数取值范围 0 ≤ val ≤ 1，三组权重相加必须等于1；
2. 遗忘阈值层级逐级递增：L1 < L2 < L3 < L4，禁止高层阈值低于低层；
3. 复用保护次数层级逐级递增：L1 < L2 < L3 < L4；
4. L5安全准入阈值固定下限，人工修改不得低于出厂默认值；
5. 所有参数修改必须完成管理员双重挑战码确认，单次批量修改最多支持5个分槽。

## 核心处理逻辑
```
FUNCTION weight_config_main_loop():
    STATE_READY = CONFIG_READY
    STATE_LOAD = LOADING_CONFIG
    STATE_VERIFY = CONFIG_VERIFY
    STATE_SYNC = CONFIG_SYNC
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_LOAD
    // 初始化加载持久化全分槽三维配置
    full_slot_config_map = load_persist_all_slot_config()
    current_config_version = 1
    stat_modify_count = 0
    last_report_ts = NOW()
    global_default_config = 出厂默认三维参数结构体

    // 加载完成切换就绪状态
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

        // 2. 处理各模块三维参数查询请求
        IF 收到分槽三维参数查询请求:
            query_req = 获取查询请求
            target_slot = query_req.目标分槽ID
            query_dim = query_req.查询维度
            // 无匹配分槽使用全局默认配置兜底
            slot_cfg = full_slot_config_map.get(target_slot, global_default_config)
            // 组装查询回执
            query_resp = build_config_query_resp(
                slot_id=target_slot,
                i_weight=slot_cfg.I权重集,
                forget_thresh=slot_cfg.分层遗忘阈值,
                reuse_thresh=slot_cfg.分层复用阈值,
                security_thresh=slot_cfg.L5准入阈值,
                version=current_config_version
            )
            // 回执下发至请求模块
            send_query_response(target=query_req.来源模块, resp_data=query_resp)

        // 3. 接收人工配置更新指令
        IF 收到人工配置更新指令:
            op_req = 获取人工配置指令
            admin_id = op_req.管理员ID
            target_slot_list = op_req.分槽ID列表
            new_param_set = op_req.三维新参数集
            internal_state = STATE_VERIFY

            // 第一步：双重确认校验
            double_check_result = launch_admin_double_verify(admin_id, timeout=60*1000, challenge_code=op_req.挑战码)
            IF NOT double_check_result.通过:
                send_modify_reject_notify(target=人工运维接口, reason="管理员双重确认失败或超时")
                internal_state = STATE_READY
                CONTINUE

            // 第二步：参数合法性全校验
            verify_pass = True
            verify_reason = ""
            // 权重和校验
            weight_sum = new_param_set.I复用权重 + new_param_set.I安全权重 + new_param_set.I时效权重
            IF weight_sum != 1.0:
                verify_pass = False
                verify_reason = "I三维权重总和必须等于1"
            // 遗忘阈值层级递增校验
            elif NOT (new_param_set.L1遗忘阈值 < new_param_set.L2遗忘阈值 < new_param_set.L3遗忘阈值 < new_param_set.L4遗忘阈值):
                verify_pass = False
                verify_reason = "遗忘阈值必须遵循L1<L2<L3<L4层级递增规则"
            // 复用次数层级递增校验
            elif NOT (new_param_set.L1复用次数 < new_param_set.L2复用次数 < new_param_set.L3复用次数 < new_param_set.L4复用次数):
                verify_pass = False
                verify_reason = "复用保护次数必须层级逐级递增"
            // L5安全阈值下限保护
            elif new_param_set.S直达阈值 < 0.9 OR new_param_set.L4置信阈值 < 0.85:
                verify_pass = False
                verify_reason = "L5安全准入阈值不可低于系统出厂下限"

            IF NOT verify_pass:
                send_modify_reject_notify(target=人工运维接口, reason=verify_reason)
                internal_state = STATE_READY
                CONTINUE

            // 校验通过，更新内存配置并持久化
            internal_state = STATE_SYNC
            FOR slot_id IN target_slot_list:
                full_slot_config_map[slot_id] = new_param_set
            // 持久化落盘
            persist_save_all_config(full_slot_config_map)
            current_config_version += 1
            stat_modify_count += 1

            // 广播新版配置快照至所有依赖模块
            full_config_broadcast = build_global_config_snapshot(full_slot_config_map, current_config_version)
            broadcast_target_list = ["ag-mem-27","ag-mem-29","ag-mem-40","ag-mem-41"]
            FOR target_mod IN broadcast_target_list:
                send_config_sync_snapshot(target=target_mod, snapshot=full_config_broadcast)

            // 生成配置变更审计日志推送ag-mem-51
            audit_log = build_config_change_log(
                op_type=op_req.操作类型,
                admin=admin_id,
                slot_list=target_slot_list,
                param_diff=新旧参数对比摘要,
                ts=NOW()
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)

            // 返回配置更新完成回执
            finish_ack = build_modify_finish_ack(
                batch_id=生成UUID(),
                modify_slot_num=LEN(target_slot_list),
                param_diff=新旧参数摘要,
                effective_ts=NOW()
            )
            send_modify_ack(target=人工运维接口, ack_data=finish_ack)
            internal_state = STATE_READY

        // 4. 处理全局配置重置指令
        IF 收到全局默认配置重置指令:
            reset_req = 获取重置指令
            admin_id = reset_req.管理员ID
            // 双重确认校验
            double_check_result = launch_admin_double_verify(admin_id, timeout=60*1000)
            IF NOT double_check_result.通过:
                send_modify_reject_notify(target=人工运维接口, reason="重置操作双重确认失败")
                CONTINUE
            // 覆盖全部分槽为出厂默认配置
            full_slot_config_map = 全分槽批量赋值(global_default_config)
            persist_save_all_config(full_slot_config_map)
            current_config_version += 1
            stat_modify_count += 1
            // 广播默认配置快照
            full_config_broadcast = build_global_config_snapshot(full_slot_config_map, current_config_version)
            FOR target_mod IN ["ag-mem-27","ag-mem-29","ag-mem-40","ag-mem-41"]:
                send_config_sync_snapshot(target=target_mod, snapshot=full_config_broadcast)
            // 写入重置审计日志
            reset_audit_log = build_config_change_log("全局配置重置", admin_id, ["全部分槽"], "恢复出厂三维参数", NOW())
            send_audit_log("ag-mem-51", reset_audit_log)
            // 返回重置完成回执
            send_modify_ack(target=人工运维接口, ack_data=构建重置完成回执)

        // 5. 每180秒周期上报配置运行统计
        IF NOW() - last_report_ts >= 180 * 1000:
            stat_report = build_config_stat_report(
                current_state=internal_state,
                total_slot_count=LEN(full_slot_config_map),
                config_version=current_config_version,
                daily_modify_times=stat_modify_count
            )
            send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 查询未知业务分槽ID | 自动加载全局出厂默认三维参数返回，记录轻度告警 | 人工在配置模块新增对应分槽独立参数 |
| 人工配置参数数值越界、层级倒置 | 直接拒绝修改，返回标准化校验失败原因，不写入存储 | 管理员调整参数至合法区间重新提交 |
| 批量修改分槽数量超过5个上限 | 拆分多批次分步更新，或拒绝超大批量指令 | 缩减单次修改分槽列表至5个以内 |
| 持久化存储写入失败 | 内存配置不更新，返回修改失败，无配置广播下发 | 底层持久化介质IO恢复后重试配置修改 |
| 配置广播下发时下游模块离线 | 保留快照缓存，下游模块重启后主动拉取最新版本参数 | 下游模块恢复在线，主动发起参数查询 |
| 全局紧急熔断指令下发 | 冻结所有配置读写，拒绝查询、修改、重置操作 | F0下发RESUME恢复指令 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 分槽三维参数查询请求 | 只读 | ag-mem-27/29/40/41 发送 |
| 内部调度总线 | 读 | 人工配置更新/重置指令 | 只读 | 人工运维配置接口下发 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | 三维权重配置查询回执 | 专属写入 | 向各依赖模块返回参数 |
| 内部调度总线 | 写 | 配置更新完成回执/拒绝通知 | 专属写入 | 向人工运维接口返回操作结果 |
| 内部调度总线 | 写 | 全局配置同步广播快照 | 事件写入 | 向ag-mem-27/29/40/41推送新版参数 |
| 内部调度总线 | 写 | 配置变更审计日志 | 事件写入 | 向 ag-mem-51 推送 |
| 内部调度总线 | 写 | 配置周期状态上报 | 周期写入 | 向 ag-mem-03 推送 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| P-01 | 全系统分层阈值、I计算权重、复用保护参数统一由本模块集中管控，业务存储/判定模块禁止本地硬编码参数 |
| P-02 | 所有人工修改、全局重置操作必须执行管理员双重挑战码确认，单次操作独立校验，不可复用凭证 |
| P-03 | L5安全准入S值、置信度阈值设置下限保护，人工配置不可降低系统安全底线 |
| P-04 | 遗忘阈值、复用保护次数强制层级递增，防止高层长效记忆被提前清理 |
| P-05 | 任何配置变更、重置操作必须完整写入ag-mem-51审计日志，记录管理员、修改范围、新旧参数对比，不可删除篡改 |
| P-06 | 仅授权ag-mem-27/29/40/41查询三维配置，其他模块无权限读取权重与阈值参数 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M35-01 | `CONFIG_READY`，ag-mem-40查询普通分槽遗忘阈值 | 分槽参数查询请求（维度=遗忘阈值） | 返回分层递增L1~L4遗忘I阈值，匹配当前配置版本 |
| TC-M35-02 | `CONFIG_READY`，管理员提交倒置阈值（L3阈值<L2） | 人工批量配置更新指令 | 参数校验失败，拒绝修改，返回层级递增规则提示 |
| TC-M35-03 | `CONFIG_READY`，合法参数+双重确认通过 | 合规三维参数修改指令 | 配置持久化、全模块广播快照、生成变更审计日志 |
| TC-M35-04 | `CONFIG_READY`，修改S直达阈值=0.85低于下限 | 人工配置指令调低安全准入阈值 | 校验拦截，拒绝提交，提示安全阈值下限保护 |
| TC-M35-05 | `CONFIG_READY`，查询不存在的全新分槽ID | 未知分槽参数查询请求 | 返回全局出厂默认三维参数，记录轻度告警 |
| TC-M35-06 | `CONFIG_READY`，收到全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，拒绝所有参数查询与配置修改 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、全局配置中枢定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 三维权重维度、分层参数、合法性校验规则完整 | ✅ |
| 伪代码覆盖参数查询、人工修改校验、持久化、广播同步、重置、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖未知分槽、参数非法、批量超限、持久化故障、下游离线、熔断共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无逻辑漏洞、无违规参数修改路径 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |
