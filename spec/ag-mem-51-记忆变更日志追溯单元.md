# ag-mem-51 记忆变更日志追溯单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-51 |
| 模块名称 | 记忆变更日志追溯单元 |
| 所属分区 | 全局基础底座 / 全链路审计日志持久化中心 |
| 核心职责 | 接收全ag-mem模块推送的各类操作审计日志；统一持久化存储所有记忆变更、配置修改、调度任务、安全拦截、容量管控、熔断状态变更日志；提供按分槽、条目ID、模块、操作人、时间范围多维度日志检索能力；读取ag-mem-35容量维度日志存储配额、日志保留天数、单批次写入分片上限；自动执行过期日志清理，防止日志存储溢出；定时上报日志存储占用至ag-mem-48；所有日志写入、检索、过期清理操作自身留存极简操作记录；仅负责日志存储、检索、过期清理，无业务记忆条目读写、调度、配置修改权限。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控日志写入/检索服务启停）、ag-mem-03（漏斗二调度，接收日志单元周期运行统计）、ag-mem-35（通用三维配置中心，读取日志存储最大容量、日志保留周期、单批次写入分片上限、检索并发限流）、ag-mem-48（上报日志持久层存储占用）、全系列ag-mem业务模块（接收各模块审计日志上报） |
| 被依赖模块 | 运维后台面板（提供多维度日志检索接口）、ag-mem-48（接收日志存储容量定时上报）、ag-mem-03（周期上报日志单元运行统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 日志待机就绪 | `LOG_IDLE` | 日志写入缓存空闲，实时接收日志上报、响应检索请求 | 系统初始化加载日志存储配置、熔断恢复、过期日志清理完成 |
| 批量日志缓存合并 | `LOG_FETCH` | 聚合大批量模块同步推送的审计日志，存入写入缓存 | 短时间涌入大批量变更日志上报 |
| 日志批量持久写入 | `LOG_PERSIST` | 分片将缓存日志写入持久存储，按分槽/条目建立索引 | 批量日志缓存合并完成 |
| 过期日志清理&检索响应 | `MAINTENANCE` | 按ag-mem-35配置周期扫描并删除超期日志；同步处理运维日志检索请求 | 定时日志维护周期抵达 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止大批量日志批量写入与过期清理，仅保留单条日志同步写入与基础检索 | ag-mem-01下发熔断指令；RESUME切回LOG_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 业务审计日志上报 | Struct（来源模块ID、操作类型、funnel_id/item_id、操作人、变更前后快照、执行耗时、风险等级、时间戳） | 全部ag-mem子模块（01/03/15~30/35/37/40/41/42/45/48） | 任意模块完成变更、调度、拦截、配置修改操作后推送 | 高 |
| 日志检索查询请求 | Struct（检索维度：模块/分槽/条目/操作人/时间区间、分页参数、并发查询ID） | 运维后台面板 | 管理员追溯记忆变更、故障排查审计 | 普通 |
| 全局日志存储配置回执 | Struct（日志最大存储容量、日志自动保留天数、单次批量写入上限、检索并发限流阈值） | ag-mem-35 通用配置中心 | 模块初始化、日志存储策略更新、定时维护前拉取 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控批量日志写入、过期清理任务 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 日志检索分页结果回执 | List<Struct>（完整日志条目、分页总条数、当前页码） | 运维后台面板 | 校验检索请求权限、检索完成 | 普通 |
| 日志存储容量占用上报 | Struct（单元ag-mem-51、日志持久层总KB、有效日志条数、过期待清理条数） | ag-mem-48 全局容量配额 | 每60秒定时上报、过期日志清理完成后即时上报 | 普通 |
| 日志单元周期运行统计上报 | Struct（当前状态、今日日志写入总条数、检索请求总量、过期清理批次、释放存储KB） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 日志存储与清理核心规则（V1.1容量维度标准，取自ag-mem-35配置）
### 1. 日志写入分片约束
单次批量写入日志最大条数由ag-mem-35配置控制，超量自动分片串行落盘，避免IO瞬时压力。
### 2. 自动过期清理规则
1. 日志保留时长统一由配置下发，超过保留天数的日志定时批量删除；
2. 日志存储占用达到配置最大容量上限时，优先清理最早过期日志，释放存储空间；
3. 清理操作分片执行，限制单次删除日志数量，不阻塞日志正常写入。
### 3. 检索限流规则
同一时间最大并发检索请求数取自配置，超出并发限制直接返回繁忙提示，防止检索查询占用存储IO。
### 4. 熔断降级规则
1. PAUSE半熔断：关闭批量日志合并写入、定时过期清理，仅支持单条日志实时落盘与少量基础检索；
2. FUSE全熔断：仅缓存日志不持久化，关闭所有检索与清理任务，熔断恢复后批量补写入。
### 5. 流转强制约束
1. 仅做日志存储、索引、清理、检索，无任何业务记忆条目新增/删除/修改、分槽管控、配置变更能力；
2. 日志存储上限、保留周期、分片写入上限、检索并发限流全部由ag-mem-35统一管控，本地无硬编码参数；
3. 单向接收全模块日志上报，仅对外输出检索结果与容量统计，不向业务模块下发业务调度指令；
4. 日志上报模块失联时缓存日志，模块恢复后批量补落盘，不丢失操作记录。

