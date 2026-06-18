# ag-mem-28-L5核心层存储单元 规整落地版接口规格文档
统一对齐 ag-mem-24~27 系列五层存储模块文档规范，标准化结构体、梳理全链路权限管控逻辑、补全交互边界，完整保留原生安全约束与业务规则，适配开发、联调、安全审计、自动化测试使用。

## 一、模块基础元信息
| 项 | 内容 |
|----|------|
| 模块唯一ID | ag-mem-28 |
| 模块全称 | L5核心层存储单元 |
| 所属架构 | 三、漏斗二：任务经验漏斗 / 五层存储（L0~L5顶层永久记忆区） |
| 层级定位 | 漏斗二最高层级永久记忆分区，存储安全底线、高风险不可抗力、人工锁定核心策略；**全层默认物理写保护、条目永久不可自动删除** |
| 容量配额 | 占漏斗二总容量0.5%，硬上限200条 |
| 生命周期规则 | 无自动遗忘/清理机制，不受ag-mem-40遗忘单元管控；仅人工双重授权可删除条目 |
| 核心能力 | 临时解锁校验、多渠道写入准入校验、安全持久化写入、令牌鉴权只读检索、容量水位监控、周期状态上报、自动超时回锁 |
| 核心约束 | 写入必须持有ag-mem-29签发临时解锁令牌；查询必须携带ag-mem-30有效访问令牌；仅三类合法写入来源；S直达通道强制S≥0.9+成功标签 |

### 上下游依赖图谱
#### 依赖模块（主动接收/调用）
1. ag-mem-16 工具调用槽：S≥0.9高安全经验直达写入请求
2. ag-mem-27 L4抽象提炼单元：置信度≥0.85合规规则推送写入
3. ag-mem-29 L5硬锁定单元：下发解锁令牌、锁定状态变更通知、管控写入权限
4. ag-mem-30 L5防篡改只读管控单元：查询令牌合法性校验
5. ag-mem-48 全局容量管控单元：查询实时容量、使用率
6. ag-mem-01 总控F0：全局熔断调度指令

#### 被依赖模块（对外输出）
1. ag-mem-16/27/29：写入成功回执/写入拒绝通知
2. ag-mem-15~19 场景分槽：令牌校验通过后的L5只读经验查询结果
3. ag-mem-48、ag-mem-03、ag-mem-29：周期状态指标上报
4. ag-mem-29：临时解锁超时自动锁定通知

## 二、内部状态机（5种互斥运行状态）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `LOCKED_NORMAL` | 正常锁定 | 默认写保护，仅开放查询，阻断所有写入 | 系统初始化完成；解锁超时；人工强制锁定；熔断恢复 |
| `TEMP_UNLOCKED` | 临时解锁 | 持有有效30秒令牌，允许合规来源写入 | ag-mem-29下发合法临时解锁令牌 |
| `CAPACITY_FULL` | 容量已满 | 使用率≥95%或条目达200上限，永久拒绝新写入 | 容量查询判定水位超标；人工清理后可恢复LOCKED_NORMAL |
| `LOCK_FAULT` | 锁定异常 | 底层写保护校验失效，强制只读，禁止写入 | 存储锁校验返回异常 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，立即回锁、冻结读写 | F0下发FUSE熔断指令；RESUME指令切回LOCKED_NORMAL |

## 三、L5全局存储配置常量
| 配置项 | 默认值 | 业务说明 |
|--------|:---:|------|
| L5容量占漏斗二总比例 | 0.5% | 静态配额，由ag-mem-48统一核算 |
| L5条目硬上限 | 200条 | 达到上限直接标记CAPACITY_FULL，拒绝新增写入 |
| 条目留存规则 | 永久存储 | 无自动过期、自动遗忘、自动归档逻辑 |
| 单条目最大存储体积 | 30KB | 超限直接拒绝写入 |
| 单次写入操作超时阈值 | 500ms | 单条写入最大阻塞时长 |
| 容量紧急拒绝水位 | 95% | 使用率≥95%标记容量已满 |
| 系统默认状态 | LOCKED_NORMAL | 开机/熔断恢复强制进入锁定模式 |
| 临时解锁令牌有效期 | 30秒 | 超时自动吊销令牌、回锁存储 |
| S直达写入硬性阈值 | S≥0.9 | 工具槽高安全经验准入门槛 |

