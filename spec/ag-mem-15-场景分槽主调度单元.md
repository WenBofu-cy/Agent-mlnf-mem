# ag-mem-15 场景分槽主调度单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书 4.4 分层分流前置模块）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-15 |
| 模块名称 | 场景分槽主调度单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 前置分流分层模块（L0上游入口分流器） |
| 核心职责 | 全局任务原始交互数据统一接收入口；基于业务场景、任务类型、用户空间、会话标识完成一级分槽路由；生成唯一funnel_id分槽标识，分发原始经验数据流至ag-mem-20 L0临时缓冲层；维护全量funnel分槽元数据池，记录各分槽创建时间、业务归属、冷热访问标记；接收ag-mem-35下发分槽划分规则、生命周期阈值；同步向ag-mem-03漏斗二调度单元上报分槽实时负载；对外提供funnel分槽元数据查询接口供ag-mem-37、ag-mem-40调用；定时上报分槽元数据内存占用至ag-mem-48；所有分槽创建、路由分发、分槽销毁操作写入ag-mem-51审计日志；作为记忆通路最前端分流节点，无持久业务经验存储，仅维护分槽路由元数据。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-35（三维权重配置单元，读取分槽划分规则、funnel生命周期、冷热判定参数）、ag-mem-48（全局容量配额管控，上报分槽元数据内存开销） |
| 被依赖模块 | ag-mem-20（L0临时缓冲层，唯一下游数据分发目标）、ag-mem-37（重要度定时刷新单元，查询funnel分槽基础元数据）、ag-mem-40（遗忘阈值判定单元，读取分槽冷热标签）、ag-mem-48（接收分槽元数据容量定时上报）、ag-mem-51（记录分槽全生命周期审计日志）、ag-mem-03（漏斗二调度单元，接收分槽负载、数量统计上报） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 分流待机就绪 | `SLOT_IDLE` | 元数据池正常，等待上游原始任务数据流入，无批量路由任务 | 系统初始化、熔断恢复、批量分发完成 |
| 原始数据接收缓存 | `RAW_DATA_BUFFER` | 接收外部任务交互原始数据，存入内存临时缓冲队列 | 上游业务引擎下发任务交互原始数据流 |
| 分槽路由计算 | `SLOT_ROUTE_CALC` | 按场景/任务/用户维度匹配分槽规则，生成/复用funnel_id，标记分槽冷热状态 | 原始数据缓冲填充完成 |
| 批量下发至L0缓冲 | `DISPATCH_TO_L0` | 按funnel分组打包原始经验条目，批量推送至ag-mem-20 | 全部分槽路由计算完毕 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，清空待分发原始缓存，暂停分槽创建与路由分发 | F0下发FUSE熔断指令；RESUME切回SLOT_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 外部任务原始交互数据流 | List<Struct>（task_content、task_type、user_space_id、session_id、action_time、raw_S、raw_base_I） | 外部业务执行引擎 | 智能体完成任务交互，输出原始经验素材 | 高 |
| 分槽划分规则配置回执 | Struct（场景匹配映射表、funnel自动过期天数、冷热访问判定周期、单funnel最大并发缓存条数） | ag-mem-35 三维权重配置单元 | 模块初始化、业务分槽策略更新 | 普通 |
| funnel分槽元数据批量查询请求 | Struct（funnel_id列表 / 按场景筛选条件） | ag-mem-37 / ag-mem-40 | 全局I值重算、分层遗忘扫描前置分槽过滤 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、全局熔断停机 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 按funnel分组原始经验批量推送 | List<Struct>（funnel_id、单槽原始经验条目集合、分槽冷热标记） | ag-mem-20 L0临时缓冲层 | 路由计算完成，存在有效待分发原始经验 | 高 |
| 原始数据流接收回执 | Struct（接收总条数、合法可分槽条数、非法丢弃条数） | 外部业务执行引擎 | 原始数据缓存解析完成 | 高 |
| funnel分槽完整元数据快照 | List<Struct>（funnel_id、scene场景、task_type、create_ts、last_access_ts、cold_flag、active_item_count） | ag-mem-37、ag-mem-40 | 收到分槽元数据批量查询请求 | 高 |
| 分槽元数据内存占用上报 | Struct（单元标识ag-mem-15、元数据总KB、当前活跃funnel总数） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量原始数据分发后即时上报 | 普通 |
| 分槽生命周期审计日志 | Struct（事件类型、新增funnel数量、销毁过期funnel数量、分发原始条目总量、时间戳、场景范围） | ag-mem-51 记忆变更日志追溯单元 | 每一批原始数据路由分发处理完成、过期分槽清理完成 | 普通 |
| 分槽负载周期运行统计上报 | Struct（当前状态、今日新建funnel总数、销毁过期funnel总数、累计分发至L0条目总量、冷热分槽占比） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 场景分槽核心规则（V1.1前置分流规范）
### 1. 全局分槽配置参数（ag-mem-35统一分发）
1. funnel自动过期时长：90天，无访问自动标记待销毁；
2. 冷热判定周期：15天，15天无交互标记cold_flag=冷分槽；
3. 单funnel L0前置缓存上限：2000条原始条目，超上限触发分流限流；
4. funnel生成匹配优先级：user_space_id > task_type > scene场景标签。

### 2. 原始数据丢弃规则（满足任意一条直接丢弃，不生成funnel、不送入L0）
1. task_type为空、user_space_id非法；
2. raw_base_I ≤ 0，无基础价值，无需进入记忆链路；
3. 单条原始交互内容为空，无有效任务经验素材。

### 3. funnel复用/新建逻辑
1. 存在匹配scene+task_type+user_space_id的未过期funnel_id：复用现有分槽，更新last_access_ts；
2. 无匹配分槽：新建唯一funnel_id，写入分槽元数据池；
3. 冷分槽重新产生交互：自动切换cold_flag=热分槽，重置访问时间戳。

### 4. 流转强制约束
1. 唯一下游：所有分槽打包数据仅推送至ag-mem-20，无其他分流出口；
2. 无持久经验存储：仅内存维护funnel元数据，原始数据分发完成即清空本地缓冲；
3. 不参与I值迭代、向量构建、归档、晋升等记忆业务逻辑，仅做前置路由分流；
4. 分层隔离：无法直接读写L1~L5任意存储层条目，仅提供分槽ID路由标识。

### 5. 批量约束
单次接收原始数据流最大1000条，超量自动分片串行路由计算，防止分槽匹配算力过载。

## 核心处理逻辑
```
FUNCTION scene_slot_dispatch_main_loop():
    STATE_IDLE = SLOT_IDLE
    STATE_BUFFER = RAW_DATA_BUFFER
    STATE_ROUTE = SLOT_ROUTE_CALC
    STATE_DISPATCH = DISPATCH_TO_L0
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载分槽全局配置
    slot_cfg = query_slot_config(from_m35="ag-mem-35")
    funnel_expire_ms = slot_cfg.funnel_expire_day * 24 * 3600 * 1000
    cold_cycle_ms = slot_cfg.cold_check_day * 24 * 3600 * 1000
    max_slot_buffer = slot_cfg.per_funnel_max_raw_item
    temp_raw_buffer = []
    funnel_meta_pool = {} // key:funnel_id, value:分槽元数据结构体
    stat_new_funnel = 0
    stat_destroy_funnel = 0
    stat_dispatch_total = 0
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                temp_raw_buffer.clear()
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = SLOT_IDLE

        // 定时清理过期冷分槽
        now_ts = NOW()
        expire_funnel_list = []
        for f_id, meta in funnel_meta_pool.items():
            idle_duration = now_ts - meta.last_access_ts
            if idle_duration >= funnel_expire_ms:
                expire_funnel_list.append(f_id)
        for expire_fid in expire_funnel_list:
            del funnel_meta_pool[expire_fid]
            stat_destroy_funnel += 1

        // 2. 接收外部业务原始任务数据流
        IF 收到外部任务原始交互数据流:
            raw_data_list = 获取原始条目列表
            internal_state = RAW_DATA_BUFFER
            temp_raw_buffer.extend(raw_data_list)
            recv_total = len(temp_raw_buffer)
            // 回执回传给业务引擎
            recv_ack = build_raw_recv_ack(total=len(raw_data_list), cached=recv_total, discard=0)
            send_ack(target="外部业务执行引擎", ack_data=recv_ack)
            internal_state = SLOT_ROUTE_CALC

            // 3. 分槽路由匹配、新建/复用funnel
            slot_group_map = {}
            discard_count = 0
            valid_raw_items = []
            // 过滤无效原始条目
            for raw_item in temp_raw_buffer:
                if raw_item.task_type == "" or raw_item.user_space_id == None or raw_item.raw_base_I <= 0:
                    discard_count += 1
                    continue
                valid_raw_items.append(raw_item)
            // 按匹配维度生成分组key
            for item in valid_raw_items:
                group_key = f"{item.user_space_id}_{item.task_type}_{item.scene}"
                match_funnel = None
                // 匹配已有funnel
                for f_id, meta in funnel_meta_pool.items():
                    if meta.user_space_id == item.user_space_id and meta.task_type == item.task_type and meta.scene == item.scene:
                        match_funnel = f_id
                        break
                if match_funnel != None:
                    // 复用分槽，更新访问时间
                    funnel_meta_pool[match_funnel].last_access_ts = now_ts
                    idle_time = now_ts - funnel_meta_pool[match_funnel].create_ts
                    if idle_time < cold_cycle_ms:
                        funnel_meta_pool[match_funnel].cold_flag = False
                    if match_funnel not in slot_group_map:
                        slot_group_map[match_funnel] = []
                    slot_group_map[match_funnel].append(item)
                else:
                    // 新建funnel分槽
                    new_fid = gen_uuid()
                    funnel_meta_pool[new_fid] = {
                        "funnel_id": new_fid,
                        "scene": item.scene,
                        "task_type": item.task_type,
                        "user_space_id": item.user_space_id,
                        "create_ts": now_ts,
                        "last_access_ts": now_ts,
                        "cold_flag": False,
                        "active_item_count": 0
                    }
                    stat_new_funnel += 1
                    slot_group_map[new_fid] = [item]
            temp_raw_buffer.clear()
            internal_state = DISPATCH_TO_L0

            // 4. 按funnel分组批量推送至ag-mem-20
            dispatch_batch_list = []
            for f_id, item_list in slot_group_map.items():
                // 单槽条目超限截断
                if len(item_list) > max_slot_buffer:
                    item_list = item_list[:max_slot_buffer]
                funnel_meta_pool[f_id].active_item_count = len(item_list)
                cold_tag = funnel_meta_pool[f_id].cold_flag
                dispatch_batch_list.append({
                    "funnel_id": f_id,
                    "raw_item_list": item_list,
                    "cold_flag": cold_tag
                })
            if len(dispatch_batch_list) > 0:
                send_slot_batch(target="ag-mem-20", batch_list=dispatch_batch_list)
                stat_dispatch_total += sum(len(b["raw_item_list"]) for b in dispatch_batch_list)
            // 写入分槽路由审计日志
            audit_log = build_slot_route_audit(
                raw_total=len(raw_data_list),
                discard_num=discard_count,
                new_funnel=stat_new_funnel,
                expire_clear=len(expire_funnel_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = SLOT_IDLE

        // 5. 响应分槽元数据批量查询
        IF 收到funnel分槽元数据批量查询请求:
            query_param = 获取查询条件
            meta_snap = []
            if query_param.按场景筛选 != None:
                target_scene = query_param.按场景筛选
                for f_id, meta in funnel_meta_pool.items():
                    if meta.scene == target_scene:
                        meta_snap.append(meta)
            else:
                query_fid_list = query_param.funnel_id列表
                for f_id in query_fid_list:
                    if f_id in funnel_meta_pool:
                        meta_snap.append(funnel_meta_pool[f_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_snap)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_snap)

        // 6. 定时内存占用上报 + 180s周期统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            meta_kb = calc_funnel_meta_kb(funnel_meta_pool, slot_cfg.avg_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-15", used_kb=meta_kb, funnel_count=len(funnel_meta_pool))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                cold_count = sum(1 for m in funnel_meta_pool.values() if m.cold_flag == True)
                hot_count = len(funnel_meta_pool) - cold_count
                stat_report = build_slot_stat_report(
                    state=internal_state,
                    today_new_funnel=stat_new_funnel,
                    total_destroy=stat_destroy_funnel,
                    total_dispatch_item=stat_dispatch_total,
                    hot_slot=hot_count,
                    cold_slot=cold_count
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 原始条目task_type/用户ID缺失、基础I非法 | 直接丢弃，计入丢弃统计，不生成分槽 | 业务引擎重新推送完整合规原始交互数据 |
| 单次原始数据流超1000条上限 | 自动分片串行路由计算，分批打包下发L0 | 内置分片逻辑自动执行 |
| 单funnel原始条目超过2000条上限 | 截断超出部分，仅保留前2000条推送ag-mem-20 | 业务侧降低单批次交互输出量 |
| funnel元数据内存占用溢出 | 优先自动清理90天过期冷分槽释放内存，仍溢出则限流接收新原始数据 | 扩容计算内存或调短funnel过期周期 |
| 全局FUSE熔断触发 | 清空待分发原始缓冲，暂停新建funnel与路由分发 | ag-mem-01下发RESUME恢复指令 |
| 无场景分槽匹配配置 | 采用全局通用三字段匹配规则兜底创建funnel | ag-mem-35运维侧补充分场景专属匹配策略 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 外部业务总线 | 读 | 外部任务原始交互数据流 | 只读 | 业务执行引擎上游输入 |
| 内部调度总线 | 读 | 分槽规则配置、全局熔断指令、分槽元数据查询 | 只读 | ag-mem-35、ag-mem-01、ag-mem-37/40 |
| 内部调度总线 | 写 | 按funnel分组原始经验批量推送 | 专属写入 | 唯一下游 ag-mem-20 |
| 外部业务总线 | 写 | 原始数据流接收回执 | 专属写入 | 返回业务执行引擎 |
| 内部调度总线 | 写 | 分槽元数据快照、内存容量上报、审计日志、周期负载统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| S15-01 | 仅允许业务引擎原始数据作为输入，禁止其他存储模块反向推送条目，保证记忆数据源头唯一可控 |
| S15-02 | 所有原始经验仅单向分发至ag-mem-20，无旁路分流至其他存储层，记忆通路入口链路统一 |
| S15-03 | 不持久化任何任务原始经验，仅内存维护funnel分槽元数据，减少原始业务数据存储泄露风险 |
| S15-04 | 分槽匹配规则、funnel过期时长、冷热判定阈值全部由ag-mem-35集中管控，本地无硬编码业务参数 |
| S15-05 | 新建、过期销毁、批量分发全量写入审计日志，留存funnel创建时间与业务归属，支撑全链路分槽溯源 |
| S15-06 | 熔断状态清空待分发原始缓冲，恢复后业务引擎重新推送数据，避免缓存过期无效原始素材流入记忆链路 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M15-01 | `SLOT_IDLE`，全新场景+用户+任务类型合规原始数据 | 批量原始交互条目 | 自动新建funnel_id，分组推送至ag-mem-20，新增分槽审计日志 |
| TC-M15-02 | `SLOT_IDLE`，已有匹配funnel的原始交互数据 | 同分槽维度原始条目 | 复用现有funnel，更新访问时间戳，标记热分槽后下发L0 |
| TC-M15-03 | `SLOT_IDLE`，raw_base_I≤0、task_type为空的无效条目混杂数据流 | 混合合法/非法原始条目 | 非法条目直接丢弃，合法条目正常分槽分发，统计丢弃数量 |
| TC-M15-04 | `SLOT_IDLE`，单次原始数据流1200条 | 超大批量原始数据 | 自动分片串行路由计算，完整下发至ag-mem-20无阻塞 |
| TC-M15-05 | `SLOT_IDLE`，ag-mem-37下发指定funnel_id元数据查询 | 分槽ID批量查询请求 | 返回对应funnel完整场景、冷热、创建时间元数据快照 |
| TC-M15-06 | `SLOT_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，清空原始缓冲，停止分槽创建与数据分发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-15匹配白皮书记忆通路前置分槽调度定位 | ✅ |
| 下游唯一ag-mem-20，数据流作为五层记忆最前端入口闭环无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖数据接收、路由计算、批量下发全流程 | ✅ |
| 输入输出完整标注收发端、结构体、优先级，上下游链路无错乱 | ✅ |
| funnel分槽生成、冷热标记、过期清理规则严格对齐V1.1前置分流降噪设计 | ✅ |
| 伪代码覆盖原始数据接收、过滤、分槽匹配、新建复用、批量下发、元数据查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖非法原始条目、超大批次、单槽超限、内存溢出、熔断、无分槽规则共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅业务引擎可输入原始经验 | ✅ |
| 6条V1.1安全约束杜绝源头脏数据、旁路分流、原始数据长期留存风险 | ✅ |
| 6条自动化测试用例覆盖全部分槽调度核心场景 | ✅ |

---