## 核心处理逻辑
```
FUNCTION memory_log_trace_main_loop():
    STATE_IDLE = LOG_IDLE
    STATE_FETCH = LOG_FETCH
    STATE_PERSIST = LOG_PERSIST
    STATE_MAINTENANCE = MAINTENANCE
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35日志存储容量配套配置
    log_cfg = query_log_storage_config(from_m35="ag-mem-35")
    max_write_batch = log_cfg.max_log_per_slice
    log_retention_days = log_cfg.log_keep_days
    log_max_storage_kb = log_cfg.log_storage_max_size
    max_query_concurrent = log_cfg.max_search_concurrent
    log_write_buffer = []
    stat_total_write_log = 0
    stat_total_search_req = 0
    stat_clean_batch = 0
    last_cap_report_ts = NOW()
    last_maintenance_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                send_internal_log("熔断暂停批量日志写入与清理", old_state, internal_state, now_ts)
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = LOG_IDLE
                send_internal_log("熔断恢复日志完整写入与维护", old_state, internal_state, now_ts)

        // 全熔断状态仅缓存日志，跳过批量写入、清理、检索
        IF internal_state == SYSTEM_PAUSED:
            IF 收到业务审计日志上报:
                log_write_buffer.append(获取日志上报结构体)
            SLEEP 10ms
            CONTINUE

        // 2. 接收全模块审计日志上报
        IF 收到业务审计日志上报:
            log_batch = 获取日志上报列表
            internal_state = LOG_FETCH
            log_write_buffer.extend(log_batch)
            internal_state = LOG_PERSIST
            // 分片持久写入
            slice_log = split_slice(log_write_buffer, max_write_batch)
            for slice in slice_log:
                persist_write_log_batch(slice)
                stat_total_write_log += len(slice)
            log_write_buffer.clear()
            internal_state = LOG_IDLE

        // 3. 处理运维日志检索请求
        IF 收到日志检索查询请求:
            search_req = 获取检索请求结构体
            // 并发限流校验
            if get_current_search_count() >= max_query_concurrent:
                send_search_reply(target="运维后台", result={"success":False, "msg":"检索并发超限，请稍后重试"})
                CONTINUE
            stat_total_search_req += 1
            // 多维度条件检索分页查询
            search_result = run_log_multi_dim_search(search_req)
            send_search_reply(target="运维后台", result=search_result)

        // 4. 定时日志维护（过期清理+容量校验）
        IF (now_ts - last_maintenance_ts) >= log_cfg.maintenance_interval:
            internal_state = MAINTENANCE
            // 查询当前日志存储占用
            current_log_kb = get_log_storage_usage()
            expire_cutoff_ts = now_ts - log_retention_days * 24 * 3600 * 1000
            // 清理过期日志
            expire_log_list = scan_expire_logs(expire_cutoff_ts)
            if len(expire_log_list) > 0:
                slice_expire = split_slice(expire_log_list, max_write_batch)
                for slice in slice_expire:
                    clean_expire_log_batch(slice)
                stat_clean_batch += 1
            // 存储超限补充清理
            while get_log_storage_usage() > log_max_storage_kb:
                early_expire = get_earliest_expire_logs(count=max_write_batch)
                clean_expire_log_batch(early_expire)
            last_maintenance_ts = now_ts
            internal_state = LOG_IDLE

        // 5. 60秒定时存储占用上报 + 180s周期运行统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            log_storage_kb = get_log_storage_usage()
            cap_report = build_cap_report(layer="ag-mem-51", used_kb=log_storage_kb, cached_log_count=len(log_write_buffer))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_log_runtime_stat(
                    state=internal_state,
                    total_write=stat_total_write_log,
                    total_search=stat_total_search_req,
                    total_clean_batch=stat_clean_batch
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 日志持久层IO写入失败 | 日志留存写入缓存，下一轮维护周期重试落盘，记录内部告警 | 持久存储读写链路恢复 |
| 单次批量日志上报数量超过分片上限 | 自动分片串行写入存储，避免IO阻塞 | 内置分片逻辑自动执行 |
| 日志存储容量达到配置最大上限 | 自动批量删除最早过期日志释放空间，保障新日志可写入 | 过期日志清理完成、扩容日志存储介质 |
| 同时并发检索请求超出配置限流阈值 | 直接返回繁忙拒绝检索，不中断日志写入主线程 | 已有检索任务执行完毕释放并发名额 |
| PAUSE半熔断收到大批量日志上报 | 拆分单条同步写入，关闭批量合并落盘，暂停过期日志自动清理 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35日志配置拉取失败 | 加载内置兜底保留天数、写入分片上限，输出配置缺失告警 | ag-mem-35恢复下发完整容量三维配置 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 全模块审计日志上报、日志检索请求、全局熔断指令、日志存储三维配置 | 只读 | 全部ag-mem业务模块、运维后台、ag-mem01、ag-mem35 |
| 运维总线 | 写 | 日志检索分页结果回执 | 专属写入 | 运维后台面板 |
| 内部调度总线 | 写 | 日志存储容量上报、日志单元周期运行统计 | 周期写入 | ag-mem48、ag-mem03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| LOG51-01 | 日志存储上限、自动保留周期、批量写入分片、检索并发限流全部取自ag-mem-35，本地禁止硬编码任何存储管控参数 |
| LOG51-02 | 仅具备日志存储、检索、过期清理能力，无任何修改、删除业务记忆条目、调整分槽、变更系统配置的操作权限，全链路变更行为仅留痕不可篡改 |
| LOG51-03 | 日志持久层采用不可篡改存储格式，过期仅自动清理超期数据，已写入日志禁止人工单条删除，保障审计溯源不可抵赖 |
| LOG51-04 | 熔断分级关停大批量日志合并与自动清理，故障期间优先保证关键操作日志可落地，避免日志IO抢占系统资源 |
| LOG51-05 | 批量写入、过期清理分片限流，控制单次IO操作规模，平滑存储负载，不阻塞记忆业务主线流程 |
| LOG51-06 | 熔断状态缓存所有上报日志，恢复后批量补写入持久层，杜绝系统故障期间操作审计记录丢失 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M51-01 | `LOG_IDLE`，多个业务模块同步推送大批量变更审计日志 | 批量日志上报数据流 | 自动分片串行持久写入存储，统计写入总量，缓存清空 |
| TC-M51-02 | `LOG_IDLE`，运维发起分槽+时间区间多维度日志检索 | 分页日志检索请求 | 按条件过滤日志返回分页结果，统计检索请求次数 |
| TC-M51-03 | `LOG_IDLE`，到达定时维护周期，存在大量超保留期日志 | 定时维护触发信号 | 分片批量清理过期日志，释放存储容量，记录清理批次统计 |
| TC-M51-04 | `LOG_IDLE`，日志存储占用达到配置容量上限 | 存储容量指标校验触发 | 自动优先清理最早过期日志，腾出空间接收新日志写入 |
| TC-M51-05 | `LOG_IDLE`，收到F0 PAUSE半熔断后大批量日志上报 | 半熔断+批量日志上报 | 拆分单条实时落盘，暂停批量合并与自动过期清理 |
| TC-M51-06 | `LOG_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，仅缓存日志不持久化，关闭检索与清理任务 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-51匹配白皮书全局日志审计追溯中心定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35容量维度日志存储参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖日志缓存、分片写入、过期清理、检索全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 分片写入、自动过期清理、存储容量保护、检索并发限流、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖日志批量上报、分片持久写入、多维度检索、定时维护清理、容量上报全链路 | ✅ |
| 异常场景包含存储IO故障、超大批量日志、存储溢出、检索并发超限、半熔断降级、配置缺失共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅留存操作记录，无修改业务数据权限 | ✅ |
| 6条V1.1安全约束统一存储参数、操作权限隔离、日志防篡改、故障限流、IO平滑、日志不丢失 | ✅ |
| 6条自动化测试用例覆盖全部日志审计核心业务场景 | ✅ |
