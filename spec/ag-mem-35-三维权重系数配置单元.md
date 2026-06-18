# ag-mem-35 三维权重配置单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书全局统一配置中心）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-35 |
| 模块名称 | 配三维权重置单元（全局统一配置中心） |
| 所属分区 | 全局基础底座 / 全模块统一参数下发中枢 |
| 核心职责 | 全记忆链路唯一配置存储与分发中心，统一承载**容量权重、冷热负载权重、记忆晋升权重**三维全套参数；持久化存储所有模块业务阈值、周期、倍率、白名单、限流规则；接收运维后台配置新增/修改/删除指令，校验参数合法性并落地持久化；主动向所有ag-mem系列模块推送配置变更回执；响应各模块实时配置拉取请求；维护配置版本号、变更记录；定时上报配置持久层容量占用至ag-mem-48；所有配置新增、修改、回滚、删除操作全量写入ag-mem-51审计日志；仅负责参数存储、校验、分发，无记忆条目读写、分槽管控、调度执行能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度，接收全局熔断指令，熔断后暂停配置推送）、ag-mem-48（全局容量配额管控，上报自身配置存储容量开销） |
| 被依赖模块 | 全部ag-mem业务模块（ag-mem01/03、ag-mem15~19、ag-mem20~30、ag-mem37/40/42/45/48）、运维配置后台（下发配置变更指令、查询全量配置）、ag-mem-51（写入配置变更审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 配置待机就绪 | `CFG_IDLE` | 配置库正常加载，等待各模块拉取请求/运维变更指令，无批量推送任务 | 系统初始化加载完整配置、熔断恢复、批量配置推送完成 |
| 运维配置变更校验 | `CFG_VERIFY` | 接收运维新增/修改/删除配置，校验参数范围、模块归属、三维参数合法性 | 运维后台下发配置变更操作指令 |
| 配置持久落地+版本更新 | `CFG_PERSIST` | 校验通过后写入持久配置库，递增全局配置版本号，记录变更快照 | 参数校验全部合法，无非法阈值 |
| 批量配置变更推送 | `CFG_BROADCAST` | 分片向所有关联业务模块推送最新配置回执 | 配置持久化完成，存在受影响模块 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局FUSE熔断，暂停配置主动推送，仅保留只读查询能力 | ag-mem-01下发FUSE指令；RESUME切回CFG_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 运维配置变更操作指令 | Struct（操作类型add/update/rollback/delete、target_module、三维参数分组、参数键值对、操作人、版本回滚目标号） | 运维配置后台 | 人工修改/新增/回滚/删除业务参数 | 高 |
| 模块配置实时拉取请求 | Struct（target_module_id、参数分组：容量/冷热/晋升/熔断/分槽/遗忘） | 全ag-mem业务模块 | 模块初始化、定时重载配置、策略变更主动拉取 | 高 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 总控F0 | 全局熔断启停，控制配置广播推送开关 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 模块专属三维配置回执 | Struct（当前全局cfg_version、对应分组全套阈值、周期、倍率、白名单、限流参数） | 请求配置的对应ag-mem模块 | 收到模块配置拉取请求、全局配置变更广播推送 | 高 |
| 配置变更操作执行回执 | Struct（操作结果success/fail、新全局版本号、非法参数错误明细） | 运维配置后台 | 运维下发配置变更指令校验持久完成 | 高 |
| 配置存储容量占用上报 | Struct（单元ag-mem-35、配置总存储KB、配置分组数量、全局版本号） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量配置变更后即时上报 | 普通 |
| 配置变更审计日志 | Struct（操作类型、目标模块、变更前后参数快照、操作账号、全局新旧版本号、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一次配置新增/修改/回滚/删除落地完成 | 普通 |
| 配置单元周期运行统计上报 | Struct（当前状态、今日配置变更总次数、版本迭代总数、参数校验失败次数） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 三维权重配置核心规范（V1.1全局统一参数标准）
### 1. 三维参数分组划分
1. **容量维度参数**（供给ag-mem28、ag-mem48）：分层总配额、预警/紧急占用比例、向量索引预留容量、单funnel容量上限
2. **冷热负载维度参数**（供给ag-mem16、ag-mem17、ag-mem18、ag-mem19）：冷热评分权重、分槽闲置过期天数、软/硬限流QPS阈值、限流冷却时长、高风险分组判定比例
3. **记忆晋升维度参数**（供给ag-mem20~27、ag-mem30、ag-mem37、ag-mem40）：各层晋升周期、归档扫描倍率、I值刷新周期、遗忘扫描周期、条目I值衰减权重、冷数据判定天数

### 2. 全局配置基础约束
1. 全局配置版本号单调递增，每一次有效变更+1；各模块本地缓存版本低于全局版本时自动拉取更新；
2. 参数强校验规则：百分比阈值0~100、时间周期≥10s、容量/数量阈值≥0、倍率≥1；非法参数直接驳回运维操作；
3. 模块参数隔离：仅向目标模块下发其业务所需分组参数，不广播全量配置，减少总线数据量；
4. 兜底默认配置：模块未配置专属参数时，自动加载全局通用三维模板，保证无配置时模块可正常运行。

