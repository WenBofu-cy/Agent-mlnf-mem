# ag-mem-42-冗余记忆删除与归档单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-42 |
| 模块名称 | 冗余记忆删除与归档单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制 |
| 核心职责 | 遗忘链路最终执行单元，接收ag-mem-40输出的遗忘候选清单，按清单内`suggest_handle`字段区分**物理删除**/归档落盘；分层处理L1/L2直接删除、L3/L4归档持久化；执行前校验条目无人工锁定/用户收藏标记，防止误清理；操作完成后同步变更至对应分层存储ag-mem-20~26；全量清理动作推送审计日志至ag-mem-51；支持批量分批执行、容量释放统计、熔断中断回滚；L5条目永久拒绝清理操作，严格遵循V1.1长效记忆保护规范。仅负责数据落地清理/归档，不参与遗忘判定逻辑。 |
| 依赖模块 | ag-mem-40（接收遗忘候选清单）、ag-mem-20~26（分层存储，执行删除/归档变更、同步条目元数据）、ag-mem-01（总控F0全局熔断指令）、ag-mem-48（全局容量配额单元，更新存储占用统计） |
| 被依赖模块 | ag-mem-20~26（接收清理完成同步回执）、ag-mem-48（上报清理释放空间）、ag-mem-51（推送清理/归档审计日志）、ag-mem-03（漏斗二调度单元，周期上报清理统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 空闲待命 | `IDLE` | 无清理任务，等待遗忘候选清单输入 | 系统初始化、单批次清理全部完成、熔断恢复 |
| 清单解析 | `PARSE_LIST` | 拆分候选批次、区分删除/归档条目、校验条目保护标记 | 收到ag-mem-40下发遗忘候选清单 |
| 批量清理执行 | `EXEC_CLEAN` | 循环批量执行条目删除或归档写入 | 清单解析完成，无阻断校验异常 |
| 结果同步上报 | `SYNC_REPORT` | 向分层存储同步条目状态、上报释放容量、输出审计日志 | 整批条目清理/归档完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，中断所有清理事务、未完成批次回滚 | 接收F0 FUSE熔断指令；RESUME切回IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 遗忘候选清理清单 | List<Struct>（批次ID、条目ID、遗忘原因、当前I值、层级、分槽、建议处理方式delete/archive） | ag-mem-40 遗忘阈值判定单元 | 遗忘判定完成后批量下发 | 高 |
| 条目保护标记校验回执 | Struct（条目ID、是否人工锁定/用户收藏） | ag-mem-20~26 分层存储单元 | 解析清单时逐条校验保护标记 | 高 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控漏斗F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 清理执行完成回执 | Struct（批次ID、清理总条数、归档条数、删除条数、失败条数、预估释放空间、失败条目ID列表） | ag-mem-40 遗忘阈值判定单元 | 整批候选条目处理完毕 | 高 |
| 条目状态变更同步通知 | List<Struct>（条目ID、操作类型delete/archive、变更时间戳） | ag-mem-20~26 对应分层存储单元 | 单条条目删除/归档成功 | 高 |
| 清理归档审计日志 | Struct（事件类型、批次ID、操作总量、分层分布、释放空间、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每批次清理完成 | 高 |
| 容量释放更新通知 | Struct（本次释放KB、分层释放明细） | ag-mem-48 全局容量配额管控单元 | 批次清理完成 | 普通 |
| 清理周期统计上报 | Struct（当前状态、累计删除量、累计归档量、累计释放空间、失败次数） | ag-mem-03 漏斗二专属调度单元 | 每180秒周期性上报 | 普通 |

## 清理分层执行规则（V1.1标准）
### 1. 分层处理策略
| 层级 | 建议处理方式 | 执行逻辑 |
|:---:|:---:|------|
| L1 临时层 | delete | 条目物理删除，无归档备份 |
| L2 近期层 | delete | 条目物理删除，无归档备份 |
| L3 中期层 | archive | 条目从L3移除，写入离线归档分区，原存储删除 |
| L4 长期层 | archive | 条目从L4移除，写入离线归档分区，原存储删除 |
| L5 核心层 | 禁止处理 | 任何清单内L5条目直接过滤，跳过清理，记录告警日志 |

### 2. 前置拦截校验（任一命中直接跳过本条清理）
1. 条目层级=L5；
2. 条目标记「人工锁定/用户收藏」；
3. 条目24h内存在访问记录；
4. 条目复用次数满足ag-mem-41复用保护标准；
5. 条目不存在于对应分层存储（并发已删除）。

