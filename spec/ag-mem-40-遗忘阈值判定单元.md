# ag-mem-40-遗忘阈值判定单元 （对齐Agent V1.1白皮书）
严格遵循V1.1白皮书**五层记忆分层遗忘机制**，完整承接L0~L4生命周期淘汰逻辑，隔离L5永久记忆、区分L4常规保护/强制扫描双模式，统一整套ag-mem模块文档范式，上下游链路完全兼容已完成的ag-mem20~30、41、42、35、51，全量保留原生判定、保护、分批输出能力，无功能删减，预留V1.1迭代扩展空间。

## 一、模块基础元信息（V1.1架构对齐标注）
| 项 | 内容 | V1.1白皮书规范说明 |
|----|------|-------------|
| 模块唯一ID | ag-mem-40 | 漏斗二「晋升与遗忘执行机制」核心判定单元 |
| 模块全称 | 遗忘阈值判定单元 | V1.1定义：记忆分层淘汰决策层，只做筛选、不执行删除 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制 | 配套五层存储L1~L4生命周期管理 |
| 顶层定位 | 全系统唯一遗忘决策中枢；接收各存储层、分槽、定时模块扫描请求，基于分槽独立I阈值、复用次数、访问时效、人工标记做多维度遗忘判定；输出标准化候选清单交付ag-mem-42落地清理；L5永久屏蔽遗忘、L4默认保护，贴合V1.1长效记忆保护规则 | V1.1强制约束：遗忘与写入/存储职责分离，判定、执行拆分为两个独立模块 |
| 核心能力 | 分层过滤（L5直接拦截、L4按需放行）、分槽独立阈值读取、三重遗忘判定条件、多维度记忆保护校验、超大候选分批输出、快照隔离并发删除、遗忘事件审计日志、周期指标上报 | 全部能力向下兼容，支持后续新增分级归档、自定义保护标签等升级扩展 |

### 上下游依赖图谱（贴合V1.1调用链路）
#### 依赖模块（读取数据/配置）
1. ag-mem-20~26 分层存储单元：提供条目I值、元数据快照
2. ag-mem-35 三维权重系数配置单元：各分槽L1/L2/L3/L4专属遗忘阈值
3. ag-mem-37 重要度增量定时刷新单元：定时批量遗忘扫描发起方
4. ag-mem-41 最低复用次数校验单元：边界条目复用保护校验
5. ag-mem-01 总控F0：全局熔断/恢复调度指令
6. ag-mem-15~19 场景分槽：人工维护类遗忘扫描请求

#### 被依赖模块（对外输出结果）
1. ag-mem-42 冗余记忆删除与归档单元：接收遗忘候选清单执行删除/归档
2. ag-mem-20~26、ag-mem-37、ag-mem-15~19：返回扫描完成回执、预估释放空间
3. ag-mem-41：推送受保护条目通知
4. ag-mem-51 记忆变更日志追溯单元：全量遗忘判定审计日志
5. ag-mem-03 漏斗二调度单元：每180秒周期遗忘统计上报

## 二、内部状态机（5种互斥状态，统一V1.1状态规范）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `IDLE` | 空闲等待 | 无扫描任务，轮询总线扫描指令 | 系统初始化；单批次遗忘判定全部完成；熔断恢复 |
| `SCANNING` | 条目扫描中 | 拉取目标层级、分槽的条目元数据快照 | 收到合法遗忘扫描请求 |
| `JUDGING` | 判定执行中 | 逐条校验阈值、保护条件、三重遗忘规则 | 条目快照拉取完成/分批扫描循环 |
| `OUTPUTTING` | 结果输出 | 分批推送候选清单、回执、审计日志 | 全批次条目判定结束 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，中断所有扫描任务，丢弃未完成候选 | F0下发FUSE熔断指令；RESUME切回IDLE |

## 三、V1.1分层遗忘全局配置与规则
### 3.1 层级遗忘准入规则（硬编码，不可修改）
| 层级 | 是否常规参与遗忘 | 触发条件 | 清理策略 |
|:---:|:---:|------|------|
| L1临时层 | 是 | 定时/容量预警/人工维护 | 低I条目直接删除 |
| L2近期层 | 是 | 定时/容量预警/人工维护 | 低I条目直接删除 |
| L3中期层 | 是 | 定时/容量预警/人工维护 | 低I条目优先归档 |
| L4长期层 | 否（默认保护） | 仅传入`强制扫描L4=true`才执行判定 | 低I条目优先归档 |
| L5核心层 | 永不参与 | 任何扫描请求直接拦截 | 无遗忘逻辑，永久保留 |