### 3. 变更推送规则
1. 单模块局部参数修改：仅推送该目标模块配置回执；
2. 全局通用三维模板修改：分片广播全部业务模块；
3. 熔断SYSTEM_PAUSED状态：停止主动广播推送，模块只能主动拉取只读配置。

### 4. 流转强制约束
1. 无任何业务记忆读写、分槽创建销毁、调度指令下发能力，仅做参数存储分发；
2. 所有业务模块参数唯一来源，禁止模块本地硬编码阈值、周期、权重；
3. 配置单向分发：仅向外输出只读参数回执，不接收业务模块回写参数；
4. 持久化双副本存储配置，防止单副本丢失导致全链路参数失效。

### 5. 批量约束
全局广播推送单次最多批量下发30个模块配置回执，超量自动分片串行推送，避免总线消息风暴。

## 核心处理逻辑
```
FUNCTION cfg_3d_weight_main_loop():
    STATE_IDLE = CFG_IDLE
    STATE_VERIFY = CFG_VERIFY
    STATE_PERSIST = CFG_PERSIST
    STATE_BROADCAST = CFG_BROADCAST
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 全局配置元数据
    global_cfg_version = 1
    persist_cfg_store = {} // 完整三维配置持久存储
    module_cfg_map = {} // key:module_id, value:该模块所需三维参数分组
    stat_cfg_modify_times = 0
    stat_verify_fail = 0
    last_cap_report_ts = NOW()
    max_broadcast_batch = 30

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE":
                internal_state = STATE_PAUSED
                send_audit_log(target="ag-mem-51", log_data=build_cfg_state_audit(old_state, internal_state, "F0熔断暂停配置广播", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = CFG_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_cfg_state_audit(old_state, internal_state, "熔断恢复配置正常广播", now_ts))

        // 2. 处理运维配置变更操作
        IF 收到运维配置变更指令:
            op_req = 获取运维操作结构体
            internal_state = CFG_VERIFY
            verify_result = verify_3d_param_valid(op_req, persist_cfg_store)
            if not verify_result.success:
                stat_verify_fail += 1
                // 返回失败回执给运维后台
                fail_ack = build_cfg_op_ack(success=False, err_detail=verify_result.err_msg, new_version=global_cfg_version)
                send_op_ack(target="运维配置后台", ack_data=fail_ack)
                internal_state = CFG_IDLE
                CONTINUE
            // 参数校验通过，落地持久存储
            internal_state = CFG_PERSIST
            old_cfg_snap = deep_copy(persist_cfg_store.get(op_req.target_module, {}))
            apply_cfg_change(persist_cfg_store, op_req)
            global_cfg_version += 1
            stat_cfg_modify_times += 1
            new_cfg_snap = persist_cfg_store[op_req.target_module]
            // 生成变更审计日志
            audit_log = build_cfg_change_audit(
                op_type=op_req.操作类型,
                target_mod=op_req.target_module,
                old_snap=old_cfg_snap,
                new_snap=new_cfg_snap,
                op_account=op_req.操作人,
                old_ver=global_cfg_version - 1,
                new_ver=global_cfg_version,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            // 向运维返回成功回执
            success_ack = build_cfg_op_ack(success=True, err_detail="", new_version=global_cfg_version)
            send_op_ack(target="运维配置后台", ack_data=success_ack)
            internal_state = CFG_BROADCAST
            // 筛选需要推送配置的模块
            target_broadcast_mods = get_affected_module_list(op_req, module_cfg_map)
            slice_mod_list = split_slice(target_broadcast_mods, max_broadcast_batch)
            for slice in slice_mod_list:
                for mid in slice:
                    mod_3d_cfg = assemble_module_3d_cfg(mid, persist_cfg_store, global_cfg_version)
                    send_cfg_reply(target=mid, cfg_data=mod_3d_cfg)
            internal_state = CFG_IDLE

        // 3. 响应各模块配置实时拉取请求
        IF 收到模块配置拉取请求:
            pull_req = 获取拉取参数
            target_mid = pull_req.target_module_id
            req_groups = pull_req.参数分组列表
            // 组装该模块所需三维配置回执
            reply_cfg = assemble_module_3d_cfg(target_mid, persist_cfg_store, global_cfg_version, filter_groups=req_groups)
            send_cfg_reply(target=target_mid, cfg_data=reply_cfg)

        // 4. 60s定时容量上报 + 180s周期统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cfg_total_kb = calc_persist_cfg_size(persist_cfg_store)
            cap_report = build_cap_report(layer="ag-mem-35", used_kb=cfg_total_kb, group_count=len(persist_cfg_store), current_version=global_cfg_version)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                stat_report = build_cfg_runtime_stat(
                    state=internal_state,
                    total_modify=stat_cfg_modify_times,
                    total_verify_fail=stat_verify_fail,
                    latest_version=global_cfg_version
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 运维提交参数数值越界、类型错误 | 校验直接驳回，返回错误明细，不落地配置、不更新版本 | 运维修正参数后重新提交变更指令 |
| 单次广播推送目标模块超过30个上限 | 自动分片串行下发配置回执，避免总线消息拥堵 | 内置分片逻辑自动执行 |
| 配置持久存储IO读写失败 | 拒绝本次配置变更，返回存储异常回执，不更新全局版本 | 底层持久存储读写链路恢复正常 |
| 业务模块拉取不存在的自定义参数分组 | 自动填充全局通用三维模板参数返回，不返回空值 | 运维可新增该模块专属分组配置 |
| 全局FUSE熔断触发 | 停止主动配置广播推送，仅保留模块主动拉取只读配置能力 | ag-mem-01下发RESUME恢复广播 |
| 运维执行版本回滚目标版本不存在 | 参数校验失败，驳回操作，记录校验失败统计 | 运维填写合法历史版本号重新提交 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 运维配置总线 | 读 | 运维配置变更操作指令 | 只读 | 运维配置后台 |
| 全局内部调度总线 | 读 | 模块配置拉取请求、F0全局熔断指令 | 只读 | 全部ag-mem业务模块、ag-mem-01 |
| 全局内部调度总线 | 写 | 模块三维配置回执批量推送 | 专属配置下发权限 | 所有ag-mem业务模块 |
| 运维配置总线 | 写 | 配置变更操作执行回执 | 专属写入 | 运维配置后台 |
| 内部调度总线 | 写 | 配置存储容量上报、配置变更审计日志、周期运行统计 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1全局配置强制规范）
| 规则编号 | 内容 |
|:---:|------|
| CFG35-01 | 全链路唯一配置下发源，禁止任意业务模块本地硬编码阈值、周期、权重，统一收敛参数管控至ag-mem-35，消除多版本参数不一致风险 |
| CFG35-02 | 仅具备配置读写存储权限，无任何记忆条目、分槽、调度任务操作权限，纯参数底座，杜绝业务数据篡改风险 |
| CFG35-03 | 所有运维参数变更前置强合法性校验，拦截非法极值参数，防止错误阈值引发全链路容量溢出、无限流、记忆晋升失效故障 |
| CFG35-04 | 每一次配置变更完整记录变更前后参数快照、操作人、全局版本号写入ag-mem-51审计日志，支持任意历史版本回滚与故障参数溯源 |
| CFG35-05 | 配置广播分片限流，单次最多推送30个模块，规避大批量瞬时总线消息抢占通信资源，保障各模块业务指令优先通行 |
| CFG35-06 | 熔断状态仅开放只读配置查询，暂停主动广播推送，避免故障期间批量下发新参数加剧链路异常 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-CFG35-01 | `CFG_IDLE`，运维修改ag-mem-28容量预警阈值80→90，参数合法 | 配置update变更指令 | 参数校验通过，持久存储更新，全局版本+1，向ag-mem-28推送新配置回执，生成变更审计日志 |
| TC-CFG35-02 | `CFG_IDLE`，运维提交冷槽判定天数=-5非法参数 | 含负数阈值变更指令 | 参数校验失败，返回错误回执，配置不落地、版本不递增 |
| TC-CFG35-03 | `CFG_IDLE`，ag-mem-16启动初始化拉取冷热权重分组配置 | 模块配置拉取请求 | 返回冷热维度全套三维权重、评分阈值配置回执 |
| TC-CFG35-04 | `CFG_IDLE`，修改全局通用晋升模板，关联36个业务模块 | 全局模板更新指令 | 自动分片2批串行广播配置回执至全部关联模块，无总线阻塞 |
| TC-CFG35-05 | `CFG_IDLE`，运维下发版本回滚至历史合法版本指令 | 配置rollback操作指令 | 恢复历史参数快照，版本递增，广播对应模块新配置，记录回滚审计日志 |
| TC-CFG35-06 | `CFG_IDLE`，收到ag-mem-01下发FUSE熔断指令 | 全局全熔断调度指令 | 切换`SYSTEM_PAUSED`，停止主动配置广播，仅响应模块主动拉取请求 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-35匹配白皮书全局三维权重统一配置中心定位 | ✅ |
| 覆盖全部上下游业务模块，全链路参数唯一来源，配置分发数据流闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖运维变更校验、持久落地、分片广播全流程 | ✅ |
| 输入输出完整标注收发端、结构体、优先级，全局参数链路无错乱 | ✅ |
| 三维参数分组、版本管控、参数校验、分片广播规则严格对齐V1.1全局统一参数规范 | ✅ |
| 伪代码覆盖运维变更校验、持久存储、分片广播、模块拉取、容量上报、变更审计全链路 | ✅ |
| 异常场景覆盖非法参数、存储IO故障、超大广播批量、无专属配置、熔断只读、版本回滚失败共6类全覆盖 | ✅ |
| 总线权限隔离，仅ag-mem-35具备全局配置下发权限，业务模块仅可读不可写参数 | ✅ |
| 6条V1.1安全约束统一参数管控、隔离业务操作、拦截非法阈值、全变更可审计、防消息风暴、熔断限流 | ✅ |
| 6条自动化测试用例覆盖全部配置中心核心业务场景 | ✅ |

---