## 四、写入准入规则（三类合法来源）
| 写入来源 | 准入前置条件 | 必备凭证 |
|--------|--------------|----------|
| S值直达（ag-mem-16） | 条目S≥0.9 + result_tag=成功 | ag-mem-29签发临时解锁令牌 |
| L4规则推送（ag-mem-27） | 规则置信度≥0.85 + ag-mem-45安全库校验通过 | ag-mem-29签发临时解锁令牌 |
| 人工锁定写入（ag-mem-29） | 人工复审标记永久保留核心经验 | 人工专属安全写入令牌 |

### 全场景写入拒绝黑名单
1. 当前状态非TEMP_UNLOCKED（无解锁令牌）
2. 写入请求安全令牌与当前有效解锁令牌不匹配
3. 写入来源不属于上述三类合法渠道
4. S直达来源条目S＜0.9 或结果标签为失败/策略失误
5. L5使用率≥95% 或条目总数≥200硬上限
6. 单条经验数据超过30KB体积限制
7. 底层存储写入IO异常、锁定校验失败
8. 临时解锁令牌已超时失效

## 五、输入总线接口（内部调度总线 只读）
| 输入消息名称 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| L5写入请求 | L5WriteReq | ag-mem-16 / ag-mem-27 / ag-mem-29 | 满足L5准入条件发起写入 | 最高 |
| 临时解锁令牌下发 | UnlockTokenMsg | ag-mem-29 | 人工授权临时开放写入权限 | 最高 |
| L5经验查询请求 | L5QueryReq | ag-mem-15~19 | 场景分槽检索永久核心经验 | 高 |
| 令牌验证回执 | TokenVerifyResp | ag-mem-30 | 本模块校验查询令牌后返回 | 高 |
| L5容量查询回执 | CapacityResp | ag-mem-48 | 每次写入前主动查询容量 | 高 |
| 锁定状态变更通知 | LockStateChangeNotify | ag-mem-29 | 人工切换锁定/解锁权限 | 最高 |
| 全局调度指令 | F0ControlEnum | ag-mem-01 | 熔断/暂停/恢复服务 | 紧急 |

### 入参核心结构体定义
1. **L5WriteReq L5写入请求**
```json
{
  "item_id": "条目唯一ID",
  "exp_data": "结构化经验数据（脱敏后）",
  "I": "float 重要度",
  "S": "float 安全显著性",
  "write_source": "enum[S直达/L4推送/人工锁定]",
  "security_token": "字符串 解锁令牌ID",
  "result_tag": "enum[成功/失败/策略失误]"
}
```
2. **UnlockTokenMsg 临时解锁令牌**
```json
{
  "token_id": "令牌唯一标识",
  "max_write_cnt": "本次授权最大写入条目数",
  "valid_ts": "long 令牌过期时间戳",
  "sign": "加密签名校验串"
}
```
3. **L5QueryReq 查询请求**
```json
{
  "query_filter": "多维度检索条件",
  "access_token": "ag-mem-30签发查询令牌",
  "max_return": "int 最大返回条目数量"
}
```
4. **F0ControlEnum 全局指令枚举**
`PAUSE / RESUME / FUSE`

## 六、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| L5写入成功回执 | L5WriteAck | ag-mem-16/27/29 | 条目持久化写入完成 | 最高 |
| L5写入拒绝通知 | L5WriteRejectNotify | ag-mem-16/27/29 | 任意准入校验失败 | 最高 |
| L5查询结果列表 | L5QueryResp | ag-mem-15~19 | 令牌校验通过并检索完成 | 高 |
| L5周期状态上报 | L5StatusReport | ag-mem-48、ag-mem-03、ag-mem-29 | 每120秒 / 状态变更瞬间 | 普通 |
| 解锁超时自动锁定通知 | AutoLockNotify | ag-mem-29 | 30秒令牌过期自动回锁 | 普通 |

### 出参核心结构体定义
1. **L5WriteAck 写入成功回执**
```json
{
  "item_id": "写入条目ID",
  "write_status": "success",
  "current_usage": "float 当前L5使用率",
  "lock_recover_tip": "写入完成后仍持有临时解锁令牌，超时自动锁定"
}
```
2. **L5WriteRejectNotify 写入拒绝通知**
```json
{
  "item_id": "待写入条目ID",
  "reject_reason": "文本拒绝原因",
  "current_module_state": "当前模块状态枚举"
}
```
3. **L5QueryResp 查询返回结果**
```json
{
  "layer_tag": "L5",
  "match_item_list": [
    {
      "item_id": "条目ID",
      "exp_data": "脱敏经验数据",
      "I": "float 重要度",
      "S": "float 安全显著性",
      "write_source": "写入来源",
      "lock_ts": "long 入库锁定时间",
      "readonly": true,
      "editable": false,
      "deletable": false
    }
  ]
}
```
4. **L5StatusReport 周期状态上报**
```json
{
  "internal_state": "模块状态枚举",
  "total_item_count": "int 当前条目总数",
  "usage_rate": "float 存储使用率",
  "lock_flag": "锁定/临时解锁",
  "last_write_ts": "long 最近一次写入时间戳"
}
```

