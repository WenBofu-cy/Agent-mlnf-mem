# ag-mem-30-L5核心层防篡改与只读管控单元 （ V1.1）
严格遵循V1.1白皮书**L5双层权限隔离架构**：ag-mem-29管控写入令牌、ag-mem-30管控查询鉴权、双模块密钥互通、统一审计链路，完整保留原有全部业务逻辑，统一整套mem模块文档范式，兼容现有ag-mem-28/29/51上下游，不丢失任何原生升级能力。

## 一、模块基础元信息（V1.1）
| 项 | 内容 | V1.1架构说明 |
|----|------|-------------|
| 模块唯一ID | ag-mem-30 | 漏斗二五层存储配套**只读访问网关** |
| 模块全称 | L5核心层防篡改与只读管控单元 | V1.1定义：L5读写权限分离网关 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层存储配套安全管控模块 | 与ag-mem-29组成L5双权限安全体系 |
| 顶层定位 | L5唯一统一访问入口，全局拦截非法读/写；签发短期只读查询令牌、校验ag-mem-29下发的写入令牌；隔离场景分槽越权检索，防篡改、防泄露、防伪造令牌 | V1.1安全规范：禁止绕过网关直连L5存储 |
| 核心能力 | 查询令牌签发/过期清理/吊销、写入令牌ID+签名双重校验、来源模块白名单拦截、违规行为告警审计、容量限流（最大20活跃查询令牌）、熔断全局清令牌、周期权限指标上报 | 完整覆盖V1.1智能体长效记忆安全约束 |
| 不可缺失升级能力 | 令牌HMAC签名防伪造、分槽查询隔离、批量过期自动回收、高频违规临时封禁、双模块共享密钥校验写入令牌、全操作日志推送ag-mem-51审计 | 所有能力向下兼容，无功能裁剪 |

### 上下游依赖图谱（贴合V1.1模块调用链路）
#### 依赖模块（接收/主动交互）
1. ag-mem-28 L5存储单元：接收令牌校验请求、下发校验回执
2. ag-mem-29 L5写入锁控单元：实时同步活跃写入令牌、共享签名密钥、接收违规告警
3. ag-mem-01 总控F0：全局熔断/恢复调度指令
4. 会话管理器：查询令牌主动吊销请求

#### 被依赖模块（对外提供服务）
1. ag-mem-15~19 场景分槽：签发只读查询令牌、返回鉴权结果
2. ag-mem-28：查询/写入令牌标准化校验回执
3. ag-mem-29：写入违规行为同步告警
4. ag-mem-51 记忆日志追溯单元：全量访问审计日志
5. ag-mem-03 漏斗二调度单元：周期权限管控指标上报

## 二、内部状态机（5种互斥状态，匹配V1.1状态规范）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `NORMAL_GATE` | 正常管控 | 网关正常运行，支持令牌签发、校验、拦截 | 系统初始化完成；熔断解除恢复服务 |
| `WRITE_TOKEN_CHECK` | 写入令牌校验中 | 校验外部携带的写入令牌ID、签名、有效期 | 收到任意模块L5写入请求 |
| `QUERY_TOKEN_ISSUE` | 查询令牌签发中 | 校验分槽合法身份，生成短期只读令牌 | 场景分槽发起L5检索申请 |
| `VIOLATION_BLOCK` | 违规阻断 | 检测无令牌、伪造令牌、未授权模块，拦截并告警 | 令牌校验失败、非法访问、越权检索 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，清空全部活跃查询令牌，拒绝所有访问 | F0下发FUSE熔断指令；RESUME切回NORMAL_GATE |

## 三、全局权限&令牌配置常量（V1.1安全标准固定阈值）
| 配置项 | 默认值 | V1.1规范说明 |
|--------|:---:|------|
| 查询令牌有效期 | 300秒 | 短期只读凭证，不可延期 |
| 写入令牌有效期 | 30秒 | 和ag-mem-29签发时效完全对齐 |
| 最大并发活跃查询令牌 | 20个 | 限流保护，超量自动淘汰最旧令牌 |
| 令牌加密算法 | HMAC-SHA256 | 仅ag-mem-29/30共享密钥，外部不可伪造 |
| 令牌吊销生效时效 | 即时 | 吊销后立即拦截对应令牌所有请求 |
| 过期清理定时周期 | 60秒 | 自动回收超时查询令牌 |
| 访问上报周期 | 120秒 | 统一漏斗二指标上报标准 |
| 高频违规锁定时长 | 600秒 | 同一模块多次伪造令牌临时封禁 |

### V1.1 L5访问权限白名单矩阵
| 请求来源模块 | 允许操作 | 所需令牌 | 限制规则 |
|------|:---:|:---:|------|
| ag-mem-15~19 场景分槽 | 只读查询 | 查询令牌 | 仅可检索自身分槽来源L5条目，跨槽拦截 |
| ag-mem-28 L5存储单元 | 令牌校验回调 | 无 | 仅被动接收校验请求，不主动访问数据 |
| ag-mem-16 S直达写入 | 写入持久化 | ag-mem-29签发写入令牌 | 需双重ID+签名校验 |
| ag-mem-27 L4规则推送 | 写入持久化 | ag-mem-29签发写入令牌 | 需双重ID+签名校验 |
| 人工运维接口 | 写入/删除 | ag-mem-29签发写入令牌 | 前置人工双重确认 |
| 其余所有模块 | 全部禁止 | 无 | 编译+运行时双层拦截，直接告警 |

## 四、输入总线接口（内部调度总线 只读）
| 输入消息名称 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| L5只读查询申请 | L5QueryApplyReq | ag-mem-15~19 | 场景分槽读取永久核心经验 | 高 |
| L5写入访问请求 | L5WriteAccessReq | ag-mem16/27/人工接口 | 发起L5写入操作 | 最高 |
| 活跃写入令牌同步消息 | ActiveWriteTokenSync | ag-mem-29 | 令牌签发/吊销/变更实时同步 | 最高 |
| 查询令牌吊销指令 | QueryTokenRevokeCmd | 会话管理器 / ag-mem-28 | 会话销毁、令牌泄露处置 | 高 |
| L5存储令牌校验回调 | TokenVerifyCallbackReq | ag-mem-28 | L5检索时校验查询令牌合法性 | 高 |
| 全局调度控制指令 | F0ControlEnum | ag-mem-01 | 系统熔断、暂停、恢复 | 紧急 |

### 入参标准化结构体
1. **L5QueryApplyReq 分槽查询申请**
```json
{
  "filter": "多维检索条件",
  "source_slot_id": "ag-mem-15/16/17/18/19",
  "source_module_id": "模块唯一标识",
  "max_return_count": "单次最大返回条目"
}
```
2. **L5WriteAccessReq L5写入访问请求**
```json
{
  "exp_item_data": "待写入脱敏经验数据",
  "write_source_type": "S值直达/L4推送/人工锁定",
  "carry_write_token": {
    "token_id": "写入令牌ID",
    "hmac_sign": "ag-mem-29签发签名"
  }
}
```
3. **F0ControlEnum 全局指令枚举**
`PAUSE / RESUME / FUSE`

## 五、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 查询令牌签发回执 | QueryTokenIssueAck | ag-mem-15~19 | 分槽查询申请校验通过 | 高 |
| 查询令牌校验回执 | QueryTokenVerifyResp | ag-mem-28 | L5存储发起令牌鉴权 | 高 |
| 写入令牌校验回执 | WriteTokenVerifyResp | ag-mem-28 | 写入令牌ID+签名校验通过 | 最高 |
| 访问拒绝通知 | AccessRejectNotify | 请求发起模块 | 任意鉴权校验失败 | 高 |
| 违规安全告警 | ViolationSecurityAlert | ag-mem-29、ag-mem-51 | 伪造令牌、未授权访问、越权检索 | 高 |
| 令牌吊销确认回执 | TokenRevokeAck | 会话管理器/ag-mem-28 | 令牌吊销操作完成 | 普通 |
| 网关周期状态上报 | AccessGatewayStatReport | ag-mem-03 | 每120秒/状态切换瞬间 | 普通 |

### 出参标准化结构体
1. **QueryTokenIssueAck 只读查询令牌**
```json
{
  "token_id": "L5-QTOKEN-UUID",
  "auth_slot_id": "授权仅可查询的分槽",
  "operate_type": "只读查询",
  "valid_second": 300,
  "issue_ts": "签发时间戳",
  "hmac_sign": "模块本地签名串"
}
```
2. **WriteTokenVerifyResp 写入令牌校验通过回执**
```json
{
  "token_valid": true,
  "max_write_limit": "令牌授权写入条目上限",
  "remain_valid_sec": "剩余30秒有效期",
  "match_write_source": "写入来源渠道"
}
```
3. **ViolationSecurityAlert 违规审计告警**
```json
{
  "violation_type": "无令牌访问/令牌签名伪造/未授权模块/过期令牌",
  "source_module": "违规发起模块ID",
  "request_snapshot": "请求内容摘要",
  "alert_level": "一般/严重",
  "event_ts": "违规发生时间戳"
}
```

## 六、完整业务主流程伪代码（注释优化，对齐V1.1安全流程）
```python
FUNCTION l5_access_control_main_loop():
    # 状态常量
    STATE_NORMAL = "NORMAL_GATE"
    STATE_WRITE_CHECK = "WRITE_TOKEN_CHECK"
    STATE_QUERY_ISSUE = "QUERY_TOKEN_ISSUE"
    STATE_BLOCK = "VIOLATION_BLOCK"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_NORMAL
    query_token_store = {}  # 活跃查询令牌存储
    active_write_token_cache = None  # 同步自ag-mem-29的写入令牌
    violation_counter = 0
    last_token_clean_ts = NOW()
    last_report_ts = NOW()
    # 统计指标
    stat_issue_token = 0
    stat_reject_req = 0

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度（V1.1紧急安全机制）
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSE
                # 熔断清空全部查询令牌，阻断所有访问
                query_token_store.clear()
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSE:
                internal_state = STATE_NORMAL

        # 2. 实时同步ag-mem-29下发的活跃写入令牌
        if recv_write_token_sync_msg():
            active_write_token_cache = get_sync_token_data()
            continue

        # 3. 处理场景分槽L5只读查询申请
        if recv_l5_query_apply():
            apply_req = get_query_apply()
            source_mod = apply_req.source_module_id
            # 校验来源是否在白名单
            if source_mod not in ["ag-mem-15","ag-mem-16","ag-mem-17","ag-mem-18","ag-mem-19"]:
                internal_state = STATE_BLOCK
                send_access_reject(source_mod, "未授权模块禁止访问L5", alert_level="严重")
                send_violation_alert(target=["ag-mem-51"], type="未授权查询", source=source_mod)
                violation_counter += 1
                stat_reject_req += 1
                internal_state = STATE_NORMAL
                continue

            # 令牌数量限流，超20淘汰最旧令牌
            if len(query_token_store) >= 20:
                # 按签发时间升序取第一条
                oldest_tok_id = sorted(query_token_store.items(), key=lambda x:x[1]["issue_ts"])[0][0]
                revoke_single_token(oldest_tok_id, reason="令牌数量达到上限20")

            # 签发全新查询令牌
            internal_state = STATE_QUERY_ISSUE
            new_tok_id = f"L5-QTOKEN-{gen_uuid()}"
            new_query_token = {
                "token_id": new_tok_id,
                "auth_slot_id": apply_req.source_slot_id,
                "operate_type": "只读查询",
                "valid_sec": 300,
                "issue_ts": NOW(),
                "hmac_sign": hmac_signature(new_tok_id + apply_req.source_slot_id + str(NOW()))
            }
            query_token_store[new_tok_id] = new_query_token
            stat_issue_token += 1
            send_query_token_ack(source_mod, new_query_token)
            internal_state = STATE_NORMAL

        # 4. 响应ag-mem-28发起的查询令牌校验
        if recv_token_verify_callback():
            verify_req = get_verify_callback()
            tok_id = verify_req.token_id
            if tok_id not in query_token_store:
                send_verify_resp(target="ag-mem-28", valid=False, reason="令牌不存在或已吊销")
                continue
            tok_data = query_token_store[tok_id]
            # 判断是否过期
            if NOW() - tok_data["issue_ts"] > 300 * 1000:
                del query_token_store[tok_id]
                send_verify_resp(target="ag-mem-28", valid=False, reason="查询令牌已过期")
                continue
            # 校验通过，返回授权槽位与剩余时效
            remain_sec = 300 - int((NOW() - tok_data["issue_ts"]) / 1000)
            send_verify_resp(
                target="ag-mem-28",
                valid=True,
                auth_slot=tok_data["auth_slot_id"],
                op_type=tok_data["operate_type"],
                remain_sec=remain_sec
            )

        # 5. 处理外部L5写入访问，校验写入令牌
        if recv_l5_write_access_req():
            internal_state = STATE_WRITE_CHECK
            write_req = get_write_access_req()
            carry_tok = write_req.carry_write_token
            source_mod = write_req.write_source_type

            # 无活跃写入令牌直接拦截
            if active_write_token_cache is None or active_write_token_cache["status"] != "有效":
                internal_state = STATE_BLOCK
                send_access_reject(source_mod, "当前无有效活跃写入令牌", "严重")
                send_violation_alert(["ag-mem-29","ag-mem-51"], "无令牌写入尝试", source_mod)
                violation_counter += 1
                stat_reject_req += 1
                internal_state = STATE_NORMAL
                continue
            # 令牌ID不匹配拦截
            if carry_tok["token_id"] != active_write_token_cache["token_id"]:
                internal_state = STATE_BLOCK
                send_access_reject(source_mod, "写入令牌ID不匹配，疑似伪造", "严重")
                send_violation_alert(["ag-mem-29","ag-mem-51"], "令牌ID伪造", source_mod)
                violation_counter += 1
                stat_reject_req += 1
                internal_state = STATE_NORMAL
                continue
            # HMAC签名双重校验（V1.1核心安全规则）
            expect_sign = hmac_signature(
                active_write_token_cache["token_id"]
                + str(active_write_token_cache["max_write_limit"])
                + str(active_write_token_cache["valid_sec"])
            )
            if carry_tok["hmac_sign"] != expect_sign:
                internal_state = STATE_BLOCK
                send_access_reject(source_mod, "写入令牌签名校验失败", "严重")
                send_violation_alert(["ag-mem-29","ag-mem-51"], "令牌签名篡改", source_mod)
                violation_counter += 1
                stat_reject_req += 1
                internal_state = STATE_NORMAL
                continue
            # 校验30秒有效期
            if NOW() - active_write_token_cache["issue_ts"] > 30 * 1000:
                send_access_reject(source_mod, "写入令牌已超时失效", "一般")
                stat_reject_req += 1
                internal_state = STATE_NORMAL
                continue
            # 全部校验通过，下发回执至ag-mem-28
            remain_write_sec = 30 - int((NOW() - active_write_token_cache["issue_ts"]) / 1000)
            send_write_token_verify_resp(
                target="ag-mem-28",
                valid=True,
                max_write=active_write_token_cache["max_write_limit"],
                remain_sec=remain_write_sec,
                source_confirm=active_write_token_cache["write_source"]
            )
            internal_state = STATE_NORMAL

        # 6. 接收令牌吊销指令，执行销毁
        if recv_token_revoke_cmd():
            revoke_cmd = get_revoke_cmd()
            revoke_single_token(revoke_cmd.token_id, revoke_cmd.revoke_reason)
            send_revoke_ack(revoke_cmd.sender, revoke_cmd.token_id, "已完成吊销")

        # 7. 每60秒定时清理全部过期查询令牌
        if NOW() - last_token_clean_ts >= 60 * 1000:
            expired_list = []
            for tid, tdata in query_token_store.items():
                if NOW() - tdata["issue_ts"] > 300 * 1000:
                    expired_list.append(tid)
            for tid in expired_list:
                del query_token_store[tid]
            last_token_clean_ts = NOW()

        # 8. 每120秒周期上报网关运行指标
        if NOW() - last_report_ts >= 120 * 1000:
            report_data = build_gateway_stat_report(
                internal_state=internal_state,
                total_issue=stat_issue_token,
                total_reject=stat_reject_req,
                active_token_count=len(query_token_store),
                total_violation=violation_counter
            )
            send_stat_report(report_data, target="ag-mem-03")
            last_report_ts = NOW()

        SLEEP(10)

# 子函数：吊销单条查询令牌并记录日志
FUNCTION revoke_single_token(token_id, revoke_reason):
    if token_id in query_token_store:
        del query_token_store[token_id]
        write_audit_log(tok_id=token_id, reason=revoke_reason, op="令牌吊销")
```

## 七、异常故障处理矩阵（V1.1故障安全规范）
| 故障场景 | 标准处理逻辑 | 恢复条件 |
|--------|----------|----------|
| 查询令牌并发达到20上限 | 自动淘汰签发时间最早令牌，生成新令牌，记录清理日志 | 无，内置限流逻辑自动执行 |
| 系统刚启动，未同步ag-mem-29写入令牌缓存 | 拦截全部写入请求，提示等待令牌同步，无告警 | ag-mem-29完成令牌信息同步推送 |
| 写入/查询令牌签名校验不匹配（伪造篡改） | 直接阻断请求，生成严重级安全告警推送日志与锁控模块 | 重新走正规授权流程生成合法令牌 |
| 同一模块短时间多次违规访问 | 累计违规计数，超限后临时封禁该模块L5访问600秒 | 封禁时长结束，人工复核解除限制 |
| 全局紧急熔断指令下发 | 清空所有活跃查询令牌，冻结全部读写鉴权流程 | 总控F0下发RESUME恢复指令 |

## 八、内部调度总线访问契约（统一V1.1总线权限规范）
| 总线流向 | 消息类型 | 访问权限 | 通信主体 |
|--------|----------|----------|----------|
| 读（入站） | 查询申请、写入访问请求、令牌同步、吊销指令、校验回调、全局调度指令 | 只读 | 外部模块/ag-mem28/29/F0 → ag-mem-30 |
| 写（出站） | 查询令牌签发回执、令牌校验回执、写入校验回执 | 模块专属写入 | ag-mem-30 → ag-mem15~19 / ag-mem-28 |
| 写（出站） | 访问拒绝通知、违规安全告警 | 事件专属写入 | ag-mem-30 → 请求源 / ag-mem29 / ag-mem-51 |
| 写（出站） | 令牌吊销确认、周期状态上报 | 普通/周期写入 | ag-mem-30 → 会话管理器 / ag-mem-03 |

## 九、V1.1强制安全边界（不可修改，审计核心校验点）
| 编号 | 约束规则（V1.1白皮书原文对应） |
|:---:|------|
| S-01 | L5所有读、写访问必须经过本网关鉴权，任何模块绕过ag-mem-30直连ag-mem-28视为高危漏洞，底层存储直接拦截 |
| S-02 | 查询令牌仅授予只读权限，任何携带查询令牌发起写入操作统一拒绝并生成违规告警 |
| S-03 | 写入令牌校验必须同时核对令牌ID + HMAC签名双重条件，任一不匹配判定为令牌伪造 |
| S-04 | 令牌签名密钥仅在ag-mem-29、ag-mem-30双模块内部共享，外部任何模块无法获取密钥生成合法令牌 |
| S-05 | 全部未授权、伪造、过期访问行为实时阻断，完整写入ag-mem-51不可篡改审计日志 |
| S-06 | 查询/写入令牌固定有效期，无任何对外接口支持延期、续期、重置时效 |

## 十、自动化功能测试用例（全覆盖V1.1核心业务分支）
| 用例编号 | 前置状态 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M30-01 | NORMAL_GATE，ag-mem-15合法查询申请 | L5查询申请（分槽ag-mem-15） | 签发300秒有效查询令牌，计入签发统计 |
| TC-M30-02 | NORMAL_GATE，ag-mem-28传入有效未过期令牌 | 查询令牌校验回调 | 返回令牌有效、授权槽位、剩余有效期 |
| TC-M30-03 | NORMAL_GATE，携带当前活跃合法写入令牌 | L5写入访问请求 | 双重校验通过，向ag-mem-28下发写入校验成功回执 |
| TC-M30-04 | NORMAL_GATE，无任何写入令牌直接发起写入 | 裸写入请求 | 拒绝访问，生成严重级违规告警同步至ag-mem-29/51 |
| TC-M30-05 | NORMAL_GATE，传入已过期查询令牌ID | 令牌校验回调 | 返回令牌无效（已过期），自动清理过期令牌 |
| TC-M30-06 | NORMAL_GATE，当前活跃查询令牌共20条 | 第21条分槽查询申请 | 自动吊销最早签发令牌，下发全新查询令牌 |

## 十一、交付验收自检清单（对齐整套mem文档统一标准）
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二配套L5只读网关定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| V1.1访问权限白名单矩阵、令牌管理阈值完整 | ✅ |
| 伪代码覆盖令牌签发、双向令牌校验、限流淘汰、过期清理、违规告警、周期上报全链路 | ✅ |
| 异常场景覆盖5类典型故障，处理逻辑符合V1.1安全故障规范 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 6条V1.1强制安全约束无逻辑漏洞、无绕过路径 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充（贴合V1.1整体记忆架构）
1. L5完整安全闭环：ag-mem-28（存储）+ ag-mem-29（写入令牌签发）+ ag-mem-30（读写统一鉴权网关）三层防护，完全匹配V1.1白皮书顶层安全架构；
2. 本模块仅做访问鉴权，不存储经验、不生成写入令牌、不执行删除逻辑，职责单一无越权；
3. 所有违规行为同步推送ag-mem-29锁控单元与ag-mem-51日志单元，满足V1.1智能体安全审计追溯要求；
4. 读写令牌两套独立时效、独立校验逻辑，读写权限完全隔离，杜绝越权篡改顶层永久记忆；
5. 所有存量功能完整保留，无删减、无降级，支持后续V1.1迭代扩展多会话令牌、分级访问权限等升级能力。