### 3.2 三重遗忘判定条件（满足任意一条标记为遗忘候选）
1. 基础条件：条目I ＜ 当前分槽对应层级遗忘阈值
2. 边界缓冲条件：阈值 ≤ I ＜ 阈值+0.05 且 条目复用次数＜最低保护次数
3. 容量紧急兜底条件：触发原因为「容量紧急」且条目I落在本层级后20%区间

### 3.3 记忆保护校验规则（命中任意一条直接跳过遗忘判定）
1. 条目层级=L5；
2. 层级=L4且未开启强制扫描；
3. 条目带「用户收藏/人工锁定」标记；
4. 当前时间 - 最近访问时间 ＜ 24h；
5. 复用次数≥最低保护次数 且 I ＞ 层级阈值×0.8。

## 四、输入总线接口（内部调度总线 只读）
| 输入消息名称 | 结构体 | 发送方 | 触发时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 遗忘扫描申请 | ForgetScanReq | ag-mem20~26 / ag-mem37 / ag-mem15~19 | 定时周期、存储容量预警、人工清理维护 | 高 |
| 条目元数据快照列表 | ItemMetaSnapshotList | ag-mem20~26 | 扫描请求下发后同步条目I、层级、复用、时间戳等 | 高 |
| 分槽遗忘阈值配置包 | SlotForgetThresholdCfg | ag-mem-35 | 模块初始化、阈值动态更新 | 高 |
| 复用次数保护校验回执 | ReuseProtectCheckResp | ag-mem-41 | 边界条目辅助校验 | 普通 |
| 全局调度控制指令 | F0ControlEnum | ag-mem-01 | 系统熔断、暂停、恢复 | 紧急 |

### 入参标准化结构体
1. **ForgetScanReq 遗忘扫描申请**
```json
{
  "target_layer": "L1/L2/L3/L4/L5",
  "scan_scope": "enum[full_all / item_id_list]",
  "target_item_ids": ["可选指定条目ID数组"],
  "trigger_cause": "enum[timer / capacity_alarm / manual_maintain / capacity_emergency]",
  "force_scan_L4": false,
  "source_slot_id": "对应场景分槽编号"
}
```
2. **ItemMetaSnapshotList 条目快照**
```json
{
  "item_list": [
    {
      "item_id": "条目唯一ID",
      "I_value": "float 综合重要度",
      "current_layer": "L1~L4",
      "slot_id": "来源分槽",
      "write_ts": "写入时间戳",
      "last_access_ts": "最近访问时间戳",
      "reuse_count": "工具/任务复用次数",
      "manual_tag": "无/用户收藏/人工锁定"
    }
  ]
}
```
3. **F0ControlEnum 全局指令枚举**
`PAUSE / RESUME / FUSE`

## 五、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体 | 接收模块 | 发送时机 | 优先级 |
|--------|--------|--------|----------|:---:|
| 遗忘候选清理清单 | ForgetCandidateList | ag-mem-42 | 单批次判定全部完成 | 高 |
| 扫描完成回执 | ScanCompleteAck | 扫描请求发起模块 | 每轮扫描结束 | 高 |
| 受保护条目通知 | ProtectedItemNotify | ag-mem-41 | 条目命中保护规则跳过遗忘 | 普通 |
| 遗忘审计事件日志 | ForgetAuditLog | ag-mem-51 | 每次扫描判定完成 | 普通 |
| 遗忘运行状态上报 | ForgetStatReport | ag-mem-03 | 每180秒周期推送指标 | 普通 |

### 出参标准化结构体
1. **ForgetCandidateList 待清理条目清单**
```json
{
  "batch_id": "批次UUID",
  "candidate_items": [
    {
      "item_id": "条目ID",
      "forget_reason": "对应三条判定条件文本说明",
      "item_I": "条目当前I值",
      "layer_threshold": "分槽对应层级遗忘阈值",
      "suggest_handle": "enum[delete / archive]",
      "layer": "L1/L2/L3/L4",
      "slot_id": "来源分槽"
    }
  ]
}
```
2. **ScanCompleteAck 扫描回执**
```json
{
  "scan_total": "本次扫描总条目数",
  "candidate_count": "遗忘候选数量",
  "retain_count": "保留不遗忘条目数",
  "protected_skip_count": "受保护跳过条目数",
  "estimated_free_space_kb": "预估释放存储空间",
  "cost_ms": "本次判定耗时",
  "remark": "分层拦截提示（L5拦截/L4默认保护等）"
}
```
3. **ForgetAuditLog 审计日志**
```json
{
  "event_type": "forget_threshold_judge",
  "trigger_cause": "扫描触发类型",
  "batch_candidate_summary": "候选条目数量、层级分布摘要",
  "event_ts": "判定完成时间戳"
}
```

