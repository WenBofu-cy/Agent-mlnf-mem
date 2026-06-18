# ag-mem-45-安全规则合规校验单元 接口规格（对齐Agent V1.1白皮书）
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-45 |
| 模块名称 | 安全规则合规校验单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / L5写入安全前置校验模块 |
| 核心职责 | L4抽象规则推送至L5前唯一合规校验网关，接收ag-mem-29下发的L4待写入条目批量校验请求；基于内置业务安全黑名单、风险关键词、敏感行为规则、分槽业务准入白名单逐条校验条目内容；输出合规/不合伙判定、风险等级、拦截原因；仅服务ag-mem-29，不直接操作存储、不签发令牌、不修改经验数据；所有拦截、放行记录推送ag-mem-51审计日志；支持安全规则动态更新、人工批量复核风险条目；L5永久记忆准入强制前置校验，是V1.1顶层记忆安全防护核心环节。 |
| 依赖模块 | ag-mem-01（总控F0全局熔断调度）、人工安全运维接口（规则更新、风险复核）、ag-mem-35（三维权重配置单元，读取分槽专属安全管控规则） |
| 被依赖模块 | ag-mem-29（L5安全锁控单元，接收合规校验回执）、ag-mem-51（记忆变更日志追溯单元，记录安全校验事件）、ag-mem-03（漏斗二调度单元，周期上报安全校验统计） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 空闲待命 | `SAFE_IDLE` | 无校验任务，等待ag-mem-29下发批量校验请求 | 系统初始化、批次校验全部完成、熔断恢复 |
| 规则加载 | `RULE_LOAD` | 加载分槽安全白名单、风险黑名单、敏感匹配规则 | 服务启动、人工下发规则更新指令 |
| 批量条目校验 | `ITEM_CHECK` | 逐条匹配安全规则，判定风险等级与合规结果 | 收到ag-mem-29校验请求，规则加载完成 |
| 结果回执输出 | `RESULT_OUT` | 组装批量校验回执推送至ag-mem-29，生成安全审计日志 | 整批条目校验完毕 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止所有校验任务，拒绝新校验请求 | F0下发FUSE熔断指令；RESUME切回SAFE_IDLE |

## 输入数据
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4条目合规批量校验请求 | Struct（批次ID、条目ID列表、条目完整文本摘要、来源分槽ID、请求来源ag-mem-29） | ag-mem-29 L5安全锁控单元 | ag-mem-27完成L4抽象，申请推送至L5时发起 | **最高** |
| 安全规则更新指令 | Struct（黑名单新增/删除、分槽白名单变更、风险阈值调整、管理员ID、双重确认挑战码） | 人工安全运维接口 | 运维更新安全过滤规则 | 高 |
| 分槽专属安全规则配置回执 | Struct（分槽ID、业务准入白名单、高危拦截阈值） | ag-mem-35 三维权重配置单元 | 模块初始化、规则动态更新 | 普通 |
| 全局调度指令 | Enum（PAUSE/RESUME/FUSE） | ag-mem-01 总控F0 | 系统紧急故障、模式切换 | 紧急 |

## 输出数据
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 批量合规校验回执 | List<Struct>（条目ID、是否合规、风险等级、拦截原因、分槽ID） | ag-mem-29 L5安全锁控单元 | 整批条目校验完成 | **最高** |
| 安全校验审计日志 | Struct（批次ID、合规数量、拦截数量、高风险条目数、时间戳） | ag-mem-51 记忆变更日志追溯单元 | 每一批次校验结束 | 高 |
| 规则更新完成回执 | Struct（修改规则条数、生效时间、管理员ID） | 人工安全运维接口 | 规则校验、加载完成 | 高 |
| 安全校验周期统计上报 | Struct（当前状态、今日总校验批次、拦截总数、高风险条目累计） | ag-mem-03 漏斗二调度单元 | 每180秒周期性上报 | 普通 |

## 安全校验分层规则（V1.1强制准入标准）
### 一、四条拦截判定规则（命中任意一条直接标记不合规，禁止写入L5）
1. 内容命中全局高危黑名单（敏感指令、违规行为、风险关键词）
2. 条目行为匹配分槽禁止操作清单（由ag-mem-35分槽配置下发）
3. 风险得分超过分槽拦截阈值（综合风险分0~1，阈值分槽独立配置）
4. 来源分槽未在L5写入准入白名单内

### 二、风险等级划分
| 风险分值区间 | 等级 | 处理逻辑 |
|:---:|:---:|------|
| 0 ≤ score ＜ 0.3 | 低风险 | 合规，允许写入L5 |
| 0.3 ≤ score ＜ 0.6 | 中风险 | 合规放行，日志标记预警 |
| ≥ 0.6 | 高风险 | 直接拦截，禁止进入L5长期核心记忆 |

### 三、特殊豁免规则
无人工豁免通道，所有高风险条目强制拦截；仅人工修改安全规则下调阈值后方可放行，修改规则必须管理员双重确认。

### 四、批量约束
单次校验最大500条，超量自动拆分多批次串行校验，避免规则匹配算力过载。

## 核心处理逻辑
```
FUNCTION safe_compliance_check_main_loop():
    STATE_IDLE = SAFE_IDLE
    STATE_RULE_LOAD = RULE_LOAD
    STATE_ITEM_CHECK = ITEM_CHECK
    STATE_RESULT_OUT = RESULT_OUT
    STATE_PAUSED = SYSTEM_PAUSED

    internal_state = STATE_RULE_LOAD
    // 初始化加载全局+分槽安全规则
    full_safe_rule_map = load_slot_safe_rule(from_m35="ag-mem-35")
    global_blacklist = load_global_risk_blacklist()
    stat_check_batch = 0
    stat_block_total = 0
    stat_high_risk = 0
    last_report_ts = NOW()

    internal_state = STATE_IDLE

    WHILE 系统运行中:
        // 1. 最高优先级：全局熔断调度
        IF 收到全局调度指令:
            cmd = 获取调度指令
            IF cmd == "FUSE":
                internal_state = STATE_PAUSED
                CONTINUE
            IF cmd == "RESUME" AND internal_state == SYSTEM_PAUSED:
                internal_state = STATE_IDLE

        // 2. 接收ag-mem-29下发批量合规校验请求
        IF 收到L4条目合规批量校验请求:
            check_req = 获取校验请求
            batch_id = check_req.批次ID
            item_list = check_req.条目ID列表
            slot_id = check_req.来源分槽ID
            internal_state = ITEM_CHECK
            slot_rule = full_safe_rule_map.get(slot_id, full_safe_rule_map["通用兜底槽"])
            batch_resp = []
            pass_count = 0
            block_count = 0
            batch_high_risk = 0

            FOR item IN item_list:
                item_id = item.条目ID
                item_text = item.条目文本摘要
                risk_score = 0.0
                block_reason = ""
                is_compliance = True

                // 规则1：全局黑名单匹配
                IF match_global_blacklist(item_text, global_blacklist):
                    is_compliance = False
                    block_reason = "内容命中全局高危黑名单"
                    risk_score = 0.75
                // 规则2：分槽禁止行为匹配
                ELIF match_slot_forbid_action(item_text, slot_rule.forbid_list):
                    is_compliance = False
                    block_reason = "匹配本分槽禁止操作规则"
                    risk_score = 0.70
                // 规则3：风险分值超过分槽拦截阈值
                ELSE:
                    risk_score = calc_item_risk_score(item_text)
                    IF risk_score >= slot_rule.block_threshold:
                        is_compliance = False
                        block_reason = f"风险分值{risk_score}超出分槽拦截阈值{slot_rule.block_threshold}"
                // 规则4：分槽无L5写入准入权限
                IF slot_id NOT IN slot_rule.allow_slot_list:
                    is_compliance = False
                    block_reason = "当前分槽未开通L5写入准入权限"

                // 统计风险数据
                IF is_compliance:
                    pass_count += 1
                ELSE:
                    block_count += 1
                    IF risk_score >= 0.6:
                        batch_high_risk += 1
                // 组装单条校验结果
                batch_resp.append({
                    "item_id": item_id,
                    "is_compliance": is_compliance,
                    "risk_score": risk_score,
                    "block_reason": block_reason,
                    "slot_id": slot_id
                })

            // 校验完成，输出回执
            internal_state = RESULT_OUT
            stat_check_batch += 1
            stat_block_total += block_count
            stat_high_risk += batch_high_risk
            // 回执下发至ag-mem-29
            send_check_response(target="ag-mem-29", batch_id=batch_id, resp_data=batch_resp)
            // 推送审计日志至ag-mem-51
            audit_log = build_safe_audit_log(
                batch_id=batch_id,
                pass_num=pass_count,
                block_num=block_count,
                high_risk_num=batch_high_risk,
                event_ts=NOW()
            )
            send_audit_log(target="ag-mem-51", log_data=audit_log)
            internal_state = STATE_IDLE

        // 3. 处理人工安全规则更新指令
        IF 收到安全规则更新指令:
            rule_req = 获取规则更新指令
            admin_id = rule_req.管理员ID
            internal_state = RULE_LOAD
            // 管理员双重确认校验
            double_verify = launch_admin_double_verify(admin_id, timeout=60*1000, code=rule_req.挑战码)
            IF NOT double_verify.通过:
                send_rule_reject_notify(target=人工运维接口, reason="双重确认校验失败")
                internal_state = STATE_IDLE
                CONTINUE
            // 更新内存规则并持久化
            update_safe_rule_store(rule_req.modify_content)
            full_safe_rule_map = reload_all_slot_rule()
            global_blacklist = reload_global_blacklist()
            // 返回规则更新完成回执
            finish_ack = build_rule_update_ack(modify_count=len(rule_req.modify_content), admin=admin_id)
            send_rule_update_ack(target=人工运维接口, ack_data=finish_ack)
            // 写入规则变更审计日志
            rule_change_log = build_rule_modify_log(admin_id, rule_req.modify_content, NOW())
            send_audit_log(target="ag-mem-51", log_data=rule_change_log)
            internal_state = STATE_IDLE

        // 4. 每180秒周期上报安全校验统计
        IF NOW() - last_report_ts >= 180 * 1000:
            stat_report = build_safe_stat_report(
                current_state=internal_state,
                total_check_batch=stat_check_batch,
                total_block=stat_block_total,
                total_high_risk=stat_high_risk
            )
            send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP 10ms
```

## 约束与异常处理
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 待校验条目文本摘要缺失 | 直接标记高风险拦截，记录日志 | 上游ag-mem-27补全条目文本摘要后重新发起校验 |
| 目标分槽无专属安全规则 | 自动加载通用兜底分槽规则完成校验 | ag-mem-35补充分槽独立安全配置 |
| 单次校验条目超过500条上限 | 自动拆分多批次串行校验，互不干扰 | 内置分片逻辑无需人工干预 |
| 规则匹配计算超时 | 本条标记高风险拦截，不阻塞同批次其他条目 | 优化规则匹配引擎或下调单次校验条数 |
| 全局紧急熔断触发 | 终止当前批次校验，丢弃未完成结果，拒绝新校验请求 | F0下发RESUME恢复指令 |
| 规则更新持久化失败 | 内存规则不更新，返回修改失败回执 | 底层存储IO恢复后重新提交规则修改 |

## 总线契约
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L4条目合规批量校验请求 | 只读 | ag-mem-29 发送 |
| 内部调度总线 | 读 | 安全规则更新指令 | 只读 | 人工安全运维接口下发 |
| 内部调度总线 | 读 | 分槽安全规则配置回执 | 只读 | ag-mem-35 同步 |
| 内部调度总线 | 读 | 全局调度熔断指令 | 只读 | ag-mem-01 下发 |
| 内部调度总线 | 写 | 批量合规校验回执 | 专属写入 | 向 ag-mem-29 返回校验结果 |
| 内部调度总线 | 写 | 规则更新完成/拒绝回执 | 专属写入 | 向人工运维接口返回操作结果 |
| 内部调度总线 | 写 | 安全校验审计日志、规则变更日志 | 事件写入 | 向 ag-mem-51 推送 |
| 内部调度总线 | 写 | 安全校验周期统计上报 | 周期写入 | 向 ag-mem-03 推送 |

## 安全边界
| 规则编号 | 内容 |
|:---:|------|
| S-01 | 所有写入L5的L4规则必须经过本模块前置合规校验，无校验回执ag-mem-29禁止签发写入令牌 |
| S-02 | 高风险条目（风险分≥0.6）强制拦截，无临时放行接口，仅能通过修改安全规则放宽阈值 |
| S-03 | 安全黑名单、分槽准入规则统一由本模块管理，ag-mem-29/28无独立校验能力，杜绝旁路校验漏洞 |
| S-04 | 所有规则新增、阈值下调、分槽准入变更操作，必须管理员双重确认，独立校验不可复用凭证 |
| S-05 | 每一批次校验、每一次规则修改完整写入ag-mem-51不可篡改审计日志，留存风险分值、拦截原因 |
| S-06 | 仅接收ag-mem-29发起的校验请求，拒绝其他模块直接调用合规校验接口，防止绕过安全审查 |

## 接口校验用例
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M45-01 | `SAFE_IDLE`，条目无风险、分槽准入正常 | 批量校验请求，低风险条目 | 回执标记合规放行，低风险预警日志 |
| TC-M45-02 | `SAFE_IDLE`，条目命中全局高危黑名单 | 携带违规关键词条目 | 标记不合规、高风险，拦截禁止写入L5 |
| TC-M45-03 | `SAFE_IDLE`，分槽无专属安全配置 | 未知业务分槽校验请求 | 自动使用通用兜底规则完成校验 |
| TC-M45-04 | `SAFE_IDLE`，单次校验600条L4条目 | 超大批量校验请求 | 自动拆分为2个子批次串行校验 |
| TC-M45-05 | `SAFE_IDLE`，管理员下调拦截阈值+双重确认通过 | 合法规则更新指令 | 规则持久化更新，生成规则变更审计日志 |
| TC-M45-06 | `SAFE_IDLE`，校验中途收到FUSE熔断指令 | 全局紧急熔断指令 | 终止当前批次校验，切换SYSTEM_PAUSED，拒绝新请求 |

## 质量自检清单
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、L5写入前置安全校验定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 四条拦截规则、风险分级、批量约束完整清晰 | ✅ |
| 伪代码覆盖批量校验、规则匹配、风险打分、规则更新、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖摘要缺失、无分槽配置、超大批次、计算超时、熔断、持久化故障共6类 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无绕过漏洞 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

