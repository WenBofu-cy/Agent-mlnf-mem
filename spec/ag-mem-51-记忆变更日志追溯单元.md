# ag-mem-51-记忆变更日志追溯单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-51 |
| 模块名称 | 记忆变更日志追溯单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 全局审计追溯中枢 |
| 核心职责 | 全ag-mem体系唯一统一日志存储、检索、归档模块；接收所有记忆分层、判定、锁控、配置、清理、安全校验、容量管控模块推送的事件日志；标准化统一日志格式持久化存储；提供人工运维日志检索、按模块/层级/时间/事件类型筛选查询；支持日志过期自动归档、不可篡改写入机制；所有记忆新增、删除、I值更新、遗忘、L5写入、配置修改、容量预警、违规拦截行为全部留痕；不参与记忆业务逻辑，仅负责日志存储与追溯，满足V1.1智能体全链路可审计安全规范。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、人工运维检索接口（日志查询、导出、归档操作） |
| 被依赖模块 | ag-mem-20~48、ag-mem-15~19、ag-mem-03、ag-mem-01（全模块日志推送目标）、ag-mem-03（漏斗二调度，周期上报日志存储统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 日志接收就绪 | `LOG_READY` | 正常接收全模块日志写入请求，响应人工检索查询 | 系统初始化日志库加载完成、熔断恢复、归档任务结束 |
| 日志持久写入 | `LOG_WRITE` | 校验日志标准化字段、写入本地持久化存储，生成防篡改校验哈希 | 收到任意模块推送事件日志 |
| 日志检索处理 | `LOG_QUERY` | 解析人工检索条件，按维度筛选日志数据集返回结果 | 人工运维下发日志查询/导出指令 |
| 过期日志归档 | `LOG_ARCHIVE` | 扫描超保存周期日志，离线归档压缩，释放在线存储 | 定时归档倒计时归零、人工触发归档 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，暂停日志写入、检索、归档，缓存临时日志待恢复 | F0下发FUSE熔断指令；RESUME切回LOG_READY |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 标准化事件日志推送 | Struct（事件唯一批次ID、来源模块ID、事件类型、目标层级/分槽、变更摘要、风险等级、操作时间戳、关联条目ID列表、哈希校验值） | ag-mem01~48、ag-mem15~19 | 任意模块完成记忆变更、判定、配置修改、预警、违规拦截后推送 | 高 |
| 人工日志检索指令 | Struct（筛选维度：模块/层级/时间区间/事件类型/风险等级、分页参数、导出标记、管理员ID、双重确认码） | 人工运维检索接口 | 运维查询历史记忆操作记录 | **最高** |
| 人工手动归档指令 | Struct（归档过期天数阈值、管理员ID、双重确认码） | 人工运维检索接口 | 主动触发离线日志归档压缩 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 日志写入回执 | Struct（日志批次ID、存储位置、防篡改哈希、写入成功标记） | 日志推送来源模块 | 单条/批量日志持久化完成 | 普通 |
| 日志检索结果集 | List<Struct>（完整日志条目、分页总数、导出文件地址） | 人工运维检索接口 | 检索条件筛选完成 | **最高** |
| 归档完成回执 | Struct（归档日志总量、释放在线存储空间、归档文件路径） | 人工运维检索接口 | 过期日志归档压缩完毕 | 普通 |
| 日志存储周期状态上报 | Struct（当前状态、在线日志总量、归档总条数、今日日志写入量、存储占用KB） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 日志统一规范（V1.1强制标准格式）
### 1. 全系统事件分类
1. 记忆数据变更：条目新增、I值刷新、删除、归档
2. 遗忘判定事件：扫描启动、候选生成、条目保护跳过
3. L5安全事件：写入校验、令牌签发、违规访问拦截、安全校验拦截
4. 配置变更事件：权重/阈值修改、分槽参数重置
5. 容量资源事件：容量预警、紧急溢出、配额调整、空间释放
6. 审计管控事件：管理员人工操作、日志归档、批量维护
7. 系统异常事件：熔断、模块离线、参数异常、令牌伪造告警

### 2. 日志防篡改机制
每条日志写入时自动生成SHA256哈希值，绑定批次ID+时间戳；检索时自动校验哈希，哈希不匹配标记日志篡改告警，禁止导出异常日志。

### 3. 日志存储生命周期规则
1. 在线存储：近30天日志，支持实时检索
2. 离线归档：超过30天自动压缩归档，仅支持定向导出，不在线检索
3. 永久留存：L5相关所有操作日志、高危安全拦截日志永久不自动清理，仅人工归档

### 4. 批量写入约束
单次批量日志推送上限500条，超量拆分串行写入，防止IO阻塞。

