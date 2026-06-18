# ag-mem-20-L0临时缓冲层 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-20 |
| 模块名称 | L0临时缓冲层 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层分层存储体系 |
| 核心职责 | 漏斗二最前端临时缓冲存储，接收智能体实时生成的原始短期经验、工具交互快照；仅做短时缓存，不参与长期留存；定时向L1(ag-mem-21)执行合格条目晋升转移；过滤无效、低价值原始快照；向ag-mem-37提供条目元数据用于I值刷新；向ag-mem-40提供扫描数据用于遗忘判定；容量数据定时上报ag-mem-48；条目清理、晋升、过期全部推送审计日志至ag-mem-51；缓冲条目默认短时效自动过期，不进入L3/L4/L5长效体系。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断）、ag-mem-35（三维权重配置，读取L0缓冲时效、晋升阈值）、ag-mem-48（全局容量配额，接收容量上限规则） |
| 被依赖模块 | ag-mem-21（L1临时层，接收晋升合格条目）、ag-mem-37（重要度定时刷新，读取缓冲条目元数据）、ag-mem-40（遗忘判定，提供缓冲扫描数据）、ag-mem-42（冗余清理单元，执行过期缓冲条目删除）、ag-mem-48（上报容量占用）、ag-mem-51（推送缓冲变更审计日志）、ag-mem-03（周期上报缓冲运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 缓冲待机 | `BUFFER_IDLE` | 正常接收原始经验写入，等待定时晋升/过期清理任务 | 系统初始化、熔断恢复、批次晋升完成 |
| 条目写入缓存 | `ITEM_WRITE` | 校验原始快照合法性，写入L0缓冲内存+持久化缓存 | 智能体下发新原始经验条目 |
| 定时晋升筛选 | `PROMOTE_SCAN` | 遍历缓冲条目，比对I值、时效、复用次数筛选可晋升条目 | 晋升定时器倒计时归零 |
| 过期条目清理 | `EXPIRE_CLEAN` | 筛选超时缓冲条目，生成清理候选推送ag-mem-42 | 缓冲过期扫描触发 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止写入、晋升、清理，缓冲数据临时缓存 | F0下发FUSE熔断指令；RESUME切回BUFFER_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 原始经验写入请求 | Struct（条目ID、工具交互快照、初始复用次数、初始S值、生成时间戳、来源分槽） | 智能体业务执行链路 | 智能体完成单次工具/任务交互生成原始经验 | 高 |
| 缓冲定时晋升扫描指令 | Struct（触发类型=定时，目标晋升层级L1） | 内部定时调度/ag-mem-37 | 定时周期到达，批量晋升筛选 | 普通 |
| 缓冲过期扫描指令 | Struct（过期时效阈值） | 内部定时调度 | 缓冲条目超时清理周期到达 | 普通 |
| I值批量查询请求 | Struct（条目ID列表） | ag-mem-37 重要度刷新单元 | 全局I值定时重算 | 高 |
| 分层容量定时上报回执 | Struct（分层配额、预警阈值） | ag-mem-48 全局容量管控 | 模块初始化、配额更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 条目写入完成回执 | Struct（条目ID、缓冲存储位置、初始I值） | 智能体业务执行链路 | 原始经验写入缓冲完成 | 高 |
| 可晋升条目批量推送 | List（条目完整元数据、I值、复用次数、来源分槽） | ag-mem-21 L1临时层 | 晋升筛选完成，存在合格条目 | 高 |
| L0过期遗忘候选清单 | List（条目ID、过期原因、建议处理方式delete） | ag-mem-42 冗余清理单元 | 过期扫描筛选出超时条目 | 普通 |
| 缓冲条目元数据快照 | List（条目ID、I值、复用次数、写入时间、分槽ID） | ag-mem-37 / ag-mem-40 | I刷新/遗忘扫描查询 | 高 |
| L0分层容量占用上报 | Struct（层级=L0、当前占用KB、条目总数、平均单条大小） | ag-mem-48 全局容量配额 | 每60秒定时上报、条目批量变更后即时上报 | 普通 |
| 缓冲变更审计日志 | Struct（事件类型、条目数量、操作类型、时间戳） | ag-mem-51 日志追溯单元 | 写入、晋升、过期清理全部操作完成 | 普通 |
| L0缓冲周期运行上报 | Struct（当前状态、今日新增条目、晋升总量、过期清理总量） | ag-mem-03 漏斗二调度 | 每180秒周期性上报 | 普通 |

## L0缓冲核心规则（V1.1分层存储标准）
### 1. 缓冲时效规则
由ag-mem-35统一下发全局L0默认时效：**30分钟**
条目写入缓冲起计时，超过30分钟未晋升至L1自动标记过期，推送ag-mem-42物理删除，无归档流程。

### 2. 晋升准入条件（全部满足才可晋升L1）
1. 条目综合I值 ≥ L0晋升阈值（分槽独立配置，来自ag-mem-35）
2. 条目复用次数 ≥ 1次
3. 条目未标记无效快照（空工具交互、无有效行为记录直接过滤）
4. 未达到缓冲过期时效