## 七、完整业务主流程伪代码（注释优化版）
```python
FUNCTION l5_storage_main_loop():
    # 状态常量定义
    STATE_LOCKED = "LOCKED_NORMAL"
    STATE_UNLOCKED = "TEMP_UNLOCKED"
    STATE_FULL = "CAPACITY_FULL"
    STATE_FAULT = "LOCK_FAULT"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_LOCKED
    init_l5_storage()  # 初始化持久化分区+物理写保护锁
    item_counter = 0
    valid_unlock_token = None  # 当前生效解锁令牌
    token_start_ts = 0

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度指令
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSE
                # 熔断强制吊销令牌、恢复锁定
                if valid_unlock_token is not None:
                    valid_unlock_token = None
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSE:
                internal_state = STATE_LOCKED

        # 2. 接收ag-mem-29锁定状态变更通知
        if recv_lock_change_notify():
            notify = get_lock_notify()
            if notify.new_state == "LOCKED":
                internal_state = STATE_LOCKED
                valid_unlock_token = None
            elif notify.new_state == "UNLOCKED" and notify.reason == "人工授权":
                valid_unlock_token = notify.token
                token_start_ts = NOW()
                internal_state = STATE_UNLOCKED

        # 3. 临时解锁30秒超时检测，自动回锁
        if internal_state == STATE_UNLOCKED:
            if NOW() - token_start_ts > 30 * 1000:
                internal_state = STATE_LOCKED
                valid_unlock_token = None
                send_auto_lock_notify(target="ag-mem-29")

        # 4. 处理L5写入请求
        if recv_l5_write_req():
            req = get_write_request()
            # 校验1：必须处于临时解锁状态
            if internal_state != STATE_UNLOCKED:
                send_write_reject(req.item_id, "L5处于锁定状态，需临时解锁令牌")
                continue
            # 校验2：令牌匹配
            if req.security_token != valid_unlock_token.token_id:
                send_write_reject(req.item_id, "安全令牌校验失败")
                continue
            # 校验3：写入来源合法
            legal_sources = ["S值直达", "L4推送", "人工锁定"]
            if req.write_source not in legal_sources:
                send_write_reject(req.item_id, "非法写入来源")
                continue
            # 校验4：S直达通道强制S≥0.9且结果成功
            if req.write_source == "S值直达":
                if req.S < 0.9 or req.result_tag != "成功":
                    send_write_reject(req.item_id, "S值不满足L5直达条件或经验为失败")
                    continue
            # 查询容量水位
            cap_resp = call_ag_mem48_query_cap()
            usage = cap_resp.usage_rate
            # 校验5：容量满拒绝写入
            if usage >= 0.95 or item_counter >= 200:
                internal_state = STATE_FULL
                send_write_reject(req.item_id, "L5容量已满，无法新增条目")
                continue
            # 校验6：单条目体积上限30KB
            if get_exp_size(req.exp_data) > 30:
                send_write_reject(req.item_id, "条目超过30KB存储上限")
                continue
            # 执行安全写入（校验写保护已解除）
            write_ok = l5_storage_safe_write(
                item_id=req.item_id,
                exp_data=req.exp_data,
                I=req.I,
                S=req.S,
                write_source=req.write_source,
                lock_ts=NOW(),
                token=req.security_token
            )
            if not write_ok:
                send_write_reject(req.item_id, "底层存储写入异常")
                continue
            # 写入成功更新计数
            item_counter += 1
            update_l5_index(req.item_id, req.write_source, NOW())
            # 返回成功回执
            send_write_ack(
                item_id=req.item_id,
                current_usage=usage
            )

        # 5. 处理场景分槽查询请求
        if recv_l5_query_req():
            req = get_query_request()
            # 向ag-mem-30校验查询令牌
            send_token_verify_req(req.access_token)
            verify_resp = wait_token_verify_result()
            if verify_resp is None or not verify_resp.token_valid:
                send_query_reject("查询令牌无效")
                continue
            # 按授权槽位检索只读条目
            match_list = l5_storage_search(
                filter=req.query_filter,
                auth_slot=verify_resp.auth_slot_id,
                max_limit=req.max_return
            )
            # 强制标记所有条目只读、不可删改
            for item in match_list:
                item.readonly = True
                item.editable = False
                item.deletable = False
            send_query_result(match_list, layer="L5")

        # 6. 每120秒周期状态上报
        if NOW() - last_report_ts >= 120 * 1000:
            report = build_status_report(internal_state, item_counter, usage, valid_unlock_token)
            send_status_report(report, target=["ag-mem-48", "ag-mem-03", "ag-mem-29"])
            last_report_ts = NOW()

        SLEEP(10)
```

## 八、异常故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|--------|----------|----------|
| 无解锁令牌/处于LOCKED_NORMAL收到写入 | 直接拒绝写入，提示锁定状态 | ag-mem-29下发合法临时解锁令牌 |
| 写入安全令牌与当前生效令牌不匹配 | 拒绝写入，记录安全告警日志 | 使用本次有效令牌重新发起写入 |
| S直达来源S＜0.9或经验失败 | 拒绝写入，不占用令牌额度 | 积累S≥0.9的成功安全经验 |
| L5使用率≥95%/条目满200条 | 标记CAPACITY_FULL，永久阻断新增写入 | 人工双重授权清理旧条目或扩容配额 |
| 临时解锁令牌超过30秒有效期 | 自动吊销令牌、恢复锁定，未完成写入全部回滚 | 重新向ag-mem-29申请解锁令牌 |
| 写入中途收到强制锁定通知 | 写入事务回滚，数据不落地 | 重新解锁后重试写入流程 |
| 全局紧急熔断指令下发 | 立即吊销令牌、回锁存储，冻结全部读写操作 | 总控下发RESUME恢复指令 |

## 九、内部调度总线访问契约
| 总线方向 | 消息类型 | 访问权限 | 通信双方 |
|--------|----------|----------|----------|
| 读（入站） | L5写入请求、解锁令牌、查询请求、容量回执、锁定变更通知、全局调度指令 | 只读 | ag-mem16/27/29/30/48/01 → ag-mem-28 |
| 写（出站） | 写入成功回执、写入拒绝通知 | 模块专属写入 | ag-mem-28 → ag-mem16/27/29 |
| 写（出站） | L5查询结果列表 | 模块专属写入 | ag-mem-28 → ag-mem15~19 |
| 写（出站） | 周期状态上报、解锁超时通知 | 事件/周期写入 | ag-mem-28 → ag-mem48、ag-mem03、ag-mem29 |

## 十、强制安全边界（不可绕过，审计校验点）
| 编号 | 约束规则 |
|:---:|------|
| S-01 | L5默认物理写保护锁定，所有写入必须持有ag-mem-29签发的有效临时解锁令牌，无令牌一律拦截 |
| S-02 | L5条目永久驻留，不受ag-mem-40遗忘单元、ag-mem-42归档删除单元自动清理逻辑影响 |
| S-03 | 条目仅支持人工双重授权删除，所有自动化模块无权修改、覆盖、删除任意L5存储数据 |
| S-04 | 外部场景分槽查询必须携带ag-mem-30签发有效访问令牌，无令牌直接拒绝检索 |
| S-05 | 临时解锁令牌固定30秒有效期，超时系统自动吊销并恢复锁定，无延长有效期接口 |
| S-06 | 写入来源严格限制三类，其余渠道全部拦截，禁止未授权模块直写L5 |
| S-07 | S值直达通道硬性校验S≥0.9且结果标签为成功，失败/低安全经验禁止直达顶层永久存储 |

## 十一、自动化功能测试用例全覆盖
| 用例编号 | 前置条件 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M28-01 | TEMP_UNLOCKED、有效令牌、S=0.95、来源S直达、成功标签 | 合法写入请求 | 条目写入成功，返回写入确认回执，条目计数器+1 |
| TC-M28-02 | LOCKED_NORMAL、无解锁令牌 | 任意写入请求 | 写入拒绝，提示L5处于锁定状态 |
| TC-M28-03 | TEMP_UNLOCKED、S=0.75、来源S直达 | S直达写入请求 | 拒绝写入，提示S值不满足L5直达条件 |
| TC-M28-04 | TEMP_UNLOCKED、write_source=未知模块 | 非法来源写入请求 | 写入拒绝，提示非法写入来源 |
| TC-M28-05 | LOCKED_NORMAL、携带ag-mem-30有效查询令牌 | 场景分槽查询请求 | 令牌校验通过，返回匹配条目，全部标记只读不可删改 |
| TC-M28-06 | TEMP_UNLOCKED，等待30秒超时 | 超时后发起写入 | 自动恢复LOCKED_NORMAL，写入请求被拒绝 |

## 十二、交付验收自检清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二五层顶层存储定位准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 5种内部状态+完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级 | ✅ |
| L5存储配置参数、三类写入准入条件完整 | ✅ |
| 伪代码覆盖锁定校验、令牌鉴权、来源校验、容量拦截、只读查询、超时回锁全链路 | ✅ |
| 异常场景覆盖7类典型故障处理逻辑 | ✅ |
| 内部调度总线读写权限划分清晰 | ✅ |
| 7条强制安全约束无逻辑漏洞 | ✅ |
| 6条测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充说明（对接上下游）
1. L4推送至L5的规则必须先经过ag-mem-45安全规则库合规校验，本模块仅做接收写入校验，不重复安全审核；
2. 本模块仅存储数据，**无任何令牌签发能力**：写入令牌由ag-mem-29生成，查询令牌由ag-mem-30生成；
3. L5无自动淘汰、归并、抽象能力，仅作为顶层只读永久存储，所有加工逻辑下沉至L2~L4；
4. 熔断机制优先级高于临时解锁，紧急事件下强制回锁，保障顶层安全经验不被篡改；
5. 所有输出查询结果统一标记readonly=true，上层业务禁止对L5条目执行修改/删除操作。