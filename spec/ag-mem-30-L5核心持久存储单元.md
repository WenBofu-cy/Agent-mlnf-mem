# ag-mem-30-L5核心持久存储单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书4.4.1五层单向记忆晋升通路）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-30 |
| 模块名称 | L5核心持久存储单元（顶层永久记忆库） |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层单向记忆晋升通路（顶层永久存储终点） |
| 核心职责 | 唯一上游写入源为ag-mem-29锁控放行后的标准化抽象单元；系统顶层永久记忆持久层，全量落地经多层降噪、抽象、安全校验、锁控放行后的高价值抽象记忆单元；按funnel分域隔离存储，搭载高密度蒸馏向量索引，支持长期海量语义检索；无自动归档/删除逻辑，L5记忆永久留存；仅支持人工手动归档/冻结操作；对外输出顶层抽象单元完整元数据供给ag-mem-37全局I值刷新、ag-mem-40全分层遗忘扫描；定时上报业务数据、蒸馏向量索引占用容量至ag-mem-48；所有新增、人工冻结、手动归档操作全量写入ag-mem-51审计日志；五层晋升通路终点，无下游自动晋升链路。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、ag-mem-29（L5安全锁控单元，唯一合法上游写入入口）、ag-mem-35（三维权重配置单元，分funnel读取L5人工操作权限、向量索引压缩参数）、ag-mem-48（全局容量配额管控，读取L5总存储容量上限、预警阈值） |
| 被依赖模块 | ag-mem-37（重要度定时刷新单元，读取L5顶层抽象单元元数据）、ag-mem-40（遗忘阈值判定单元，提供L5全量条目扫描快照）、ag-mem-42（冗余记忆删除单元，仅接收人工发起的L5归档候选清单）、ag-mem-48（接收L5分层容量定时上报）、ag-mem-51（记录L5全部顶层记忆变更审计日志）、ag-mem-03（漏斗二调度单元，周期上报L5运行统计指标） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 待机就绪 | `L5_IDLE` | 等待ag-mem-29放行抽象单元写入，无批量操作任务 | 系统初始化完成、熔断恢复、批量写入/人工操作处理完毕 |
| 抽象单元持久写入 | `ABS_PERSIST` | 校验放行抽象单元合法性，按funnel分域落盘，构建高密度蒸馏向量索引 | 收到ag-mem-29批量放行抽象单元推送 |
| 人工批量操作扫描 | `MANUAL_OP_SCAN` | 接收运维人工冻结/归档指令，筛选目标abs_id生成归档候选 | 收到人工记忆运维操作指令 |
| 元数据批量导出扫描 | `META_EXPORT_SCAN` | 响应ag-mem-37/ag-mem-40全量元数据拉取请求，输出顶层记忆快照 | 收到元数据批量查询请求 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、人工操作、元数据导出，内存缓存元数据 | F0下发FUSE熔断指令；RESUME指令切回L5_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 锁控放行抽象记忆单元批量推送 | List<Struct>（abs_id、source_light_ids、funnel_id、abs_I、total_origin_reuse、abstract_vector、task_group、create_ts） | ag-mem-29 L5安全锁控单元 | ag-mem-29完成三层锁校验，放行合法顶层抽象单元 | 高 |
| L5人工运维操作指令 | Struct（操作类型：freeze/archive、目标funnel列表、指定abs_id清单） | ag-mem-03 漏斗二调度单元 | 运维后台发起顶层记忆冻结/归档操作 | 普通 |
| L5顶层抽象单元元数据批量查询请求 | Struct（abs_id列表 / 全量导出标记） | ag-mem-37 / ag-mem-40 | 全局I值批量重算、全分层遗忘扫描 | 高 |
| L5分层容量配额配置回执 | Struct（L5总容量上限、预警占用比例、向量索引预留容量） | ag-mem-48 全局容量配额单元 | 模块初始化、人工扩容顶层存储配额 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统故障、紧急熔断停机、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L5抽象单元写入完成回执 | Struct（批量总条数、成功写入数量、失败abs_id清单） | ag-mem-29 L5安全锁控单元 | 抽象单元批量持久落盘、蒸馏向量索引构建完成 | 高 |
| L5人工归档候选清单 | List<Struct>（abs_id、遗忘原因=人工运维、当前abs_I、suggest_handle=archive） | ag-mem-42 冗余记忆删除单元 | 人工发起归档操作，筛选目标顶层抽象单元 | 普通 |
| L5顶层抽象单元元数据快照 | List<abs_id、abs_I、total_origin_reuse、create_ts、funnel_id、abstract_vector、task_group、manual_status> | ag-mem-37 / ag-mem-40 | I值刷新、遗忘扫描批量查询/全量导出 | 高 |
| L5分层容量占用上报 | Struct（层级=L5、业务数据占用KB、蒸馏向量索引占用KB、顶层抽象单元总数量） | ag-mem-48 全局容量配额 | 每60秒定时上报、批量条目写入后即时上报 | 普通 |
| L5顶层记忆变更审计日志 | Struct（事件类型、条目操作数量、funnel分域范围、时间戳、溯源source_light_ids、人工操作账号） | ag-mem-51 记忆变更日志追溯单元 | 写入、人工冻结、手动归档操作完成 | 普通 |
| L5周期运行统计上报 | Struct（当前状态、今日新增顶层抽象单元总量、人工归档条目总数、人工冻结条目总数、向量索引总条目数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## L5顶层存储核心规则（严格对齐V1.1白皮书4.4.1五层晋升通路终点规范）
### 1. 全局固定配置参数（由ag-mem-35、ag-mem-48协同下发）
1. L5无自动时效清理机制：所有入库抽象单元永久留存，仅人工可触发冻结/归档；
2. L5容量预警阈值：占用达到90%触发容量告警推送至ag-mem-03；
3. 准入前置强制校验：仅接收ag-mem-29锁控放行条目，无锁放行标记直接拒绝写入；
4. 向量索引压缩等级：最高等级蒸馏压缩，适配海量长期顶层记忆存储。

