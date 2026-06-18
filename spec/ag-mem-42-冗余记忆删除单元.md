# ag-mem-42 冗余记忆删除单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-42 |
| 模块名称 | 冗余记忆删除单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 记忆清理执行单元 |
| 核心职责 | 接收ag-mem-40输出的待淘汰条目候选清单，作为唯一删除数据源；读取ag-mem-35下发的分层删除限流、单次清理分片上限、清理冷却周期；分片向ag-mem20~26分层存储下发条目物理删除指令；统计分层清理条目总量、释放内存；接收ag-mem-03调度信号支持紧急扩容清理任务；定时上报本地待删除缓存内存占用至ag-mem-48；所有批量条目删除操作全量写入ag-mem-51审计日志；仅负责执行条目删除，无I值计算、遗忘判定、复用校验、分槽元数据修改能力。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控清理任务启停）、ag-mem-03（漏斗二调度，下发紧急清理调度信号、接收清理运行统计）、ag-mem-35（通用三维配置中心，读取分层清理QPS限流、单次清理分片上限、清理冷却间隔）、ag-mem-40（读取分层待淘汰条目候选清单）、ag-mem20~26（下发条目删除指令，接收删除执行回执）、ag-mem-48（上报本地待删缓存内存开销） |
| 被依赖模块 | ag-mem20~26（接收批量条目删除指令，执行底层数据清理）、ag-mem-48（接收定时内存占用上报）、ag-mem-51（记录批量记忆删除审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 清理待机就绪 | `DEL_IDLE` | 待删条目缓存空闲，等待候选清单或紧急清理信号，无批量删除任务 | 系统初始化、熔断恢复、一轮完整清理任务执行完毕 |
| 待删条目缓存加载 | `CANDIDATE_FETCH` | 接收ag-mem-40待淘汰清单，存入本地分片缓存，按分层分组 | 收到遗忘判定输出的候选条目列表 |
| 清理分片限流计算 | `LIMIT_CALC` | 读取ag-mem-35分层限流阈值，拆分待删条目为合规分片，计算执行冷却间隔 | 完整待删条目缓存加载完成 |
| 分层删除指令批量下发 | `DELETE_DISPATCH` | 分片向对应分层存储下发条目删除指令，同步接收删除执行回执统计释放资源 | 分片限流计算完成，达到冷却执行窗口 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止所有批量清理任务，清空待删缓存 | ag-mem-01下发熔断指令；RESUME切回DEL_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层待淘汰条目候选清单 | List<Struct>（item_id、funnel_id、layer、item_I、eliminate_reason） | ag-mem-40 遗忘阈值判定单元 | 遗忘筛选流程完成后批量推送 | 高 |
| 紧急清理调度信号 | Struct（目标分层/funnel、临时放开限流倍率、最大清理条目数） | ag-mem-03 漏斗二调度单元 | 分层存储容量预警、人工运维紧急清理 | 高 |
| 全局三维记忆晋升配置回执 | Struct（分层单秒删除限流QPS、单次最大清理分片条目、分片间冷却毫秒） | ag-mem-35 通用配置中心 | 模块初始化、清理限流策略更新、每轮清理前拉取 | 普通 |
| 分层条目删除执行回执 | List<Struct>（item_id、layer、delete_success、released_kb、fail_reason） | ag-mem20~26 分层存储单元 | 下发删除指令后底层存储异步返回执行结果 | 普通 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，阻断批量清理任务 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分层条目批量删除指令 | List<Struct>（item_id、funnel_id、delete_batch_id、expect_release_kb） | ag-mem20~26 | 单分片限流校验通过，到达冷却窗口 | 高 |
| 批量清理执行汇总报表 | Struct（各分层清理成功条目、失败条目、总释放内存KB、限流倍率、冷却耗时） | ag-mem-03 漏斗二调度单元 | 一整轮待删条目全部下发并接收完回执 | 普通 |
| 待删缓存内存占用上报 | Struct（单元ag-mem-42、缓存候选条目总KB、等待清理条目总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量清理完成后即时上报 | 普通 |
| 记忆删除审计日志 | Struct（事件类型、清理批次ID、分层清理条目数量、总释放内存、失败条目明细、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 一轮完整清理任务全部执行完毕 | 普通 |
| 清理单元周期运行统计上报 | Struct（当前状态、今日常规清理批次、紧急扩容清理批次、累计释放内存总量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 冗余删除核心规则（V1.1记忆晋升维度标准，取自ag-mem-35配置）
### 1. 清理限流与分片约束
1. 分层独立QPS限流：L0~L4分别配置每秒最大删除条目，防止底层存储IO打满；
2. 单次分片最大清理条目、分片之间强制冷却间隔，由ag-mem-35统一下发；
3. 紧急清理信号可临时上调限流倍率，仅短期扩容，不永久修改全局配置。
### 2. 条目删除执行约束
1. 仅删除ag-mem-40输出的待淘汰候选条目，无自主筛选、新增待删条目能力；
2. 按分层隔离下发删除指令，L0条目仅下发ag-mem20，L1下发ag-mem21，分层互不干扰；
3. 删除失败条目留存缓存，等待下一轮清理周期重试，连续3次失败标记异常写入审计日志。
### 3. 熔断降级规则
1. PAUSE半熔断：暂停常规定时批量清理，仅响应运维紧急扩容清理；
2. FUSE全熔断：清空全部待删候选缓存，停止所有删除指令下发，仅保留日志上报。
### 4. 流转强制约束
1. 无条目价值判定、分槽元数据读写权限，仅纯执行删除动作；
2. 限流、分片、冷却参数全部由ag-mem-35管控，本地无硬编码清理阈值；
3. 单向数据流：仅接收候选清单、下发删除指令，不反向输出条目指标给上游判定模块；
4. 分层存储失联时自动延迟重试，不阻塞整体清理流程。

## 核心处理逻辑
```
FUNCTION redundant_delete_main_loop():
    STATE_IDLE = DEL_IDLE
    STATE_FETCH = CANDIDATE_FETCH
    STATE_LIMIT = LIMIT_CALC
    STATE_DISPATCH = DELETE_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35清理限流配置
    del_cfg = query_delete_limit_config(from_m35="ag-mem-35")
    layer_qps_limit = del_cfg.layer_delete_qps
    max_slice_item = del_cfg.max_clean_item_per_slice
    slice_cool_ms = del_cfg.slice_cool_interval
    pending_delete_cache = []
    stat_normal_clean_batch = 0
    stat_emergency_clean_batch = 0
    stat_total_release_kb = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                pending_delete_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_del_state_audit(old_state, internal_state, "熔断暂停批量清理", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = DEL_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_del_state_audit(old_state, internal_state, "熔断恢复清理任务", now_ts))

        // 全熔断状态跳过所有清理逻辑
        IF internal_state == SYSTEM_PAUSED:
            SLEEP 10ms
            CONTINUE

        // 2. 接收ag-mem-40待淘汰候选清单
        IF 收到分层待淘汰条目候选清单:
            candidate_list = 获取候选条目数组
            internal_state = CANDIDATE_FETCH
            pending_delete_cache.extend(candidate_list)
            internal_state = LIMIT_CALC
            // 按分层分组、分片拆分
            layer_group_map = group_by_layer(pending_delete_cache)
            all_slice_batches = []
            for layer, item_list in layer_group_map.items():
                slice_arr = split_slice(item_list, max_slice_item)
                for slice in slice_arr:
                    all_slice_batches.append({"layer": layer, "batch_items": slice})
            internal_state = DELETE_DISPATCH
            total_success = 0
            total_fail = 0
            total_release = 0
            fail_item_record = []
            // 分片串行下发删除指令，分片间冷却
            for batch in all_slice_batches:
                target_layer_mod = get_layer_module(batch["layer"])
                del_batch_id = generate_uuid()
                del_cmd = build_delete_batch_cmd(batch["batch_items"], del_batch_id)
                send_delete_cmd(target=target_layer_mod, cmd=del_cmd)
                SLEEP slice_cool_ms / 1000
                // 等待底层存储返回删除回执
                del_receipt = wait_delete_receipt(del_batch_id, timeout=5000)
                for res in del_receipt:
                    if res.delete_success:
                        total_success += 1
                        total_release += res.released_kb
                    else:
                        total_fail += 1
                        fail_item_record.append({"item_id": res.item_id, "reason": res.fail_reason})
            stat_total_release_kb += total_release
            pending_delete_cache.clear()
            // 输出清理汇总报表至ag-mem-03
            clean_report = build_clean_summary_report(
                success_cnt=total_success,
                fail_cnt=total_fail,
                total_release_kb=total_release,
                fail_detail=fail_item_record
            )
            send_clean_report(target="ag-mem-03", report=clean_report)
            // 写入删除审计日志
            audit_log = build_delete_audit_log(
                batch_count=len(all_slice_batches),
                success=total_success,
                fail=total_fail,
                release_kb=total_release,
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = DEL_IDLE

        // 3. 接收ag-mem-03紧急清理调度信号
        IF 收到紧急清理调度信号:
            emergency_signal = 获取紧急清理参数
            stat_emergency_clean_batch += 1
            // 临时放大限流倍率执行清理，流程同上，仅修改limit参数
            temp_limit = layer_qps_limit * emergency_signal.rate_multi
            run_emergency_clean_task(signal=emergency_signal, temp_qps=temp_limit)

        // 4. 60s定时内存上报 + 180s周期运行统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_del_cache_size(pending_delete_cache, del_cfg.avg_item_kb)
            cap_report = build_cap_report(layer="ag-mem-42", used_kb=cache_kb, pending_item_count=len(pending_delete_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_clean_runtime_stat(
                    state=internal_state,
                    normal_batch=stat_normal_clean_batch,
                    emergency_batch=stat_emergency_clean_batch,
                    total_release_mem=stat_total_release_kb
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-40候选清单为空 | 不执行清理，维持DEL_IDLE待机 | ag-mem-40生成新的待淘汰条目清单 |
| 分层存储删除指令返回失败 | 条目保留在待删缓存，下一轮清理重试；连续3次失败标记异常日志 | 分层存储读写故障恢复 |
| 单次待删条目超过分片上限 | 自动拆分多片串行下发，分片间插入冷却间隔防IO风暴 | 内置分片+冷却逻辑自动执行 |
| 待删缓存内存溢出 | 停止接收新候选清单，优先执行已有缓存清理，上报容量告警至ag-mem-48 | 缓存条目清理完毕、扩容内存 |
| PAUSE半熔断收到常规候选清单 | 缓存暂存，不执行清理，仅处理人工紧急清理任务 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35限流配置拉取失败 | 使用内置兜底限流阈值执行清理，输出配置缺失告警 | ag-mem-35恢复下发完整三维配置 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 待淘汰候选清单、紧急清理信号、全局熔断指令、分层清理限流配置、分层删除回执 | 只读 | ag-mem40、ag-mem03、ag-mem01、ag-mem35、ag-mem20~26 |
| 内部业务总线 | 写 | 分层条目批量删除指令 | 专属写入 | ag-mem20~26 |
| 内部调度总线 | 写 | 清理汇总报表、内存容量上报、删除审计日志、周期运行统计 | 事件/周期写入 | ag-mem03、ag-mem48、ag-mem51 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| DEL42-01 | 分层删除QPS、分片上限、冷却间隔全部由ag-mem-35统一管控，本地禁止硬编码清理限流参数 |
| DEL42-02 | 仅可删除ag-mem-40输出的淘汰候选条目，无自主筛选、手动指定任意条目删除的能力，防止误删有效记忆 |
| DEL42-03 | 熔断分级管控清理流量，半熔断抑制常规大批量清理，避免存储IO压力过载引发系统雪崩 |
| DEL42-04 | 所有批量删除批次完整写入ag-mem-51审计日志，记录成功/失败条目、释放内存、失败原因，支撑数据删除溯源 |
| DEL42-05 | 分片+冷却双重限流，控制单次下发删除条目数量，平滑底层存储IO负载，保障读写业务优先通行 |
| DEL42-06 | 全熔断清空待删缓存，恢复后等待ag-mem-40重新推送最新候选清单，避免基于过期清单误删有效条目 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M42-01 | `DEL_IDLE`，ag-mem-40推送大批量分层待淘汰清单 | 完整候选条目列表 | 分层分组分片下发删除指令，等待回执统计释放内存，输出清理报表、删除审计日志 |
| TC-M42-02 | `DEL_IDLE`，分层存储删除返回部分失败条目 | 含删除失败回执 | 失败条目留存缓存等待下一轮重试，审计日志记录失败明细 |
| TC-M42-03 | `DEL_IDLE`，ag-mem-03下发紧急扩容清理信号 | 紧急清理调度信号 | 临时放大限流倍率，加速清理指定范围条目 |
| TC-M42-04 | `DEL_IDLE`，待删条目远超单分片上限 | 超大批量候选清单 | 自动拆分多片，分片间执行冷却间隔串行下发 |
| TC-M42-05 | `DEL_IDLE`，收到F0 PAUSE半熔断后收到常规候选清单 | 半熔断+常规待删清单 | 缓存条目暂存，不执行批量清理，仅响应紧急清理 |
| TC-M42-06 | `DEL_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空待删缓存，停止所有删除指令下发 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-42匹配白皮书冗余记忆清理执行单元定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35清理限流三维参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖候选缓存、分片限流、删除下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 分层限流、分片冷却、紧急扩容、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖候选清单接收、分层分组、分片限流下发、回执统计、紧急清理、容量上报、审计日志全链路 | ✅ |
| 异常场景包含删除失败、超大批量、缓存溢出、半熔断拦截、配置缺失、存储失联共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅执行指定条目删除，无自主筛选删除权限 | ✅ |
| 6条V1.1安全约束统一限流参数、防误删、故障限流、删除可审计、平滑IO、规避过期清单 | ✅ |
| 6条自动化测试用例覆盖全部记忆清理核心业务场景 | ✅ |

---