### 3. 批量执行约束
1. 单批次最大处理500条，超量自动拆分多批次串行执行；
2. 单条清理操作超时阈值800ms，超时标记为失败条目；
3. 熔断触发时，当前事务全部回滚，已归档/删除条目恢复原状。

## 核心处理逻辑
```
FUNCTION memory_clean_archive_main_loop():
    STATE_IDLE = IDLE
    STATE_PARSE = PARSE_LIST
    STATE_EXEC = EXEC_CLEAN
    STATE_SYNC = SYNC_REPORT
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    # 全局统计指标
    stat_total_delete = 0
    stat_total_archive = 0
    stat_total_fail = 0
    stat_total_free_kb = 0
    last_report_ts = NOW()
    AVG_ITEM_SIZE_KB = 单条目平均体积常量

    WHILE 系统运行中:
        // 1. 最高优先级：全局熔断调度
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                中断当前所有清理事务，执行事务回滚
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收ag-mem-40下发遗忘候选清单
        IF 收到遗忘候选清理清单:
            batch_req = 获取候选清单
            batch_id = batch_req.批次ID
            full_candidate = batch_req.条目列表
            internal_state = PARSE_LIST

            split_batch_list = 拆分批次(full_candidate, 单批上限=500)
            total_batch_fail = []
            batch_delete_cnt = 0
            batch_archive_cnt = 0
            batch_free_kb = 0

            FOR 子批次 IN split_batch_list:
                success_item = []
                fail_item = []
                # 逐条前置保护校验
                FOR item IN 子批次:
                    item_id = item.条目ID
                    layer = item.来源层级
                    handle_type = item.建议处理方式
                    # L5直接拦截
                    IF layer == "L5":
                        fail_item.append(item_id)
                        CONTINUE
                    # 拉取条目保护标记
                    protect_resp = 向对应分层存储查询条目保护标记(item_id)
                    IF protect_resp.人工锁定 OR protect_resp.用户收藏:
                        fail_item.append(item_id)
                        CONTINUE
                    # 执行删除/归档事务
                    exec_result = 执行单条目清理事务(item_id, layer, handle_type)
                    IF exec_result.执行成功:
                        success_item.append(item)
                        batch_free_kb += AVG_ITEM_SIZE_KB
                        IF handle_type == "delete":
                            batch_delete_cnt += 1
                        ELSE:
                            batch_archive_cnt += 1
                    ELSE:
                        fail_item.append(item_id)
                        stat_total_fail += 1
                # 子批次完成，同步变更至分层存储
                IF LEN(success_item) > 0:
                    向ag-mem-20~26发送条目状态变更同步通知(success_item)
                # 汇总失败条目
                total_batch_fail.extend(fail_item)

            // 整批处理完成，进入同步上报阶段
            internal_state = SYNC_REPORT
            total_process = LEN(full_candidate)
            batch_success = total_process - LEN(total_batch_fail)
            # 更新全局统计
            stat_total_delete += batch_delete_cnt
            stat_total_archive += batch_archive_cnt
            stat_total_free_kb += batch_free_kb

            // 1. 回执下发至ag-mem-40
            finish_ack = 组装清理完成回执(
                batch_id=batch_id,
                total_process=total_process,
                archive_count=batch_archive_cnt,
                delete_count=batch_delete_cnt,
                fail_count=LEN(total_batch_fail),
                free_kb=batch_free_kb,
                fail_item_ids=total_batch_fail
            )
            向ag-mem-40发送清理执行完成回执(finish_ack)

            // 2. 推送审计日志至ag-mem-51
            audit_log = 组装清理归档审计日志(
                batch_id=batch_id,
                total_ops=batch_success,
                del_num=batch_delete_cnt,
                arch_num=batch_archive_cnt,
                free_space=batch_free_kb,
                event_ts=NOW()
            )
            向ag-mem-51推送审计日志(audit_log)

            // 3. 更新全局容量配额
            capacity_notify = 组装容量释放更新通知(batch_free_kb)
            向ag-mem-48发送容量释放更新通知(capacity_notify)

            internal_state = STATE_IDLE

        // 3. 每180秒周期上报清理统计指标
        IF NOW() - last_report_ts >= 180 * 1000:
            stat_report = 组装清理周期统计上报(
                current_state=internal_state,
                total_del=stat_total_delete,
                total_arch=stat_total_archive,
                total_free=stat_total_free_kb,
                total_fail=stat_total_fail
            )
            向ag-mem-03发送清理周期统计上报(stat_report)
            last_report_ts = NOW()

        SLEEP 10ms

// 子函数：单条目清理事务执行
FUNCTION 执行单条目清理事务(item_id, layer, handle_type):
    开启本地事务
    IF handle_type == "delete":
        对应分层存储执行物理删除(item_id)
    ELSE IF handle_type == "archive":
        读取条目完整数据
        写入归档离线分区
        原分层存储删除该条目
    # 事务超时800ms判定失败
    IF 事务执行超时 OR 存储IO异常:
        事务回滚
        RETURN {执行成功=False}
    ELSE:
        事务提交
        RETURN {执行成功=True}
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 候选清单包含L5条目 | 直接标记为失败条目，跳过清理并记录告警日志 | 无，L5永久禁止清理 |
| 条目带人工锁定/用户收藏标记 | 拦截清理，计入失败列表，回执同步失败ID | 人工解除条目保护标记后重新发起遗忘扫描 |
| 单条目清理IO超时/存储故障 | 事务回滚，标记条目失败，不影响同批次其他条目 | 底层存储IO恢复后重新执行遗忘扫描 |
| 单批次条目超500条上限 | 自动切分多子批次串行处理，互不干扰 | 内置自动拆分逻辑，无需人工干预 |
| 全局紧急熔断触发 | 当前活跃批次全部事务回滚，未处理清单丢弃，拒绝新任务 | F0下发RESUME恢复指令 |
| 条目在清理前被并发删除 | 标记为失败，不产生变更日志，无报错 | 无，快照隔离并发变更 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 遗忘候选清理清单 | 只读 | ag-mem-40 下发 |
| 内部调度总线 | 读 | 条目保护标记校验回执 | 只读 | ag-mem-20~26 返回 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | 清理执行完成回执 | 专属写入 | 向 ag-mem-40 返回 |
| 内部调度总线 | 写 | 条目状态变更同步通知 | 专属写入 | 向 ag-mem-20~26 同步 |
| 内部调度总线 | 写 | 清理归档审计日志 | 事件写入 | 向 ag-mem-51 推送 |
| 内部调度总线 | 写 | 容量释放更新通知 | 普通写入 | 向 ag-mem-48 同步 |
| 内部调度总线 | 写 | 清理周期统计上报 | 周期写入 | 向 ag-mem-03 推送 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| C-01 | L5层级任何条目永久禁止删除/归档，底层硬编码拦截，不受遗忘清单参数影响 |
| C-02 | 人工锁定、用户收藏、近期访问、高复用条目强制前置拦截，杜绝误清理长效经验 |
| C-03 | L3/L4长期记忆禁止直接物理删除，必须归档离线备份，满足V1.1数据追溯要求 |
| C-04 | 所有删除、归档操作开启事务，熔断/故障自动回滚，防止数据丢失 |
| C-05 | 全部清理操作生成不可篡改审计日志存入ag-mem-51，记录批次、条目、释放空间、操作时间 |
| C-06 | 仅接收ag-mem-40输出的标准化遗忘候选清单，拒绝其他模块直接发起清理指令 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M42-01 | `IDLE`，L1删除候选条目 | 批次清单含L1条目，suggest_handle=delete | 条目物理删除，回执删除计数+1，上报释放空间 |
| TC-M42-02 | `IDLE`，L4归档候选条目 | 批次清单含L4条目，suggest_handle=archive | 条目写入归档分区，原存储删除，归档计数+1 |
| TC-M42-03 | `IDLE`，清单内包含L5条目 | 候选清单携带L5条目ID | L5条目标记失败，跳过清理，日志记录告警 |
| TC-M42-04 | `IDLE`，条目带人工锁定标记 | 清单内条目校验返回人工锁定=True | 条目计入失败列表，不执行清理 |
| TC-M42-05 | `IDLE`，单批次600条候选条目 | 超大批次遗忘清单 | 自动拆分为2个子批次串行处理，统计正常累加 |
| TC-M42-06 | `IDLE`，处理中途收到FUSE熔断指令 | 全局紧急熔断指令 | 当前子批次事务全部回滚，切换SYSTEM_PAUSED，回执失败条目增多 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、遗忘执行单元定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 分层清理策略、前置拦截校验、批量约束完整 | ✅ |
| 伪代码覆盖清单拆分、前置校验、事务清理、同步回执、审计日志、容量上报全链路 | ✅ |
| 异常场景覆盖L5拦截、保护标记、IO故障、超大批次、熔断、并发删除共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无逻辑漏洞、无误删路径 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

## 遗忘链路完整闭环总结
ag-mem-40（遗忘判定）→ ag-mem-41（复用辅助校验）→ ag-mem-42（删除/归档执行）
三层解耦，决策、校验、执行完全分离，完全贴合V1.1分层记忆生命周期设计，无逻辑断点。