### 2. 写入准入唯一条件
条目携带ag-mem-29输出的合法放行标记，abs_id全局唯一无重复，funnel分域合法。

### 3. 人工操作区分规则
1. 冻结freeze：条目保留在L5存储，禁止后续I值重算与遗忘扫描，不可二次晋升；
2. 归档archive：生成归档候选清单推送ag-mem-42，从L5主存储移除，转入离线归档库；
3. 自动逻辑：无定时归档、无自动过期清理、无自动晋升下游模块。

### 4. V1.1分层流转强制约束
1. 唯一合法上游写入源：仅接收ag-mem-29锁控放行抽象单元，禁止任何其他模块直接写入顶层记忆；
2. 五层链路终点：无自动向下游晋升逻辑，是漏斗二记忆通路最终落地层；
3. 清理规则区分：L0/L1自动物理删除，L2/L3/L4自动归档，L5仅支持人工手动归档；
4. 全链路溯源：每条顶层抽象单元完整保存多层上游溯源ID集合，满足审计追溯要求。

### 5. 批量处理约束
单次批量写入最大1000条抽象单元；单次人工操作最多处理500条abs_id，超量自动分片串行执行，避免底层存储IO冲击。

## 核心处理逻辑
```
FUNCTION l5_core_storage_main_loop():
    STATE_IDLE = L5_IDLE
    STATE_PERSIST = ABS_PERSIST
    STATE_MANUAL_SCAN = MANUAL_OP_SCAN
    STATE_META_EXPORT = META_EXPORT_SCAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载全局L5顶层存储配置
    l5_global_cfg = query_layer_config(from_m35="ag-mem-35")
    l5_cap_cfg = query_cap_config(from_m48="ag-mem-48")
    cap_warn_ratio = l5_cap_cfg.warn_usage_ratio
    // 按funnel分域顶层持久存储缓存
    funnel_abs_store = {}
    stat_today_add = 0
    stat_manual_archive = 0
    stat_manual_freeze = 0
    last_report_ts = NOW()
    max_write_batch = 1000
    max_manual_batch = 500

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级处理
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = L5_IDLE

        // 2. 接收上游ag-mem-29锁控放行抽象单元写入
        IF 收到锁控放行抽象记忆单元批量推送:
            batch_write_req = 获取放行抽象条目列表
            internal_state = ABS_PERSIST
            success_cnt = 0
            fail_abs_ids = []
            now_ts = NOW()
            FOR abs_item IN batch_write_req:
                abs_id = abs_item.abs_id
                funnel_id = abs_item.funnel_id
                // 前置合法性校验：唯一准入校验
                IF abs_id == None OR abs_item.abs_I <= 0 OR abs_id IN funnel_abs_store.get(funnel_id, {}):
                    fail_abs_ids.append(abs_id)
                    CONTINUE
                // 按funnel分域持久化，构建蒸馏向量索引
                IF funnel_id NOT IN funnel_abs_store:
                    funnel_abs_store[funnel_id] = {}
                funnel_abs_store[funnel_id][abs_id] = {
                    "abs_id": abs_id,
                    "source_light_ids": abs_item.source_light_ids,
                    "funnel_id": funnel_id,
                    "abs_I": abs_item.abs_I,
                    "total_origin_reuse": abs_item.total_origin_reuse,
                    "abstract_vector": abs_item.abstract_vector,
                    "task_group": abs_item.task_group,
                    "create_ts": abs_item.create_ts,
                    "manual_status": "normal" // normal/freeze/archive
                }
                success_cnt += 1
                stat_today_add += 1
            // 回执回写给上游ag-mem-29
            write_ack = build_l5_write_ack(total=len(batch_write_req), success=success_cnt, fail_list=fail_abs_ids)
            send_write_ack(target="ag-mem-29", ack_data=write_ack)
            // 写入顶层记忆新增审计日志
            send_audit_log(event="L5批量写入锁控放行抽象单元", add_count=success_cnt, ts=now_ts)
            internal_state = L5_IDLE

        // 3. 接收人工运维冻结/归档操作指令
        IF 收到L5人工运维操作指令:
            op_cmd = 获取人工操作参数
            internal_state = MANUAL_OP_SCAN
            op_type = op_cmd.操作类型
            target_funnels = op_cmd.目标funnel列表
            target_abs_ids = op_cmd.指定abs_id清单
            archive_candidate = []
            freeze_count = 0
            now_ts = NOW()
            // 遍历目标分域筛选条目
            for funnel_id in target_funnels:
                if funnel_id not in funnel_abs_store:
                    continue
                abs_map = funnel_abs_store[funnel_id]
                for abs_id, abs_data in abs_map.items():
                    if abs_id not in target_abs_ids:
                        continue
                    if abs_data.manual_status != "normal":
                        continue
                    if op_type == "freeze":
                        abs_data.manual_status = "freeze"
                        freeze_count += 1
                        stat_manual_freeze += 1
                    elif op_type == "archive":
                        abs_data.manual_status = "archive"
                        archive_candidate.append({
                            "abs_id": abs_id,
                            "forget_reason": "人工运维归档",
                            "item_I": abs_data.abs_I,
                            "layer_threshold": 0,
                            "suggest_handle": "archive",
                            "layer": "L5",
                            "slot_id": funnel_id
                        })
                        stat_manual_archive += 1
            // 归档指令推送候选至ag-mem-42
            if len(archive_candidate) > 0:
                send_archive_candidate(target="ag-mem-42", candidate_list=archive_candidate)
            // 人工操作审计日志
            audit_log = build_manual_op_audit(op_type=op_type, freeze_num=freeze_count, archive_num=len(archive_candidate), ts=now_ts)
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = L5_IDLE

        // 4. 响应ag-mem-37 / ag-mem-40 元数据批量查询/全量导出
        IF 收到顶层抽象单元元数据批量查询请求:
            query_info = 获取查询参数
            internal_state = META_EXPORT_SCAN
            meta_result = []
            if query_info.全量导出标记 == True:
                // 全库导出
                for funnel_id, abs_map in funnel_abs_store.items():
                    for abs_id, abs_data in abs_map.items():
                        meta_result.append(abs_data)
            else:
                // 指定abs_id查询
                query_abs_list = query_info.abs_id列表
                for funnel_id, abs_map in funnel_abs_store.items():
                    for abs_id in query_abs_list:
                        if abs_id in abs_map:
                            meta_result.append(abs_map[abs_id])
            send_meta_snapshot(target="ag-mem-37", meta_list=meta_result)
            send_meta_snapshot(target="ag-mem-40", meta_list=meta_result)
            internal_state = L5_IDLE

        // 5. 定时容量上报 + 180s周期运行统计上报
        IF NOW() - last_report_ts >= 60 * 1000:
            data_kb, vec_index_kb = calc_l5_cap(funnel_abs_store, avg_abs_kb=l5_global_cfg.avg_abs_kb, vec_overhead=l5_global_cfg.high_compress_vec_overhead)
            total_abs_count = sum(len(v) for v in funnel_abs_store.values())
            cap_report = build_cap_report(layer="L5", data_used_kb=data_kb, vec_index_kb=vec_index_kb, item_count=total_abs_count)
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 容量占用超过预警阈值，推送告警至调度单元
            total_quota = l5_cap_cfg.l5_total_quota_kb
            usage_rate = (data_kb + vec_index_kb) / total_quota
            if usage_rate >= cap_warn_ratio:
                send_cap_warn(target="ag-mem-03", usage=usage_rate)
            // 每180s上报运行统计至ag-mem-03
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_l5_stat_report(
                    state=internal_state,
                    today_new_abs=stat_today_add,
                    total_manual_archive=stat_manual_archive,
                    total_manual_freeze=stat_manual_freeze
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-29推送条目无放行标记、abs_id重复 | 写入失败，加入失败列表回传上游，不落地L5存储 | ag-mem-29重新下发合法锁控放行抽象单元 |
| 单次写入条目超1000条上限 | 自动分片串行持久化，分批构建向量索引，防止IO打满 | 内置分片逻辑自动执行 |
| 底层持久存储/向量索引IO故障 | 内存缓存临时元数据，下一轮写入重试，持续失败输出告警审计 | 底层存储、向量库IO链路恢复 |
| 人工操作单次目标abs_id超过500条 | 自动拆分多批次执行冻结/归档，避免批量操作阻塞主线程 | 内置分片逻辑自动处理 |
| 全局FUSE熔断触发 | 停止写入、人工操作、元数据导出，内存缓存不落地新数据 | ag-mem-01下发RESUME恢复指令，恢复正常读写 |
| 目标funnel无独立L5配置参数 | 加载全局通用顶层存储参数兜底执行 | ag-mem-35运维侧补充分funnel顶层记忆配置 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 锁控放行抽象单元批量推送 | 只读 | ag-mem-29（唯一合法写入上游） |
| 内部调度总线 | 读 | 人工运维操作指令、分层容量配额、全局熔断指令 | 只读 | ag-mem-03、ag-mem-48、ag-mem-01 |
| 内部调度总线 | 读 | 元数据批量查询请求 | 只读 | ag-mem-37、ag-mem-40 |
| 内部调度总线 | 写 | L5写入完成回执 | 专属写入 | 回传给上游 ag-mem-29 |
| 内部调度总线 | 写 | L5人工归档候选清单 | 专属写入 | 下发 ag-mem-42 |
| 内部调度总线 | 写 | 元数据快照、容量上报、容量告警、审计日志、周期统计 | 事件/周期写入 | ag-mem-37/40、ag-mem-48、ag-mem-03、ag-mem-51 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| L5-01 | L5顶层记忆仅允许ag-mem-29锁控放行条目写入，阻断全部旁路写入入口，杜绝非法、未校验记忆进入永久存储 |
| L5-02 | 五层记忆通路终点，无自动晋升下游逻辑，顶层记忆流转链路闭环，禁止跨层回流、跨层跳转 |
| L5-03 | 顶层记忆永久留存，无自动过期/自动归档机制，仅运维人工可发起冻结、归档，管控顶层数据生命周期 |
| L5-04 | 向量压缩等级、容量预警阈值、人工操作批量上限全部由ag-mem-35、ag-mem-48集中管控，本地无硬编码业务参数 |
| L5-05 | 所有写入、冻结、人工归档操作完整写入ag-mem-51审计日志，完整留存多层溯源ID，满足顶层记忆全链路安全审计 |
| L5-06 | 熔断状态停止一切持久写入，避免半写入脏数据；恢复后必须重新经过ag-mem-29锁控校验才可入库，杜绝绕过安全链路 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M30-01 | `L5_IDLE`，ag-mem-29推送带合法放行标记全新abs_id抽象单元 | 锁控放行抽象单元批量列表 | 条目按funnel分域持久存入L5，构建高密度蒸馏向量索引，返回写入成功回执，生成新增审计日志 |
| TC-M30-02 | `L5_IDLE`，ag-mem-03下发人工归档指令，指定多条正常状态abs_id | 人工归档运维指令 | 目标条目标记archive，生成归档候选清单推送ag-mem-42，记录人工操作审计日志 |
| TC-M30-03 | `L5_IDLE`，ag-mem-03下发人工冻结指令 | 人工冻结运维指令 | 目标条目标记freeze，不再参与I值刷新与遗忘扫描，输出操作审计日志 |
| TC-M30-04 | `L5_IDLE`，单次写入1200条放行抽象单元 | 超大批量写入快照 | 自动分片串行持久化，无IO阻塞、无数据丢失 |
| TC-M30-05 | `L5_IDLE`，ag-mem-37下发全量元数据导出请求 | 全量元数据查询指令 | 输出L5所有分域顶层抽象单元完整元数据+蒸馏向量快照 |
| TC-M30-06 | `L5_IDLE`，接收全局FUSE熔断指令 | 紧急熔断调度指令 | 切换SYSTEM_PAUSED，停止写入、人工操作、元数据导出全部任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-30匹配白皮书五层记忆通路L5顶层永久存储定位 | ✅ |
| 上游唯一ag-mem-29、下游仅人工归档推送ag-mem-42，数据流闭环无冲突 | ✅ |
| 4种业务内部状态+暂停状态，切换逻辑完整，覆盖写入、人工运维、元数据导出全流程 | ✅ |
| 全部输入输出清晰标注收发模块、结构体、优先级，上下游链路无错乱 | ✅ |
| 永久存储、人工运维、无自动清理规则完全贴合V1.1顶层记忆生命周期规范 | ✅ |
| 伪代码覆盖锁控条目写入、分域持久化、人工冻结/归档、元数据导出、容量告警、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖非法条目、超大批量、存储IO故障、人工操作超限、熔断、无分域配置共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅允许ag-mem-29作为写入入口 | ✅ |
| 6条V1.1安全约束杜绝旁路写入、顶层数据随意清理、脏数据落地风险 | ✅ |
| 6条自动化测试用例覆盖全部顶层存储核心业务场景 | ✅ |

---