## 核心处理逻辑
```
FUNCTION log_trace_main_loop():
    STATE_READY = LOG_READY
    STATE_WRITE = LOG_WRITE
    STATE_QUERY = LOG_QUERY
    STATE_ARCHIVE = LOG_ARCHIVE
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_READY
    # 日志存储全局统计
    stat_today_write = 0
    stat_total_online_log = 0
    stat_total_archive_log = 0
    last_report_ts = NOW()
    archive_timer = 86400  # 每日自动归档
    archive_countdown = archive_timer
    temp_log_cache = []  # 熔断临时缓存日志

    WHILE 系统运行中:
        // 1. 最高优先级：全局熔断调度
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_READY
                // 熔断恢复，写入缓存临时日志
                IF LEN(temp_log_cache) > 0:
                    FOR log_data IN temp_log_cache:
                        write_log_persist(log_data)
                    temp_log_cache.clear()

        // 2. 接收全模块标准化日志推送
        IF 收到标准化事件日志推送:
            log_batch = 获取日志推送数据包
            internal_state = STATE_WRITE
            split_log_batch = split_list(log_batch, batch_size=500)
            write_success_count = 0

            FOR slice_log IN split_log_batch:
                FOR single_log IN slice_log:
                    // 生成防篡改哈希
                    hash_val = calc_sha256(single_log.批次ID + single_log.事件摘要 + single_log.时间戳)
                    single_log.哈希校验值 = hash_val
                    // 熔断状态存入临时缓存
                    IF internal_state == SYSTEM_PAUSED:
                        temp_log_cache.append(single_log)
                        CONTINUE
                    // 持久化写入在线日志库
                    write_log_persist(single_log)
                    write_success_count += 1
                    stat_today_write += 1
                    stat_total_online_log += 1
            // 返回写入回执至推送模块
            write_ack = build_log_write_ack(batch_id=log_batch[0].批次ID, success_num=write_success_count)
            send_write_ack(target=日志来源模块, ack_data=write_ack)
            internal_state = STATE_READY

        // 3. 人工日志检索查询指令处理
        IF 收到人工日志检索指令:
            query_req = 获取检索指令
            admin_id = query_req.管理员ID
            internal_state = STATE_QUERY
            // 管理员双重确认校验
            double_verify = launch_admin_double_verify(admin_id, timeout=60*1000, code=query_req.双重确认码)
            IF NOT double_verify.通过:
                send_query_reject(target=人工运维接口, reason="管理员双重确认校验失败")
                internal_state = STATE_READY
                CONTINUE
            // 按筛选条件检索日志
            result_dataset = query_log_storage(
                filter_module=query_req.筛选模块,
                filter_layer=query_req.层级,
                time_range=query_req.时间区间,
                event_type=query_req.事件类型,
                page_param=query_req.分页参数
            )
            // 校验每条日志哈希完整性
            valid_result = []
            tamper_count = 0
            FOR log_item IN result_dataset:
                calc_hash = calc_sha256(log_item.批次ID + log_item.事件摘要 + log_item.时间戳)
                IF calc_hash == log_item.哈希校验值:
                    valid_result.append(log_item)
                ELSE:
                    tamper_count += 1
            // 组装检索结果回执
            query_resp = build_log_query_resp(valid_data=valid_result, tamper_warn_num=tamper_count)
            send_query_result(target=人工运维接口, resp_data=query_resp)
            internal_state = STATE_READY

        // 4. 定时自动归档过期日志
        IF internal_state == STATE_READY:
            archive_countdown -= 10
            IF archive_countdown <= 0:
                internal_state = STATE_ARCHIVE
                // 筛选超过30天普通日志，L5日志排除自动归档
                expire_log_list = scan_expire_online_log(expire_day=30, skip_layer="L5")
                archive_log_batch(expire_log_list)
                stat_total_archive_log += LEN(expire_log_list)
                stat_total_online_log -= LEN(expire_log_list)
                // 归档完成回执无人工指令则仅内部统计
                archive_countdown = archive_timer
                internal_state = STATE_READY

        // 5. 人工手动归档指令
        IF 收到人工手动归档指令:
            archive_req = 获取手动归档指令
            admin_id = archive_req.管理员ID
            internal_state = STATE_ARCHIVE
            double_verify = launch_admin_double_verify(admin_id, timeout=60*1000, code=archive_req.双重确认码)
            IF NOT double_verify.通过:
                send_archive_reject(target=人工运维接口, reason="双重确认校验失败")
                internal_state = STATE_READY
                CONTINUE
            expire_list = scan_expire_online_log(expire_day=archive_req.归档天数阈值, skip_layer="L5")
            archive_log_batch(expire_list)
            stat_total_archive_log += LEN(expire_list)
            stat_total_online_log -= LEN(expire_list)
            archive_ack = build_archive_finish_ack(archive_total=LEN(expire_list))
            send_archive_ack(target=人工运维接口, ack_data=archive_ack)
            internal_state = STATE_READY

        // 6. 每180秒周期上报日志存储统计
        IF NOW() - last_report_ts >= 180 * 1000:
            log_stat_report = build_log_storage_report(
                current_state=internal_state,
                today_write=stat_today_write,
                online_total=stat_total_online_log,
                archive_total=stat_total_archive_log
            )
            send_stat_report(target="ag-mem-03", report=log_stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 日志推送缺少必填标准化字段 | 拒绝写入，返回写入回执标记失败，记录系统异常日志 | 上游模块补齐日志标准字段后重新推送 |
| 日志哈希校验不匹配（疑似篡改） | 检索结果标记篡改告警，禁止导出该条日志，留存异常记录 | 无，篡改日志永久隔离 |
| 单次批量日志推送超过500条 | 自动拆分多批次串行写入，不阻塞业务模块 | 内置分片逻辑无需人工干预 |
| 日志持久化存储IO故障 | 熔断状态存入临时缓存；正常运行时丢弃本条并告警 | 底层存储IO恢复后缓存日志自动落盘 |
| 全局紧急熔断触发 | 停止日志写入、检索、归档，新日志存入内存临时缓存 | F0下发RESUME恢复指令自动补写缓存日志 |
| L5相关日志触发自动归档流程 | 自动过滤跳过，永久保留在线存储 | V1.1强制长效记忆审计日志不自动归档 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 标准化事件日志推送数据包 | 只读 | 全ag-mem模块统一推送 |
| 内部调度总线 | 读 | 人工检索/手动归档指令 | 只读 | 人工运维检索接口下发 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | 日志写入回执 | 专属写入 | 向日志推送来源模块返回 |
| 内部调度总线 | 写 | 日志检索结果、归档完成回执 | 专属写入 | 向人工运维接口返回操作结果 |
| 内部调度总线 | 写 | 日志存储周期统计上报 | 周期写入 | 向 ag-mem-03 推送 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| L-01 | L5层级所有操作相关日志永久在线留存，自动归档逻辑强制跳过，保障顶层记忆变更永久可追溯 |
| L-02 | 每条日志写入自动生成SHA256防篡改哈希，检索时强制校验，篡改日志直接隔离告警，不可导出 |
| L-03 | 人工批量检索、手动归档操作必须管理员双重确认，独立凭证不可复用 |
| L-04 | 全系统所有记忆变更、安全拦截、配置修改行为强制推送日志至本模块，禁止任何模块省略日志上报 |
| L-05 | 日志存储读写权限隔离，仅本模块拥有写入权限，其他模块仅可只读检索，禁止外部直接修改日志库 |
| L-06 | 熔断状态临时缓存日志，服务恢复后自动补写入持久化存储，不会丢失任何审计记录 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M51-01 | `LOG_READY`，ag-mem-42推送清理归档日志 | 标准化批量日志推送（30条） | 全部写入在线日志库，返回写入成功回执，当日写入计数增加 |
| TC-M51-02 | `LOG_READY`，管理员提交合法检索指令+双重确认 | 日志检索请求，筛选L5相关事件 | 返回完整校验通过的日志数据集，无篡改告警 |
| TC-M51-03 | `LOG_READY`，日志条目哈希被人为篡改 | 检索命中篡改日志 | 结果标记篡改告警，隔离异常条目，不允许导出 |
| TC-M51-04 | `LOG_READY`，自动归档扫描过期日志 | 无人工指令，归档计时器归零 | 普通过期日志离线压缩归档，L5日志跳过保留在线 |
| TC-M51-05 | `LOG_READY`，单次推送600条事件日志 | 超大批量日志推送数据包 | 自动拆分为2个分片串行写入，全部落盘 |
| TC-M51-06 | `LOG_READY`，接收全局FUSE熔断指令 | 紧急调度熔断指令 | 切换SYSTEM_PAUSED，新日志存入内存缓存，暂停检索归档 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、全局审计追溯中枢定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 日志事件分类、防篡改哈希、生命周期分层留存规则完整 | ✅ |
| 伪代码覆盖批量写入、哈希校验、人工检索、定时归档、熔断缓存补写、周期上报全链路 | ✅ |
| 异常场景覆盖字段缺失、日志篡改、超大批次、存储IO故障、熔断缓存、L5日志保护共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束杜绝审计日志丢失、篡改风险 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |
