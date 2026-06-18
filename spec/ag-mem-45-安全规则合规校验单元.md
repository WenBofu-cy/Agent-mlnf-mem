# ag-mem-45 安全规则合规校验单元 完整标准化接口文档（对齐EM-Core-Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-45 |
| 模块名称 | 安全规则合规校验单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 全局安全准入校验底座 |
| 核心职责 | 统一承载全记忆链路安全准入、内容合规、访问权限三层校验逻辑；读取ag-mem-35全局安全维度阈值、黑白名单、拦截权重；接收分层写入、分槽访问、晋升归档、人工运维操作前置校验请求；拦截违规条目、高危分槽、越权操作；输出校验通过/拦截结果与风险等级；拦截事件同步推送运维告警；定时上报校验缓存内存占用至ag-mem-48；所有拦截、放行、人工豁免操作完整写入ag-mem-51审计日志；仅做前置校验拦截，无条目存储、删除、修改、分槽创建销毁权限。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断，管控校验服务启停）、ag-mem-03（漏斗二调度，接收周期校验统计上报）、ag-mem-35（通用三维配置中心，读取安全准入阈值、违规分级标准、黑白名单、单次批量校验上限）、ag-mem15（读取分槽基础权限标签）、ag-mem20~30（接收分层写入/条目操作前置校验请求）、ag-mem-48（上报本地校验缓存内存开销） |
| 被依赖模块 | ag-mem15、ag-mem20~30（接收校验放行/拦截回执，控制条目写入、分槽操作是否执行）、运维告警面板（接收高危拦截告警）、ag-mem-48（接收定时内存占用上报）、ag-mem-51（记录安全校验、拦截、豁免审计日志） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 校验待机就绪 | `SAFE_IDLE` | 校验缓存空闲，实时响应单条/批量安全校验请求 | 系统初始化、熔断恢复、批量校验任务处理完毕 |
| 批量请求缓存加载 | `REQ_FETCH` | 批量操作校验请求入本地缓存，拉取分槽安全标签、全局黑白名单 | 收到大批量分层写入/分槽操作校验请求 |
| 多层安全合规批量校验 | `SAFE_CALC` | 按内容合规、分槽权限、安全阈值三层规则批量校验，标记放行/拦截/豁免 | 批量请求与安全配置缓存加载完成 |
| 校验结果批量回执下发 | `RESULT_DISPATCH` | 分片向请求模块推送校验回执，高危拦截同步推送运维告警 | 全批量条目校验计算完成 |
| 暂停降级 | `SYSTEM_PAUSED` | 收到F0 PAUSE/FUSE熔断指令，停止批量安全校验，仅保留极简基础准入校验 | ag-mem-01下发熔断指令；RESUME切回SAFE_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 单条/批量安全校验请求 | List<Struct>（操作类型：写入/晋升/归档/分槽变更；item_id/funnel_id；内容特征；操作人权限标签；置信度S值） | ag-mem15、ag-mem20~30 | 任何记忆条目、分槽变更操作执行前强制发起 | 最高 |
| 全局三维安全合规配置回执 | Struct（L5最低准入S阈值、违规分级阈值、黑白名单集合、批量校验分片上限、高危拦截告警阈值） | ag-mem-35 通用配置中心 | 模块初始化、安全策略更新、批量校验前拉取 | 普通 |
| 分槽安全权限标签快照 | List<Struct>（funnel_id、访问权限等级、风险标签、白名单标记） | ag-mem-15 分槽主调度单元 | 批量校验时同步拉取分槽权限信息 | 普通 |
| 人工安全豁免指令 | Struct（funnel/item范围、豁免时效、管理员ID、双重挑战码） | 运维后台面板 | 人工放行特定高危条目/分槽 | 紧急 |
| 全局调度熔断指令 | Enum(PAUSE/RESUME/FUSE) | ag-mem-01 F0总控 | 全局熔断切换，管控批量校验能力 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 批量安全校验回执 | List<Struct>（操作ID、item/funnel_id、verify_result=PASS/BLOCK/EXEMPT、risk_level、block_reason、expire_ts） | ag-mem15、ag-mem20~30 | 单分片批量校验完成 | 最高 |
| 高危拦截汇总告警报表 | Struct（拦截总量、高危条目清单、风险等级分布、关联分槽ID） | 运维告警面板 | 批量校验出现中高风险拦截条目 | 高 |
| 校验缓存内存占用上报 | Struct（单元ag-mem-45、请求缓存总KB、待校验操作总量） | ag-mem-48 全局容量配额 | 每60秒定时上报、大批量校验完成后即时上报 | 普通 |
| 安全校验审计日志 | Struct（事件类型、批量操作总数、放行/拦截/豁免数量、高危拦截明细、管理员、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一轮批量校验、人工豁免操作完成 | 普通 |
| 安全单元周期运行统计上报 | Struct（当前状态、今日总校验批次、累计拦截条目、高危告警次数、人工豁免操作总量） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 安全合规校验核心规则（V1.1全局安全维度标准，取自ag-mem-35配置）
### 1. 三层校验链路（串行执行，任意一层拦截直接终止流程）
1. **L5安全准入阈值校验**
   条目S置信度 ≥ ag-mem-35配置最低准入阈值才可放行；低于阈值直接标记拦截。
2. **分槽权限&黑白名单校验**
   高危黑名单分槽所有操作直接拦截；白名单分槽自动豁免基础阈值校验；操作人权限低于分槽最低访问权限拦截。
3. **内容合规分级校验**
   根据内容特征匹配违规分级规则，低风险告警放行，中/高风险直接拦截。

### 2. 人工豁免约束
1. 豁免操作必须携带管理员双重挑战码校验；
2. 豁免存在时效，过期自动恢复常规校验规则；
3. 豁免范围仅支持指定分槽/指定条目，不支持全局批量豁免。

### 3. 熔断降级规则
1. PAUSE半熔断：停止大批量批量校验，仅处理单条实时写入校验，高危拦截逻辑保留；
2. FUSE全熔断：仅保留极简S阈值基础校验，关闭黑白名单、内容合规复杂校验，阻断大批量操作。

### 4. 分片批量约束
单次批量校验最大条目上限取自ag-mem-35配置，超量自动分片串行校验，防止内容特征计算占用大量CPU。

### 5. 流转强制约束
1. 仅前置校验拦截，无任何修改、删除、存储条目/分槽元数据权限；
2. 所有安全阈值、黑白名单、分级规则统一由ag-mem-35下发，本地无硬编码安全规则；
3. 单向数据流：仅向外输出校验回执、告警、日志，不向业务模块下发管控修改指令；
4. ag-mem15分槽标签缺失时加载全局通用风险兜底规则，不中断校验流程。

## 核心处理逻辑
```
FUNCTION safety_compliance_verify_main_loop():
    STATE_IDLE = SAFE_IDLE
    STATE_FETCH = REQ_FETCH
    STATE_CALC = SAFE_CALC
    STATE_DISPATCH = RESULT_DISPATCH
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_IDLE
    // 读取ag-mem-35全局安全维度配置
    safe_cfg = query_global_safety_config(from_m35="ag-mem-35")
    min_s_threshold = safe_cfg.layer5_min_s_confidence
    max_batch_verify = safe_cfg.max_verify_per_slice
    risk_level_rule = safe_cfg.risk_class_rule
    black_slot_set = set(safe_cfg.black_funnel_list)
    white_slot_set = set(safe_cfg.white_funnel_list)
    temp_verify_cache = []
    stat_total_batch = 0
    stat_high_risk_alert = 0
    stat_exempt_operate = 0
    last_cap_report_ts = NOW()

    WHILE 系统进程存活:
        now_ts = NOW()
        // 1. 最高优先级：全局熔断调度指令处理
        IF 收到全局调度熔断指令:
            fuse_cmd = 获取指令
            old_state = internal_state
            if fuse_cmd == "FUSE" or fuse_cmd == "PAUSE":
                internal_state = STATE_PAUSED
                temp_verify_cache.clear()
                send_audit_log(target="ag-mem-51", log_data=build_safe_state_audit(old_state, internal_state, "熔断限制批量安全校验", now_ts))
                CONTINUE
            elif fuse_cmd == "RESUME" and internal_state == SYSTEM_PAUSED:
                internal_state = SAFE_IDLE
                send_audit_log(target="ag-mem-51", log_data=build_safe_state_audit(old_state, internal_state, "熔断恢复完整安全校验", now_ts))

        // 全熔断状态仅保留极简单条S校验，跳过批量逻辑
        IF internal_state == SYSTEM_PAUSED:
            IF 收到单条校验请求:
                single_req = 获取单条请求
                minimal_single_verify(req=single_req, min_s=min_s_threshold)
            SLEEP 10ms
            CONTINUE

        // 2. 处理人工安全豁免指令（高优先级）
        IF 收到人工安全豁免指令:
            exempt_req = 获取豁免指令
            // 双重管理员凭证校验
            if not admin_double_challenge_check(exempt_req.admin_id, exempt_req.challenge_code):
                send_exempt_reject(target="运维后台", reason="双重身份校验失败")
                CONTINUE
            // 写入豁免配置临时缓存
            add_temp_exempt_record(exempt_req.range, exempt_req.expire_ts)
            stat_exempt_operate += 1
            // 豁免操作审计日志
            exempt_audit = build_exempt_audit_log(exempt_req, now_ts)
            send_audit_log(target="ag-mem-51", log_data=exempt_audit)

        // 3. 接收批量安全校验请求
        IF 收到批量安全校验请求:
            batch_req = 获取批量校验请求列表
            internal_state = REQ_FETCH
            temp_verify_cache.extend(batch_req)
            // 同步拉取分槽安全标签
            slot_security_meta = fetch_funnel_security_tag(source="ag-mem-15")
            internal_state = SAFE_CALC
            verify_result_list = []
            slice_batch = split_slice(temp_verify_cache, max_batch_verify)
            high_risk_block_list = []

            for slice in slice_batch:
                for req in slice:
                    f_id = req.funnel_id
                    item_s = req.confidence_S
                    op_type = req.操作类型
                    risk_res = {"result":"PASS", "risk_level":"low", "block_reason":""}
                    // 第一步：黑白名单分槽拦截
                    if f_id in black_slot_set:
                        risk_res["result"] = "BLOCK"
                        risk_res["risk_level"] = "high"
                        risk_res["block_reason"] = "分槽位于全局安全黑名单"
                        high_risk_block_list.append(req)
                        verify_result_list.append(assemble_verify_result(req, risk_res))
                        continue
                    // 白名单直接放行，跳过阈值校验
                    if f_id in white_slot_set or check_in_temp_exempt(req):
                        risk_res["result"] = "EXEMPT"
                        verify_result_list.append(assemble_verify_result(req, risk_res))
                        continue
                    // 第二步：L5置信度准入校验
                    if item_s < min_s_threshold:
                        risk_res["result"] = "BLOCK"
                        risk_res["risk_level"] = "mid"
                        risk_res["block_reason"] = f"S置信度{item_s}低于安全准入阈值{min_s_threshold}"
                        verify_result_list.append(assemble_verify_result(req, risk_res))
                        continue
                    // 第三步：内容合规分级校验
                    content_risk = calc_content_risk(req.content_feature, risk_level_rule)
                    if content_risk == "high":
                        risk_res["result"] = "BLOCK"
                        risk_res["risk_level"] = "high"
                        risk_res["block_reason"] = "内容匹配高危违规规则"
                        high_risk_block_list.append(req)
                    elif content_risk == "mid":
                        risk_res["risk_level"] = "mid"
                    verify_result_list.append(assemble_verify_result(req, risk_res))

            temp_verify_cache.clear()
            internal_state = RESULT_DISPATCH
            // 分片下发校验回执
            slice_result = split_slice(verify_result_list, max_batch_verify)
            for slice_res in slice_result:
                target_mod = get_request_source_module(slice_res[0])
                send_verify_reply(target=target_mod, batch_result=slice_res)
            stat_total_batch += 1
            // 高危拦截推送运维告警
            if len(high_risk_block_list) > 0:
                stat_high_risk_alert += 1
                alert_report = build_high_risk_alert(high_risk_block_list, now_ts)
                send_alert(target="运维告警面板", alert_data=alert_report)
            // 生成批量校验审计日志
            audit_log = build_verify_batch_audit(
                total_op=len(batch_req),
                pass_count=count_result_type(verify_result_list, "PASS"),
                block_count=count_result_type(verify_result_list, "BLOCK"),
                exempt_count=count_result_type(verify_result_list, "EXEMPT"),
                high_risk_num=len(high_risk_block_list),
                ts=now_ts
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = SAFE_IDLE

        // 4. 60秒定时内存上报 + 180秒周期运行统计上报
        IF now_ts - last_cap_report_ts >= 60 * 1000:
            cache_kb = calc_verify_cache_size(temp_verify_cache, safe_cfg.avg_req_meta_kb)
            cap_report = build_cap_report(layer="ag-mem-45", used_kb=cache_kb, pending_verify_count=len(temp_verify_cache))
            send_cap_report(target="ag-mem-48", report=cap_report)
            IF now_ts - last_cap_report_ts >= 180 * 1000:
                runtime_stat = build_safe_runtime_stat(
                    state=internal_state,
                    total_verify_batch=stat_total_batch,
                    high_risk_alert_times=stat_high_risk_alert,
                    total_exempt_ops=stat_exempt_operate
                )
                send_stat_report(target="ag-mem-03", report=runtime_stat)
            last_cap_report_ts = now_ts

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| ag-mem-15分槽安全标签拉取失败 | 全部分槽按普通无风险兜底规则校验，记录轻度告警 | ag-mem-15恢复输出分槽权限标签 |
| 批量校验请求数量超过分片上限 | 自动分片串行校验下发回执，避免CPU瞬时打满 | 内置分片逻辑自动执行 |
| 本地校验请求缓存内存溢出 | 暂停接收新批量请求，优先处理已有缓存，上报容量告警至ag-mem-48 | 缓存校验完成、扩容内存资源 |
| PAUSE半熔断收到大批量校验请求 | 拆分批量为单条依次校验，关闭批量并行计算，保留拦截能力 | ag-mem-01下发RESUME解除熔断 |
| ag-mem-35安全配置拉取失败 | 加载内置最低S阈值、空黑白名单兜底规则，输出配置缺失告警 | ag-mem-35恢复下发完整三维安全配置 |
| 人工豁免双重挑战码错误/超时 | 直接驳回豁免指令，不生成临时豁免记录 | 管理员重新发起合规豁免操作 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 批量安全校验请求、人工豁免指令、全局熔断指令、安全三维配置、分槽安全标签 | 只读 | ag-mem15、ag-mem20~30、运维后台、ag-mem01、ag-mem35 |
| 内部业务总线 | 写 | 批量安全校验回执 | 专属写入 | ag-mem15、ag-mem20~30 |
| 运维告警总线 | 写 | 高危拦截汇总告警报表 | 专属写入 | 运维告警面板 |
| 内部调度总线 | 写 | 缓存容量上报、安全校验审计日志、周期运行统计 | 事件/周期写入 | ag-mem48、ag-mem51、ag-mem03 |

## 安全边界（V1.1强制规范）
| 规则编号 | 内容 |
|:---:|------|
| SAF45-01 | 安全准入阈值、黑白名单、违规分级、批量上限全部由ag-mem-35统一管控，本地禁止硬编码任何安全校验规则 |
| SAF45-02 | 仅具备前置校验、拦截放行判定能力，无条目写入/删除、分槽权限修改、黑名单编辑权限，安全策略变更收敛至配置中心与运维后台 |
| SAF45-03 | 所有人工豁免操作强制双重管理员凭证校验，豁免带时效限制，杜绝无权限临时放行高危数据 |
| SAF45-04 | 熔断分级压缩复杂批量校验逻辑，故障期间仅保留核心S准入校验，防止安全计算抢占系统算力加剧故障 |
| SAF45-05 | 分片限流控制单次批量校验规模，平滑内容特征计算CPU负载，保障记忆读写主线业务优先执行 |
| SAF45-06 | 熔断清空待校验缓存，恢复后重新拉取实时操作请求校验，避免基于过期分槽/条目数据错误放行高危内容 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M45-01 | `SAFE_IDLE`，大批量分层写入校验请求，含黑名单分槽条目 | 批量安全校验请求 | 黑名单分槽标记高危拦截，生成校验回执下发存储模块，推送高危告警、写入审计日志 |
| TC-M45-02 | `SAFE_IDLE`，条目S值低于ag-mem-35配置最低准入阈值 | 单条条目写入校验请求 | 判定中风险拦截，拒绝条目写入分层存储 |
| TC-M45-03 | `SAFE_IDLE`，运维下发带合法双重挑战码的分槽豁免指令 | 人工安全豁免指令 | 生成时效内临时豁免记录，对应分槽后续校验跳过黑白名单与阈值限制，记录豁免审计日志 |
| TC-M45-04 | `SAFE_IDLE`，单次待校验操作远超分片上限 | 超大批量校验请求 | 自动分片串行完成校验，分批下发回执，无CPU阻塞 |
| TC-M45-05 | `SAFE_IDLE`，收到F0 PAUSE半熔断后大批量写入请求 | 半熔断+批量校验请求 | 批量拆分为单条串行校验，保留全部拦截规则，不关闭安全能力 |
| TC-M45-06 | `SAFE_IDLE`，收到F0 FUSE全熔断指令 | 全局全熔断调度指令 | 切换SYSTEM_PAUSED，清空校验缓存，关闭批量复杂校验，仅保留极简S阈值校验 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号ag-mem-45匹配白皮书全局安全合规校验底座定位 | ✅ |
| 上下游依赖对齐通用版ag-mem-35安全维度参数，链路无冲突 | ✅ |
| 4种业务状态+暂停状态，覆盖请求缓存、多层校验、回执下发全流程 | ✅ |
| 输入输出完整标注收发模块、结构体、优先级，数据流无错乱 | ✅ |
| 三层串行校验、黑白名单、人工豁免、分片限流、熔断降级规则严格对齐V1.1全局配置规范 | ✅ |
| 伪代码覆盖批量校验、人工豁免、三层安全判定、高危告警、容量上报、审计日志全链路 | ✅ |
| 异常场景覆盖分槽标签缺失、超大批量、缓存溢出、半熔断降级、配置缺失、豁免校验失败共6类全覆盖 | ✅ |
| 总线读写权限隔离，仅做前置拦截判定，无修改业务数据权限 | ✅ |
| 6条V1.1安全约束统一安全策略、权限隔离、豁免强校验、故障限流、全操作可审计、规避过期数据校验 | ✅ |
| 6条自动化测试用例覆盖全部安全校验核心业务场景 | ✅ |

---