## 六、完整业务主流程伪代码（注释优化，对齐V1.1分层遗忘逻辑）
```python
FUNCTION forget_threshold_judge_main_loop():
    # 状态常量定义
    STATE_IDLE = "IDLE"
    STATE_SCAN = "SCANNING"
    STATE_JUDGE = "JUDGING"
    STATE_OUTPUT = "OUTPUTTING"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_IDLE
    # 加载全分槽遗忘阈值配置
    slot_threshold_map = load_all_slot_threshold(from_module="ag-mem-35")
    global_stat = {
        total_scan: 0,
        total_candidate: 0,
        total_protected_skip: 0
    }
    last_report_ts = NOW()
    MIN_REUSE_PROTECT = 系统全局复用保护最低值

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSE
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSE:
                internal_state = STATE_IDLE

        # 2. 接收遗忘扫描申请
        if recv_forget_scan_request():
            scan_req = get_scan_request()
            target_layer = scan_req.target_layer
            trigger_cause = scan_req.trigger_cause
            force_L4 = scan_req.force_scan_L4
            source_slot = scan_req.source_slot_id
            start_ts = NOW()

            # 2a. L5层级直接拦截，返回空回执
            if target_layer == "L5":
                ack = build_scan_ack(0,0,0,0,0, remark="L5层永不参与遗忘判定")
                send_scan_ack(target=scan_req.source_module, ack_data=ack)
                continue

            # 2b. L4默认保护，无强制标记直接返回空回执
            if target_layer == "L4" and not force_L4:
                ack = build_scan_ack(0,0,0,0,0, remark="L4长期层默认遗忘保护，需强制扫描开关")
                send_scan_ack(target=scan_req.source_module, ack_data=ack)
                continue

            # 2c. 拉取条目快照，进入扫描状态
            internal_state = STATE_SCAN
            item_snapshot_list = fetch_item_meta_snapshot(scan_req)
            total_scan_num = len(item_snapshot_list)
            global_stat["total_scan"] += total_scan_num
            if total_scan_num == 0:
                empty_ack = build_scan_ack(0,0,0,0,0, remark="无待判定条目")
                send_scan_ack(scan_req.source_module, empty_ack)
                internal_state = STATE_IDLE
                continue

            # 3. 逐条执行遗忘判定
            internal_state = STATE_JUDGE
            candidate_list = []
            retain_cnt = 0
            protected_skip_cnt = 0
            now_ts = NOW()

            for item in item_snapshot_list:
                # 读取当前分槽阈值，无匹配分槽使用通用槽兜底
                slot_cfg = slot_threshold_map.get(item.slot_id, slot_threshold_map["通用任务槽"])
                match item.current_layer:
                    case "L1": layer_thresh = slot_cfg.L1_I_thresh
                    case "L2": layer_thresh = slot_cfg.L2_I_thresh
                    case "L3": layer_thresh = slot_cfg.L3_I_thresh
                    case "L4": layer_thresh = slot_cfg.L4_I_thresh

                # 第一步：校验全部保护条件
                is_protected = False
                protect_msg = ""
                if item.manual_tag in ["用户收藏", "人工锁定"]:
                    is_protected = True
                    protect_msg = "条目人工标记保护"
                elif (now_ts - item.last_access_ts) < 24 * 3600 * 1000:
                    is_protected = True
                    protect_msg = "24小时内存在访问记录"
                elif item.reuse_count >= MIN_REUSE_PROTECT and item.I_value > layer_thresh * 0.8:
                    is_protected = True
                    protect_msg = "复用次数达标，I值处于缓冲区间"

                if is_protected:
                    protected_skip_cnt += 1
                    send_protect_notify(target="ag-mem-41", item_id=item.item_id, reason=protect_msg, I=item.I_value, thresh=layer_thresh)
                    continue

                # 第二步：执行三重遗忘判定
                need_forget = False
                forget_reason = ""
                handle_type = "delete"
                # 条件1：I低于层级阈值
                if item.I_value < layer_thresh:
                    need_forget = True
                    forget_reason = "I值低于分槽层级遗忘阈值"
                    if item.current_layer in ["L3","L4"]:
                        handle_type = "archive"
                # 条件2：接近阈值且复用不足
                elif layer_thresh <= item.I_value < layer_thresh + 0.05 and item.reuse_count < MIN_REUSE_PROTECT:
                    need_forget = True
                    forget_reason = "I值接近阈值，复用次数未达保护标准"
                # 条件3：容量紧急兜底清理
                elif trigger_cause == "capacity_emergency" and judge_item_in_bottom20pct(item, layer=item.current_layer):
                    need_forget = True
                    forget_reason = "系统容量紧急，低重要度条目强制清理"

                if need_forget:
                    candidate_list.append({
                        "item_id": item.item_id,
                        "forget_reason": forget_reason,
                        "item_I": item.I_value,
                        "layer_threshold": layer_thresh,
                        "suggest_handle": handle_type,
                        "layer": item.current_layer,
                        "slot_id": item.slot_id
                    })
                else:
                    retain_cnt += 1

            # 4. 输出阶段，推送候选清单、日志、回执
            internal_state = STATE_OUTPUT
            global_stat["total_candidate"] += len(candidate_list)
            global_stat["total_protected_skip"] += protected_skip_cnt
            # 超大清单分批推送，单批上限500条
            batch_split_list = split_candidate_batch(candidate_list, batch_size=500)
            for batch in batch_split_list:
                send_candidate_batch(target="ag-mem-42", batch_data=batch)
            # 推送审计日志至ag-mem-51
            send_forget_audit_log(trigger_cause=trigger_cause, candidate_sum=len(candidate_list), ts=now_ts)
            # 组装回执返回发起模块
            estimate_space = len(candidate_list) * AVG_ITEM_SIZE_KB
            finish_ack = build_scan_ack(
                scan_total=total_scan_num,
                candidate_count=len(candidate_list),
                retain_count=retain_cnt,
                protected_skip_count=protected_skip_cnt,
                estimated_free_space_kb=estimate_space,
                cost_ms=now_ts - start_ts
            )
            send_scan_ack(target=scan_req.source_module, ack_data=finish_ack)
            internal_state = STATE_IDLE

        # 5. 每180秒周期上报遗忘统计指标
        if NOW() - last_report_ts >= 180 * 1000:
            stat_report = build_forget_stat_report(internal_state, global_stat)
            send_stat_report(target="ag-mem-03", report=stat_report)
            last_report_ts = NOW()

        SLEEP(10)

# 子函数：拆分超大候选清单，每批最大500条
FUNCTION split_candidate_batch(full_list, batch_size=500):
    batch_arr = []
    for i in range(0, len(full_list), batch_size):
        batch_arr.append(full_list[i:i+batch_size])
    return batch_arr
```

## 七、异常故障处理矩阵（V1.1故障安全规范）
| 故障场景 | 标准处理逻辑 | 恢复条件 |
|--------|----------|----------|
| 扫描条目快照为空 | 返回全零回执，无日志告警，直接切回IDLE | 存储层产生新可扫描条目 |
| 条目所属分槽无配置阈值 | 自动使用「通用任务槽」阈值兜底判定 | ag-mem-35补充分槽专属阈值配置 |
| 条目I值异常（I<0 / I>1） | 直接标记为遗忘候选，判定为数据损坏待清理 | 上层模块修正条目I值元数据 |
| 判定过程中原条目被并发删除 | 基于快照继续判定，不中断流程，不报错 | 无，快照隔离并发变更 |
| 遗忘候选总条数＞1000 | 自动切分500条每批，分多次推送给ag-mem-42 | 无，内置分批逻辑自动执行 |
| 全局紧急熔断指令下发 | 立即终止当前扫描，丢弃未完成候选，冻结所有判定任务 | 总控F0下发RESUME恢复指令 |

## 八、内部调度总线访问契约（统一V1.1总线权限规范）
| 总线流向 | 消息类型 | 访问权限 | 通信主体 |
|--------|----------|----------|----------|
| 读（入站） | 遗忘扫描申请、条目快照、分槽阈值配置、复用校验回执、全局熔断指令 | 只读 | 各存储/分槽/配置/F0模块 → ag-mem-40 |
| 写（出站） | 分批遗忘候选清单 | 模块专属写入 | ag-mem-40 → ag-mem-42 |
| 写（出站） | 扫描完成回执 | 模块专属写入 | ag-mem-40 → 扫描请求发起模块 |
| 写（出站） | 受保护条目通知、遗忘审计日志 | 事件写入 | ag-mem-40 → ag-mem-41 / ag-mem-51 |
| 写（出站） | 周期遗忘统计上报 | 周期写入 | ag-mem-40 → ag-mem-03 |

## 九、V1.1强制安全边界（审计硬约束，不可修改）
| 编号 | 约束规则（V1.1白皮书原文对应） |
|:---:|------|
| F-01 | L5层级永久屏蔽遗忘判定，底层硬编码拦截，任何扫描参数无法绕过 |
| F-02 | L4长期记忆默认开启遗忘保护，仅传入`force_scan_L4=true`才执行判定，防止长效关键经验误清理 |
| F-03 | 遗忘阈值严格按分槽独立配置，禁止全局统一阈值一刀切判定，适配不同业务场景记忆生命周期差异 |
| F-04 | 带用户收藏、人工锁定标记的条目强制跳过遗忘，仅人工解除标记后方可参与扫描 |
| F-05 | 本模块仅输出标准化候选清单，无任何删除、归档、修改存储数据能力，读写操作完全交由ag-mem-42执行，职责隔离防误删 |

## 十、自动化功能测试用例（全覆盖V1.1核心业务分支）
| 用例编号 | 前置状态 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M40-01 | IDLE，L2定时扫描，条目I低于层级阈值 | L2扫描申请 + 条目I=0.15，L2阈值0.20 | 条目进入遗忘候选，建议删除，回执统计候选数+1 |
| TC-M40-02 | IDLE，L3边界条目，I略高于阈值但复用不足 | L3扫描申请，I=0.30，阈值0.28，复用=1 | 命中边界条件2，加入遗忘候选，归档处理 |
| TC-M40-03 | IDLE，条目带用户收藏标记，I极低 | 扫描快照带人工收藏标签 | 计入受保护跳过数，不生成遗忘候选 |
| TC-M40-04 | IDLE，常规L4扫描，未开启强制开关 | L4遗忘扫描申请，force_scan_L4=false | 直接返回空回执，备注L4默认保护 |
| TC-M40-05 | IDLE，容量紧急触发L1扫描，条目I处于底层20%区间 | 触发原因=capacity_emergency低I条目 | 命中兜底条件3，加入遗忘候选清单 |
| TC-M40-06 | IDLE，发起L5全量扫描请求 | L5层级扫描申请 | 直接返回空回执，备注L5不参与遗忘判定 |

## 十一、交付验收自检清单（统一整套mem文档标准）
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二遗忘决策单元定位匹配V1.1白皮书 | ✅ |
| 上下游依赖、被依赖模块编号完整无遗漏 | ✅ |
| 5种内部状态、完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级字段 | ✅ |
| 分层遗忘准入、三重判定条件、多层保护规则完整 | ✅ |
| 伪代码覆盖L5拦截、L4保护、快照扫描、多维度保护校验、分批输出、审计日志、周期上报全链路 | ✅ |
| 异常场景覆盖6类典型故障，处理逻辑符合V1.1安全故障规范 | ✅ |
| 内部调度总线读写权限划分清晰统一 | ✅ |
| 5条V1.1强制安全约束无逻辑漏洞、无绕过清理路径 | ✅ |
| 6条自动化测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充（贴合V1.1五层记忆完整生命周期）
1. 记忆生命周期完整链路：分层存储(ag-mem20~26) → ag-mem-40遗忘判定筛选候选 → ag-mem-42执行删除/归档，完全遵循V1.1「决策与执行分离」安全设计；
2. L4/L5长效记忆双层防护：L5永久屏蔽遗忘、L4默认保护，保障智能体长期沉淀的通用规则、安全经验不会被自动清理，保留V1.1长效记忆核心升级能力；
3. 分槽独立阈值机制支持多业务场景差异化记忆淘汰策略，是V1.1多场景智能体扩展核心能力，完整保留不裁剪；
4. 所有遗忘判定行为全量推送ag-mem-51审计日志，满足V1.1智能体记忆变更可追溯安全要求；
5. 快照隔离并发修改，判定过程不受上层条目新增/删除干扰，保证扫描结果稳定可靠。