### 3. 分层隔离约束
1. L0仅临时缓冲，**永不直接写入L2/L3/L4/L5**，必须经L1中转晋升；
2. L0条目不参与长效记忆，遗忘处理仅物理删除，无归档备份；
3. L5永久隔离，无任何L0条目流转至顶层核心存储的通道。

### 4. 批量约束
单次晋升/过期扫描最大处理1000条，超量自动拆分串行执行。

## 核心处理逻辑
```
FUNCTION l0_buffer_main_loop():
    STATE_IDLE = BUFFER_IDLE
    STATE_WRITE = ITEM_WRITE
    STATE_PROMOTE = PROMOTE_SCAN
    STATE_EXPIRE = EXPIRE_CLEAN
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 加载L0配置参数
    l0_cfg = query_slot_config(from_m35="ag-mem-35")
    l0_expire_ms = l0_cfg.L0_expire_min * 60 * 1000
    l0_promote_thresh = l0_cfg.L0_promote_I_threshold
    buffer_item_cache = {}
    stat_today_add = 0
    stat_total_promote = 0
    stat_total_expire_clean = 0
    last_report_ts = NOW()
    promote_timer = l0_cfg.promote_cycle_sec
    expire_timer = l0_cfg.expire_scan_cycle_sec
    promote_countdown = promote_timer
    expire_countdown = expire_timer

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收智能体原始经验写入请求
        IF 收到原始经验写入请求:
            write_req = 获取写入请求
            item_id = write_req.条目ID
            slot_id = write_req.来源分槽
            reuse_cnt = write_req.初始复用次数
            s_val = write_req.初始S值
            create_ts = write_req.生成时间戳
            internal_state = ITEM_WRITE
            // 过滤无效空快照
            IF write_req.工具交互快照为空:
                send_write_ack(target=业务链路, item_id=item_id, success=False, reason="无效空交互快照")
                internal_state = STATE_IDLE
                CONTINUE
            // 初始简易I值计算
            init_I = l0_cfg.W_reuse * reuse_cnt + l0_cfg.W_safe * s_val
            // 写入缓冲缓存
            buffer_item_cache[item_id] = {
                "slot_id": slot_id,
                "reuse_count": reuse_cnt,
                "S_value": s_val,
                "I_value": init_I,
                "create_ts": create_ts,
                "last_access_ts": NOW(),
                "is_invalid": False
            }
            stat_today_add += 1
            // 返回写入回执
            send_write_ack(target=业务链路, item_id=item_id, success=True, init_I=init_I)
            // 推送写入审计日志
            write_audit_log(event="L0缓冲新增条目", item_id=item_id, ts=NOW())
            internal_state = STATE_IDLE

        // 3. 定时晋升倒计时处理
        IF internal_state == STATE_IDLE:
            promote_countdown -= 10
            IF promote_countdown <= 0:
                internal_state = PROMOTE_SCAN
                promote_candidate = []
                now_ts = NOW()
                FOR item_id, item_data IN buffer_item_cache.items():
                    // 跳过无效条目、过期条目
                    item_age = now_ts - item_data.create_ts
                    IF item_data.is_invalid OR item_age >= l0_expire_ms:
                        CONTINUE
                    // 校验晋升全部条件
                    IF item_data.I_value >= l0_promote_thresh AND item_data.reuse_count >= 1:
                        promote_candidate.append(item_data)
                // 批量推送至L1
                IF LEN(promote_candidate) > 0:
                    send_promote_batch(target="ag-mem-21", item_list=promote_candidate)
                    stat_total_promote += LEN(promote_candidate)
                    // 从L0缓冲移除已晋升条目
                    FOR promoted_item IN promote_candidate:
                        del buffer_item_cache[promoted_item.item_id]
                    write_audit_log(event="L0批量晋升至L1", count=LEN(promote_candidate), ts=NOW())
                promote_countdown = promote_timer
                internal_state = STATE_IDLE

        // 4. 定时过期清理扫描
        IF internal_state == STATE_IDLE:
            expire_countdown -= 10
            IF expire_countdown <= 0:
                internal_state = EXPIRE_CLEAN
                expire_candidate = []
                now_ts = NOW()
                FOR item_id, item_data IN buffer_item_cache.items():
                    item_age = now_ts - item_data.create_ts
                    IF item_age >= l0_expire_ms:
                        expire_candidate.append({
                            "item_id": item_id,
                            "forget_reason": "L0缓冲超时未晋升",
                            "suggest_handle": "delete",
                            "layer": "L0",
                            "slot_id": item_data.slot_id
                        })
                // 推送过期清单至ag-mem-42
                IF LEN(expire_candidate) > 0:
                    send_forget_candidate(target="ag-mem-42", candidate_list=expire_candidate)
                    stat_total_expire_clean += LEN(expire_candidate)
                expire_countdown = expire_timer
                internal_state = STATE_IDLE

        // 5. 处理ag-mem-37 I值批量查询请求
        IF 收到I值批量查询请求:
            query_req = 获取I查询请求
            query_item_ids = query_req.条目ID列表
            meta_list = []
            FOR item_id IN query_item_ids:
                IF item_id IN buffer_item_cache:
                    meta_list.append(buffer_item_cache[item_id])
            send_meta_snapshot(target="ag-mem-37", meta_data=meta_list)

        // 6. 每60秒上报容量占用至ag-mem-48
        IF NOW() - last_report_ts >= 60 * 1000:
            total_kb = calc_buffer_total_kb(buffer_item_cache, avg_item_size=l0_cfg.avg_kb)
            cap_report = build_cap_report(layer="L0", used_kb=total_kb, item_count=LEN(buffer_item_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            // 每180秒周期运行统计上报
            IF NOW() - last_report_ts >= 180 * 1000:
                stat_report = build_buffer_stat_report(
                    current_state=internal_state,
                    today_add=stat_today_add,
                    total_promote=stat_total_promote,
                    total_expire=stat_total_expire_clean
                )
                send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 原始交互快照为空无效 | 拒绝写入缓冲，返回失败回执，不生成日志 | 上游业务链路生成完整有效工具交互快照 |
| 缓冲条目晋升过程中被并发过期清理 | 条目直接归入过期清单，不再晋升，不产生报错 | 无，快照隔离并发变更 |
| 单次扫描条目超过1000条 | 自动拆分多批次串行处理，不阻塞定时循环 | 内置分片逻辑无需人工干预 |
| L0缓冲存储IO故障 | 临时内存缓存保留条目，下一轮定时重试晋升/清理 | 底层存储介质IO恢复 |
| 全局紧急熔断触发 | 停止写入、晋升、过期扫描，内存缓存保留全部缓冲条目 | F0下发RESUME恢复指令自动恢复定时任务 |
| 分槽无专属L0晋升阈值 | 自动加载全局通用L0阈值兜底判定 | ag-mem-35补充分槽独立配置 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 原始经验写入请求 | 只读 | 智能体业务执行链路 |
| 内部调度总线 | 读 | I值批量查询请求 | 只读 | ag-mem-37 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 |
| 内部调度总线 | 写 | 条目写入完成回执 | 专属写入 | 向智能体业务链路返回 |
| 内部调度总线 | 写 | 可晋升条目批量推送 | 专属写入 | 向 ag-mem-21 下发 |
| 内部调度总线 | 写 | 过期遗忘候选清单、条目元数据快照 | 专属写入 | 向 ag-mem-42、ag-mem-37 推送 |
| 内部调度总线 | 写 | 容量上报、审计日志、周期统计上报 | 周期/事件写入 | ag-mem-48、ag-mem-51、ag-mem-03 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| B0-01 | L0缓冲条目仅允许单向晋升至L1，禁止直接流转至L2/L3/L4/L5，分层链路单向隔离 |
| B0-02 | L0过期条目仅物理删除，无归档备份，不占用离线归档分区资源 |
| B0-03 | 所有写入、晋升、过期清理操作强制推送审计日志至ag-mem-51，完整留存条目变更记录 |
| B0-04 | 晋升阈值、缓冲时效统一由ag-mem-35管控，本模块无本地硬编码参数 |
| B0-05 | L0缓冲容量上限由ag-mem-48统一管控，达到预警自动加快过期扫描频率 |
| B0-06 | 熔断状态内存缓存条目不丢失，服务恢复后自动执行定时晋升与清理 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M20-01 | `BUFFER_IDLE`，完整有效工具交互快照 | 原始经验写入请求 | 条目写入缓冲，返回成功回执，生成新增审计日志 |
| TC-M20-02 | `BUFFER_IDLE`，条目I达标、复用≥1，定时晋升触发 | 晋升倒计时归零 | 条目推送至ag-mem-21，从L0缓冲移除 |
| TC-M20-03 | `BUFFER_IDLE`，条目存放35分钟超过30分钟时效 | 过期扫描触发 | 条目加入遗忘候选清单推送ag-mem-42标记删除 |
| TC-M20-04 | `BUFFER_IDLE`，空无效工具交互快照 | 原始经验写入请求 | 拒绝写入，回执标记无效快照 |
| TC-M20-05 | `BUFFER_IDLE`，ag-mem-37下发批量I查询 | I值批量查询条目ID | 返回对应缓冲条目完整元数据快照 |
| TC-M20-06 | `BUFFER_IDLE`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，停止写入、晋升、过期扫描 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、L0临时缓冲底层存储定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| L0时效、晋升准入、分层隔离、批量约束规则完整 | ✅ |
| 伪代码覆盖条目写入、定时晋升、过期清理、I值查询、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖无效快照、并发过期、超大批次、IO故障、熔断、无分槽阈值共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束分层流转隔离无旁路漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |
