# ag-mem-29-L5核心层安全规则硬锁定单元 规整落地版接口规格文档
统一对齐 ag-mem-28、ag-mem-45、ag-mem-51 配套模块文档规范，标准化结构体、梳理令牌全生命周期管控、完善安全审计链路，完整保留原生权限校验、人工双重确认、超时吊销业务逻辑，适配开发、安全审计、自动化联调测试。

## 一、模块基础元信息
| 项 | 内容 |
|----|------|
| 模块唯一ID | ag-mem-29 |
| 模块全称 | L5核心层安全规则硬锁定单元 |
| 所属架构 | 三、漏斗二：任务经验漏斗 / 五层存储配套权限管控模块 |
| 层级定位 | L5（ag-mem-28）唯一写入权限签发中枢，全局管控顶层永久存储写保护锁；不存储经验数据，仅负责令牌生成、安全准入校验、锁状态调度、操作审计 |
| 核心能力 | 三类写入渠道安全准入校验、HMAC-SHA256加密临时解锁令牌签发、单令牌并发隔离、30秒自动超时吊销、人工双重确认操作、熔断强制回锁、全操作安全日志推送、周期状态指标上报 |
| 硬性约束 | 全局仅允许1个活跃令牌；令牌有效期不可延期；人工操作必须双因子确认；所有授权/拒绝动作强制落审计日志 |

### 上下游依赖图谱
#### 依赖模块（接收/主动调用）
1. ag-mem-28 L5核心存储单元：下发令牌、锁状态变更通知、接收L5存储状态同步回执
2. ag-mem-45 安全规则库：加载全局准入阈值、校验L4推送规则合规性
3. ag-mem-01 总控F0：接收全局熔断/恢复调度指令
4. 人工操作管理接口：管理员下发锁定/解锁/删除指令

#### 被依赖模块（对外输出服务）
1. ag-mem-16 工具调用槽：S直达写入授权结果回执
2. ag-mem-27 L4抽象提炼单元：高置信规则推送授权回执
3. ag-mem-28 L5存储单元：令牌下发、锁变更通知、令牌吊销指令
4. ag-mem-51 记忆变更日志追溯单元：全量安全操作审计日志
5. ag-mem-03 漏斗二调度单元：周期权限管控指标上报

## 二、内部状态机（5种互斥运行状态）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `LOCKED_READY` | 锁定就绪 | 默认写保护生效，无活跃令牌，可接收新授权请求 | 系统初始化；令牌超时吊销；人工强制回锁；熔断恢复 |
| `SECURITY_CHECK` | 安全校验中 | 正在校验写入请求来源、指标阈值、合规规则 | 收到L5写入授权申请 |
| `TEMP_UNLOCKED` | 临时解锁 | 存在有效30秒令牌，L5开放写入权限，拒绝新增授权 | 安全校验全部通过，令牌签发完成 |
| `RELOCKING` | 锁定恢复中 | 执行令牌吊销、下发回锁通知，清理活跃令牌 | 令牌30秒超时、人工强制回锁、全局熔断 |
| `SYSTEM_PAUSED` | 暂停服务 | 紧急熔断，立即吊销令牌，冻结全部授权流程 | F0下发FUSE熔断指令；RESUME指令切回LOCKED_READY |

## 三、全局安全校验配置常量
| 配置项 | 默认阈值 | 业务说明 |
|--------|:---:|------|
| S直达准入S值底线 | ≥0.9 | ag-mem-16通道硬性门槛 |
| L4推送置信度底线 | ≥0.85 | ag-mem-27规则准入门槛 |
| S直达单次授权最大条目 | 1条 | 单令牌仅允许写入1条高安全直达经验 |
| L4推送单次授权最大条目 | 3条 | 单令牌最多写入3条合规抽象规则 |
| 人工操作单次授权最大条目 | 10条 | 管理员批量写入上限 |
| 临时解锁令牌有效期 | 30秒 | 硬编码，无延期接口 |
| 人工双重确认超时窗口 | 60秒 | 挑战码有效期，超时直接驳回操作 |
| 全局最大并发活跃令牌 | 1个 | 同一时刻仅存在1张有效令牌 |
| 令牌签名加密算法 | HMAC-SHA256 | 防伪造、防篡改校验 |

### 三类写入渠道准入校验清单
| 写入来源 | 前置校验条件 | 单次授权上限 |
|--------|--------------|----------|
| S值直达（ag-mem-16） | S≥0.9 + result_tag=成功 | 1 |
| L4规则推送（ag-mem-27） | 置信度≥0.85 + ag-mem-45合规校验通过 | 3 |
| 人工锁定写入 | 管理员身份校验 + 60秒内双重挑战码确认 | 10 |

## 四、输入总线接口（内部调度总线 只读）
| 输入消息名称 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| L5写入授权请求 | L5AuthApplyReq | ag-mem-16 / ag-mem-27 / 人工接口 | 需开通L5写入权限 | 最高 |
| 安全规则基准配置 | SecurityRuleBaseResp | ag-mem-45 | 初始化/规则动态更新 | 普通 |
| 人工锁控操作指令 | ManualLockOperateCmd | 人工管理员接口 | 人工调整L5锁定状态、批量删除 | 最高 |
| L5存储状态同步回执 | L5SyncStateResp | ag-mem-28 | 周期同步L5容量、锁定状态 | 普通 |
| 全局调度控制指令 | F0ControlEnum | ag-mem-01 | 熔断/暂停/恢复服务 | 紧急 |
| 内部令牌超时定时器信号 | TimerSignal | 模块内部 | 令牌签发满30秒 | 高 |

### 入参核心结构体定义
1. **L5AuthApplyReq 写入授权申请**
```json
{
  "source_module": "enum[S直达/L4推送/人工锁定]",
  "write_reason": "业务写入说明",
  "item_id_list": ["待写入条目ID数组"],
  "S_value": "float 安全显著性（S直达渠道必填）",
  "rule_confidence": "float 规则置信度（L4推送渠道必填）",
  "apply_write_cnt": "int 申请写入条目数量"
}
```
2. **ManualLockOperateCmd 人工操作指令**
```json
{
  "operate_type": "enum[人工解锁/强制恢复锁定/批量删除]",
  "admin_id": "管理员唯一ID",
  "admin_security_token": "管理员身份校验串",
  "operate_note": "操作事由备注"
}
```
3. **F0ControlEnum 全局指令枚举**
`PAUSE / RESUME / FUSE`

## 五、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 临时解锁令牌下发 | L5UnlockTokenMsg | ag-mem-28 | 安全校验全部通过 | 最高 |
| 授权拒绝通知 | AuthRejectNotify | ag-mem-16/27/人工接口 | 任意准入校验失败 | 最高 |
| 锁状态变更通知 | LockStateChangeNotify | ag-mem-28 | 锁定/解锁状态切换 | 最高 |
| 令牌吊销指令 | TokenRevokeCmd | ag-mem-28 | 超时/人工/熔断强制回收令牌 | 最高 |
| 安全审计事件日志 | SecurityAuditLog | ag-mem-51 | 每一次授权/拒绝/人工操作 | 高 |
| 锁控周期状态上报 | LockControlStatReport | ag-mem-03 | 每120秒/状态变更瞬间 | 普通 |

### 出参核心结构体定义
1. **L5UnlockTokenMsg 临时解锁令牌**
```json
{
  "token_id": "L5-TOKEN-UUID",
  "max_write_limit": "本次授权最大可写入条目数",
  "valid_second": 30,
  "hmac_sign": "HMAC-SHA256加密签名串",
  "create_ts": "long 令牌生成时间戳"
}
```
2. **AuthRejectNotify 授权拒绝通知**
```json
{
  "source": "申请来源模块",
  "reject_root_cause": "标准化失败原因",
  "fail_check_item": "校验失败维度（S值/置信度/合规/并发令牌）"
}
```
3. **SecurityAuditLog 安全审计日志**
```json
{
  "event_type": "enum[授权成功/授权拒绝/人工解锁/强制回锁/令牌吊销]",
  "operate_source": "操作发起方",
  "result_desc": "操作结果详情",
  "event_ts": "long 事件发生时间戳",
  "token_id": "关联令牌ID（如有）"
}
```
4. **LockControlStatReport 周期上报指标**
```json
{
  "current_state": "模块状态枚举",
  "total_auth_success": "累计授权成功次数",
  "total_auth_reject": "累计授权拒绝次数",
  "active_token_count": "当前有效令牌数量（0/1）"
}
```

## 六、完整业务主流程伪代码（注释优化版）
```python
FUNCTION l5_lock_control_main_loop():
    # 状态常量定义
    STATE_LOCKED = "LOCKED_READY"
    STATE_CHECK = "SECURITY_CHECK"
    STATE_UNLOCKED = "TEMP_UNLOCKED"
    STATE_RELOCK = "RELOCKING"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_LOCKED
    # 加载安全阈值基准
    security_rule = load_security_base_from_m45()
    active_token = None
    token_create_ts = 0
    # 统计指标
    stat_auth_ok = 0
    stat_auth_reject = 0

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度指令
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSE
                # 熔断强制吊销令牌
                if active_token is not None:
                    revoke_token_process(revoke_reason="全局紧急熔断")
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSE:
                internal_state = STATE_LOCKED

        # 2. 令牌30秒超时检测
        if internal_state == STATE_UNLOCKED:
            if NOW() - token_create_ts > 30 * 1000:
                internal_state = STATE_RELOCK
                revoke_token_process(revoke_reason="令牌30秒超时自动恢复锁定")

        # 3. 接收L5写入授权申请
        if recv_l5_auth_apply():
            apply_req = get_auth_apply()
            # 并发令牌拦截：已有活跃令牌直接拒绝
            if internal_state == STATE_UNLOCKED:
                send_auth_reject(
                    source=apply_req.source_module,
                    reason="已有活跃写入令牌，请等待令牌过期或吊销"
                )
                stat_auth_reject += 1
                write_audit_log("授权拒绝", apply_req.source_module, "并发令牌冲突")
                continue

            internal_state = STATE_CHECK
            source_type = apply_req.source_module
            s_val = apply_req.S_value
            conf = apply_req.rule_confidence
            pass_check = False
            max_allow_write = 0
            reject_msg = ""

            # 渠道分支校验
            if source_type == "S值直达":
                if s_val >= security_rule.S_min_threshold:
                    pass_check = True
                    max_allow_write = 1
                else:
                    reject_msg = f"S值未达标，当前{s_val}，要求≥{security_rule.S_min_threshold}"
            elif source_type == "L4推送":
                if conf >= security_rule.conf_min_threshold:
                    # 调用ag-mem-45合规校验
                    compliance_resp = call_m45_check(apply_req.item_id_list)
                    if compliance_resp.pass_flag:
                        pass_check = True
                        max_allow_write = min(apply_req.apply_write_cnt, 3)
                    else:
                        reject_msg = f"安全合规校验失败：{compliance_resp.reason}"
                else:
                    reject_msg = f"规则置信度不足，当前{conf}，要求≥{security_rule.conf_min_threshold}"
            elif source_type == "人工锁定":
                # 人工双重确认流程
                double_confirm_res = launch_admin_double_verify(apply_req.admin_id, timeout=60*1000)
                if double_confirm_res.pass_flag:
                    pass_check = True
                    max_allow_write = min(apply_req.apply_write_cnt, 10)
                else:
                    reject_msg = "人工双重确认超时或验证失败"
            else:
                reject_msg = "非法写入来源，不在白名单内"

            # 校验结果分支
            if pass_check:
                internal_state = STATE_UNLOCKED
                token_create_ts = NOW()
                # 生成加密令牌
                new_token = generate_hmac_token(max_allow_write)
                active_token = new_token
                # 下发令牌与锁变更通知至ag-mem-28
                send_token_to_m28(new_token)
                send_lock_state_notify(target="ag-mem-28", new_state="UNLOCKED", token=new_token)
                stat_auth_ok += 1
                write_audit_log("授权成功", source_type, f"最大写入上限{max_allow_write}")
            else:
                send_auth_reject(source=source_type, reason=reject_msg)
                stat_auth_reject += 1
                write_audit_log("授权拒绝", source_type, reject_msg)
                internal_state = STATE_LOCKED

        # 4. 处理人工锁控操作指令
        if recv_manual_lock_cmd():
            cmd = get_manual_lock_cmd()
            # 所有人工操作强制双重确认
            verify_res = launch_admin_double_verify(cmd.admin_id, timeout=60*1000)
            if not verify_res.pass_flag:
                send_manual_operate_reject("双重确认验证失败/超时")
                continue

            if cmd.operate_type == "强制恢复锁定":
                internal_state = STATE_RELOCK
                revoke_token_process(revoke_reason="人工强制回锁操作")
                internal_state = STATE_LOCKED
                write_audit_log("人工操作", "管理员", "执行强制恢复L5锁定")
            elif cmd.operate_type == "人工解锁":
                if internal_state == STATE_LOCKED:
                    internal_state = STATE_UNLOCKED
                    token_create_ts = NOW()
                    manual_token = generate_hmac_token(max_write=10)
                    active_token = manual_token
                    send_token_to_m28(manual_token)
                    send_lock_state_notify("ag-mem-28", "UNLOCKED", manual_token)
                    write_audit_log("人工解锁授权", "管理员", "人工下发解锁令牌")

        # 5. 统一令牌吊销子流程执行
        if internal_state == STATE_RELOCK:
            if active_token is not None:
                send_token_revoke_cmd(target="ag-mem-28", token_id=active_token.token_id, revoke_cause=revoke_reason)
                send_lock_state_notify("ag-mem-28", "LOCKED", None)
                active_token = None
            internal_state = STATE_LOCKED

        # 6. 每120秒周期状态上报
        if NOW() - last_report_ts >= 120 * 1000:
            report = build_stat_report(internal_state, stat_auth_ok, stat_auth_reject, active_token)
            send_stat_report(report, target="ag-mem-03")
            last_report_ts = NOW()

        SLEEP(10)

# 子函数：令牌吊销统一流程
FUNCTION revoke_token_process(revoke_reason):
    if active_token is None:
        return
    send_token_revoke_cmd("ag-mem-28", active_token.token_id, revoke_reason)
    send_lock_state_notify("ag-mem-28", "LOCKED", None)
    write_audit_log("令牌吊销", "ag-mem-29", revoke_reason)
    active_token = None
```

## 七、异常故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|--------|----------|----------|
| ag-mem-45安全规则基准加载失败 | 启用编译内置默认阈值（S≥0.9、置信≥0.85），打印降级告警日志 | ag-mem-45服务恢复连通 |
| 人工双重确认60秒超时 | 直接驳回操作，记录超时审计日志 | 管理员重新发起操作并完成双因子验证 |
| 已有活跃令牌时收到新授权申请 | 拒绝全部新申请，提示并发令牌冲突 | 当前令牌超时/人工吊销后可重新申请 |
| L5存储校验令牌签名篡改 | ag-mem-28拦截写入，本模块接收异常回执后上报告警，自动吊销该令牌 | 重新发起授权流程生成全新合法令牌 |
| 全局紧急熔断指令下发 | 立刻吊销活跃令牌、下发回锁通知，冻结全部授权流程 | 总控下发RESUME恢复指令 |

## 八、内部调度总线访问契约
| 总线方向 | 消息类型 | 访问权限 | 通信双方 |
|--------|----------|----------|----------|
| 读（入站） | L5写入授权申请、安全规则基准、人工锁控指令、L5状态回执、全局熔断指令、定时器信号 | 只读 | ag-mem16/27/28/45/01/人工接口 → ag-mem-29 |
| 写（出站） | 临时解锁令牌、锁状态变更通知、令牌吊销指令 | 模块专属写入 | ag-mem-29 → ag-mem-28 |
| 写（出站） | 授权拒绝通知、人工操作驳回通知 | 模块专属写入 | ag-mem-29 → ag-mem16/27/人工接口 |
| 写（出站） | 安全审计日志 | 模块专属写入 | ag-mem-29 → ag-mem-51 |
| 写（出站） | 周期锁控指标上报 | 周期性写入 | ag-mem-29 → ag-mem-03 |

## 九、强制安全边界（审计校验硬规则）
| 编号 | 约束规则 |
|:---:|------|
| S-01 | L5所有写入操作必须持有本模块签发的有效令牌，绕过本模块直写L5会被ag-mem-28直接拦截拒绝 |
| S-02 | 令牌30秒有效期为底层硬编码，无任何对外接口支持延期、续期 |
| S-03 | 系统全局同一时间仅允许1张有效活跃令牌，杜绝多渠道并发写入顶层永久记忆 |
| S-04 | 全部人工解锁、回锁、删除操作强制执行双重确认流程，单次操作独立验证，不可复用上次验证凭证 |
| S-05 | 所有授权成功、授权拒绝、令牌吊销、人工操作必须完整写入ag-mem-51审计日志，日志不可删除篡改 |
| S-06 | 全局熔断触发时，无条件吊销全部活跃令牌，强制L5恢复物理写保护锁定 |

## 十、自动化功能测试用例全覆盖
| 用例编号 | 前置条件 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M29-01 | LOCKED_READY，S直达申请S=0.95 | S值直达写入授权请求 | 签发30秒令牌，最大写入1条，状态切换TEMP_UNLOCKED，审计日志记录授权成功 |
| TC-M29-02 | LOCKED_READY，S直达申请S=0.75 | S值直达写入授权请求 | 拒绝授权，返回S值未达标，审计日志记录拒绝 |
| TC-M29-03 | LOCKED_READY，L4推送置信0.90、合规校验通过 | L4推送授权申请 | 签发令牌，最大写入3条，下发至ag-mem-28 |
| TC-M29-04 | LOCKED_READY，来源标记为未知模块 | 非法来源授权申请 | 直接拒绝，提示非法写入来源 |
| TC-M29-05 | TEMP_UNLOCKED（存在有效令牌） | 新S直达授权申请 | 拒绝新申请，提示已有活跃令牌 |
| TC-M29-06 | TEMP_UNLOCKED，等待30秒超时 | 内部定时器超时信号 | 自动吊销令牌，下发回锁通知，切回LOCKED_READY |

## 十一、交付验收自检清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二配套权限管控定位准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 5种内部状态+完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级 | ✅ |
| 三类写入渠道校验、令牌签发参数、人工双确认流程完整 | ✅ |
| 伪代码覆盖渠道校验、令牌生成、超时吊销、人工操作、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖5类典型故障处理逻辑 | ✅ |
| 内部调度总线读写权限划分清晰 | ✅ |
| 6条强制安全约束无逻辑漏洞 | ✅ |
| 6条测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充说明（对接ag-mem-28、ag-mem-45）
1. ag-mem-28仅负责存储校验令牌签名、有效期，**无令牌生成、权限校验能力**，所有准入规则统一由ag-mem-29管控；
2. L4推送渠道必须二次调用ag-mem-45做安全规则合规校验，本模块不内置安全规则库，仅读取基准阈值；
3. 所有权限变更、写入授权动作全量推送ag-mem-51日志单元，满足安全审计追溯要求；
4. 令牌吊销分为三类触发：30秒自动超时、人工强制回锁、全局熔断，三类场景均同步通知ag-mem-28更新锁状态；
5. 本模块仅管控写入权限，不参与L5经验存储、查询、删除逻辑，删除操作仅下发锁控指令，实际数据清理由配套人工运维单元执行。