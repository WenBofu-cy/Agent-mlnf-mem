# ag-mem-26-L4长期层存储单元 规整落地版接口规格文档
承接ag-mem-24、ag-mem-25上下游模块统一架构规范，统一结构体定义、业务链路梳理、消除逻辑歧义，完整保留全部原生业务规则，适配开发编码、联调对接、自动化测试使用。

## 一、模块基础元信息
| 项 | 内容 |
|----|------|
| 模块唯一ID | ag-mem-26 |
| 模块全称 | L4长期层存储单元 |
| 所属架构 | 三、漏斗二：任务经验漏斗 / 五层存储（L0~L5） |
| 层级定位 | 第四层高阶泛化经验存储，承接L3合格无警示经验，向上输送达标经验至L5核心层；承载跨场景通用技能沉淀 |
| 容量配额 | 占漏斗二总存储容量4.5%，硬上限1000条 |
| 生命周期规则 | 无固定30天过期时限，默认强遗忘保护；仅I值过低+复用频次不足才会进入遗忘评估 |
| 核心能力 | L3晋升条目准入校验、用户隐私去个性化清洗、L4专属I值重计算、经验持久存储、分槽同类经验计数、定时/定量触发抽象提炼、经验检索、容量水位管控、周期状态上报 |
| 禁止行为 | 不参与晋升判定、不自主删除遗忘条目；**禁止CAUTION警示条目入库**；所有入库数据强制脱敏去个性化 |

### 上下游依赖图谱
#### 依赖模块（主动调用/接收消息）
1. ag-mem-24 L3中期存储单元：接收晋升条目、返回写入回执
2. ag-mem-27 L4经验抽象提炼单元：下发提炼指令、接收提炼完成回执
3. ag-mem-28 L5核心层存储单元：推送满足门槛的高阶经验晋升条目
4. ag-mem-40 遗忘阈值判定单元：下发遗忘扫描指令、接收清理回执
5. ag-mem-48 全局容量配额管控单元：查询实时容量、周期上报指标
6. ag-mem-01 总控漏斗F0：接收全局熔断/调度指令

#### 被依赖模块（对外提供服务）
1. ag-mem-24：写入结果回执
2. ag-mem-27：提供全量待抽象经验数据集
3. ag-mem-15~ag-mem-19 场景分槽：L4长期经验查询接口

## 二、内部状态机（5种互斥运行状态）
| 状态枚举常量 | 状态名称 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| `NORMAL` | 正常服务 | 读写、查询、定时维护全部开放 | 初始化完成；熔断/抽象提炼结束恢复 |
| `CAPACITY_WARNING` | 容量预警 | 使用率≥80%，提前触发遗忘温和清理 | 容量查询后计算水位80%≤使用率＜95% |
| `CAPACITY_CRITICAL` | 容量紧急 | 使用率≥95%，阻断新增写入，强制低I条目遗忘 | 使用率≥95%，清理未达标持续保持 |
| `ABSTRACTING` | 抽象提炼中 | 下发指令给ag-mem-27执行规则提取，写入请求排队 | 累计20条同槽新条目 / 72小时定时触发 |
| `SYSTEM_PAUSED` | 暂停服务 | 全局熔断，所有读写、定时任务冻结 | 总控下发FUSE熔断指令；RESUME恢复指令退出 |

## 三、全局存储配置常量
| 配置项 | 默认值 | 业务说明 |
|--------|:---:|------|
| L4容量占漏斗二总比例 | 4.5% | 静态配额，由ag-mem-48统一核算管控 |
| L4条目硬上限 | 1000条 | 条目计数器达上限直接触发紧急遗忘清理 |
| 条目最大留存时长 | 无硬性时限 | 依靠遗忘策略动态淘汰，优质经验长期留存 |
| 单条目数据上限 | 25KB | 超阈值直接丢弃，记录跳过日志 |
| 单次写入超时阈值 | 300ms | 批量L3晋升写入最大阻塞时间 |
| 容量预警水位 | 80% | 启动温和遗忘扫描释放空间 |
| 容量紧急水位 | 95% | 阻断新写入，强制清理低重要度条目 |
| 定量抽象提炼阈值 | 同槽新增20条 | 累计满20条自动触发分槽局部提炼 |
| 定时抽象提炼周期 | 72h | 全局全量未提炼条目统一规则提取 |
| 遗忘扫描周期 | 24h | 执行全量L4条目遗忘评估 |
| 状态上报周期 | 120s | 向容量管控、调度单元推送指标 |
| 遗忘保护等级 | 强保护 | L4遗忘阈值远宽松于L3，淘汰门槛更高 |

## 四、输入总线接口（内部调度总线 只读）
统一传输通道：内部调度总线，区分消息优先级
| 输入消息名称 | 结构体类型 | 发送方模块 | 触发时机 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4晋升条目列表 | List<L3PromoteNormalItem> | ag-mem-24 | L3条目满30天且无警示标签判定晋升L4 | 高 |
| L4经验查询请求 | L4QueryReqStruct | ag-mem-15~19 | 场景分槽检索长期通用经验辅助决策 | 高 |
| 抽象提炼完成回执 | AbstractCallbackStruct | ag-mem-27 | 规则提取任务执行完毕返回结果 | 高 |
| 遗忘扫描完成回执 | ForgetCallbackStruct | ag-mem-40 | 批量遗忘清理完成返回空间释放数据 | 高 |
| L4容量查询回执 | CapacityRespStruct | ag-mem-48 | 每次写入前主动查询容量后的返回数据 | 高 |
| 全局调度控制指令 | Enum<F0Command> | ag-mem-01 | 系统暂停/恢复/熔断/手动维护扫描 | 紧急 |

### 入参核心结构体定义
1. **L3PromoteNormalItem L3晋升至L4条目**
```json
{
  "item_id": "string 条目唯一ID",
  "exp_raw_data": "原始经验结构化数据",
  "I0": "float 原始基础重要度",
  "S": "float 安全显著性",
  "C": "float 复用频次",
  "V": "float 用户价值（L4重算剔除该维度）",
  "slot_id": "string 来源分槽编号",
  "promote_ts": "long L3晋升时间戳",
  "caution_tag": "enum[NONE/NORMAL/CAUTION]"
}
```
2. **L4QueryReqStruct 查询请求**
```json
{
  "query_filter": "多维度检索条件",
  "slot_id": "来源分槽编号",
  "max_return": "int 最大返回条目数量"
}
```
3. **F0Command 调度指令枚举**
`PAUSE / RESUME / FUSE / MAINT_SCAN`

## 五、输出总线接口（内部调度总线 专属写入）
| 输出消息名称 | 结构体类型 | 接收方模块 | 发送触发时机 | 优先级 |
|--------|----------|----------|----------|:---:|
| L4写入确认回执 | L4WriteAckStruct | ag-mem-24 | L3批量晋升条目写入处理完成 | 高 |
| L4查询结果列表 | L4QueryRespStruct | 发起查询的ag-mem15~19 | 检索匹配条目完成 | 高 |
| 抽象提炼触发指令 | AbstractTriggerCmd | ag-mem-27 | 定量20条达标 / 72h定时触发 | 高 |
| 遗忘扫描触发指令 | ForgetTriggerCmd | ag-mem-40 | 容量紧急 / 24h定时扫描 | 高 |
| L4周期状态上报 | L4StatusReportStruct | ag-mem-48、ag-mem-03 | 每120s / 内部状态瞬间变更 | 普通 |

### 出参核心结构体定义
1. **L4WriteAckStruct 写入回执**
```json
{
  "total_receive": "int 接收条目总数",
  "success_write": "int 成功入库条数",
  "desensitize_count": "int 本次脱敏去个性化条目总数",
  "current_usage": "float 当前存储使用率",
  "msg": "异常描述，正常为空"
}
```
2. **L4QueryRespStruct 查询返回**
```json
{
  "layer_tag": "L4",
  "match_list": [
    {
      "item_id": "条目ID",
      "exp_data": "脱敏后经验数据",
      "I_l4": "L4重算后重要度",
      "has_abstract": "bool 是否完成抽象提炼",
      "last_access_ts": "long 最近访问时间"
    }
  ]
}
```
3. **L4StatusReportStruct 状态上报**
```json
{
  "internal_state": "状态枚举",
  "total_item_count": "int 当前总条目数",
  "usage_rate": "float 存储使用率",
  "abstracted_item_total": "int 已完成抽象提炼条目总量",
  "write_90d_sum": "int 近90日累计写入条目数"
}
```

## 六、去个性化脱敏规范（强制安全流程）
### 6.1 脱敏目标
L4经验用于跨用户、跨场景通用技能泛化，入库前必须清除所有可定位单一用户的隐私字段，杜绝用户数据泄露。

### 6.2 字段处理规则表
| 原始字段 | 处理策略 |
|--------|----------|
| 用户ID | 删除，替换固定标记「匿名用户」 |
| 会话ID | 直接清空删除 |
| 设备指纹 | 直接清空删除 |
| 地理位置信息 | 直接清空删除 |
| 用户原始输入文本 | 删除，仅保留标准化任务特征向量与意图标签 |
| 用户个性化偏好参数 | 删除，仅保留通用工具调用序列 |
| 写入时间戳 | 完整保留，用于时序趋势分析 |
| 任务特征向量 | 完整保留（泛化核心依据） |
| 工具调用序列 | 完整保留 |
| 结果标签 | 完整保留 |
| 基础I0、S、C值 | 保留，用于L4专属重要度重算 |
| 来源分槽编号 | 完整保留 |

### 6.3 L4专属重要度重算公式
剔除用户价值V维度，仅基于安全、复用能力计算长期层重要度：
$$
I_{L4} = I_0 + \alpha \times S + \gamma \times C
$$
- $\alpha、\gamma$：各场景分槽独立配置权重系数
- 不再引入用户价值V，消除个体偏好对通用经验的干扰

## 七、完整业务主流程伪代码（注释优化版）
```python
FUNCTION l4_storage_main_loop():
    # 状态常量定义
    STATE_NORMAL = "NORMAL"
    STATE_CAP_WARN = "CAPACITY_WARNING"
    STATE_CAP_CRIT = "CAPACITY_CRITICAL"
    STATE_ABSTRACT = "ABSTRACTING"
    STATE_PAUSE = "SYSTEM_PAUSED"

    internal_state = STATE_NORMAL
    init_l4_storage()  # 内存索引+持久化分区初始化
    item_counter = 0  # L4总条目计数器
    slot_new_item_stat = {}  # slot_id: 分槽自上次提炼新增条目数

    WHILE system_running:
        # 1. 最高优先级：全局熔断调度指令
        if recv_global_f0_cmd():
            cmd = get_f0_cmd()
            if cmd == "FUSE":
                internal_state = STATE_PAUSED
                continue
            if cmd == "RESUME" and internal_state == STATE_PAUSED:
                internal_state = STATE_NORMAL

        # 2. 接收L3晋升条目写入消息
        if recv_l3_promote_list():
            if internal_state == STATE_PAUSED:
                send_write_ack(total=len(list), success=0, desensitize=0, msg="系统熔断")
                continue
            
            raw_promote_list = get_promote_list()
            # 准入校验：过滤所有CAUTION警示条目
            valid_list = []
            error_msg = ""
            for item in raw_promote_list:
                if item.caution_tag == "CAUTION":
                    error_msg += f"拒绝CAUTION条目:{item.item_id};"
                    continue
                valid_list.append(item)
            if len(valid_list) == 0:
                log("全部条目为警示条目，拒绝写入", error_msg)
                continue

            # 查询实时容量水位
            cap_resp = call_ag_mem48_query_cap()
            usage = cap_resp.usage_rate
            # 更新内部容量状态
            if usage >= 0.95:
                internal_state = STATE_CAP_CRIT
            elif usage >= 0.8:
                internal_state = STATE_CAP_WARN

            # 容量紧急：强制遗忘低I条目清理
            if internal_state == STATE_CAP_CRIT:
                send_forget_scan_cmd(range="low_15pct_I", reason="容量紧急")
                wait_forget_callback()
                new_cap = call_ag_mem48_query_cap()
                if new_cap.usage_rate >= 0.95:
                    send_write_ack(len(raw_promote_list),0,0,"L4容量已满，拒绝写入")
                    continue

            # 逐条脱敏、重算I值、入库
            success_cnt = 0
            desensitize_cnt = 0
            for item in valid_list:
                # 单条目大小超限丢弃
                if get_exp_size(item.exp_raw_data) > 25:
                    log_skip(item.item_id, "超过25KB单条目上限")
                    continue
                # 执行去个性化脱敏
                desensitize_exp = desensitize_process(item.exp_raw_data)
                desensitize_cnt += 1
                # L4重要度重算
                l4_I = item.I0 + alpha * item.S + gamma * item.C
                # 持久化写入L4
                write_ok = storage_append(
                    item_id=item.item_id,
                    exp_data=desensitize_exp,
                    I_l4=l4_I,
                    slot_id=item.slot_id,
                    caution_tag="NORMAL",
                    write_ts=NOW()
                )
                if write_ok:
                    success_cnt += 1
                    item_counter += 1
                    # 更新分槽新增条目计数
                    slot = item.slot_id
                    slot_new_item_stat[slot] = slot_new_item_stat.get(slot, 0) + 1

            # 返回写入回执给ag-mem-24
            send_write_ack(
                total_receive=len(raw_promote_list),
                success_write=success_cnt,
                desensitize_count=desensitize_cnt,
                current_usage=usage
            )

            # 定量触发抽象提炼：单槽累计≥20条
            for slot, cnt in slot_new_item_stat.items():
                if cnt >= 20:
                    send_abstract_trigger(
                        range=f"slot_{slot}_new_20",
                        source_slot="ag-mem-26",
                        group=get_slot_recent_20_items(slot)
                    )
                    slot_new_item_stat[slot] = 0

        # 3. 处理场景分槽查询请求
        if recv_l4_query_req():
            req = get_query_req()
            raw_match = storage_search(req.query_filter, req.slot_id, req.max_return)
            # 标记条目是否已抽象提炼
            for entry in raw_match:
                entry.has_abstract = storage_is_abstracted(entry.item_id)
            send_query_resp(req.slot_id, raw_match)

        # 4. 72小时定时全局抽象提炼
        if NOW() - last_abstract_timer >= 72*3600:
            internal_state = STATE_ABSTRACT
            all_slots = storage_get_all_slot_ids()
            for slot in all_slots:
                unabstract_items = storage_filter(slot=slot, abstracted=False)
                if len(unabstract_items) >= 5:
                    send_abstract_trigger(
                        range=f"slot_{slot}_all_unabstract",
                        source_slot="ag-mem-26",
                        group=unabstract_items
                    )
            internal_state = STATE_NORMAL
            last_abstract_timer = NOW()

        # 5. 24小时定时遗忘扫描（强保护阈值下发）
        if NOW() - last_forget_timer >= 24*3600 and internal_state in [STATE_NORMAL, STATE_CAP_WARN]:
            send_forget_scan_cmd(
                range="full_l4",
                threshold=get_l4_slot_forget_threshold(),
                protect_level="strong",
                source="ag-mem-26"
            )
            last_forget_timer = NOW()

        # 6. 接收抽象提炼完成回执，更新条目抽象标记
        if recv_abstract_callback():
            callback = get_abstract_callback()
            for rule in callback.rule_list:
                source_ids = rule.source_item_ids
                for item_id in source_ids:
                    storage_update_abstract_flag(item_id, abstracted=True, rule_id=rule.rule_id)

        # 7. 120秒周期状态指标上报
        if NOW() - last_report_timer >= 120:
            abstract_total = storage_count_abstracted_items()
            report = build_status_report(
                internal_state, item_counter, usage, abstract_total
            )
            send_status_report(report, target=["ag-mem-48", "ag-mem-03"])
            last_report_timer = NOW()

        SLEEP(10)

# 去个性化脱敏子函数
FUNCTION desensitize_process(raw_exp_data):
    exp = deep_clone(raw_exp_data)
    exp.user_id = "匿名用户"
    exp.session_id = None
    exp.device_finger = None
    exp.location = None
    exp.user_raw_input = None
    exp.user_pref_params = None
    # 保留字段不做修改：特征向量、工具序列、标签、时间戳、分槽ID
    return exp
```

## 八、异常故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|------|----------|----------|
| 晋升条目携带CAUTION警示标签 | 整条过滤拒绝写入，回执附带错误日志 | 条目在L3完成警示降级为NORMAL后重新发起晋升 |
| L4底层存储IO读写异常 | 停止当前批次写入，返回实际成功条数，上报告警 | 存储介质/服务IO恢复正常 |
| 单条目超过25KB存储上限 | 跳过本条，记录丢弃日志，不阻塞整批写入 | 无，本条永久丢弃 |
| 使用率95%，遗忘清理后水位仍超标 | 阻断全部新增L3晋升写入，持续告警 | 人工扩容/大量条目满足遗忘条件被清理 |
| 脱敏去个性化出现数据格式兼容异常 | 执行极简保守脱敏，清空全部隐私字段，标记异常日志 | 上游L3输出数据格式修复统一 |
| 抽象提炼执行中收到批量写入 | 写入消息进入总线排队队列，提炼完成后批量处理 | ag-mem-27返回提炼完成回执，退出ABSTRACTING状态 |
| 全局紧急熔断指令下发 | 冻结所有读写、定时任务，持久化数据不做任何修改 | 总控下发RESUME恢复指令 |

## 九、内部调度总线访问契约
| 总线方向 | 消息类型 | 访问权限 | 通信双方 |
|------|----------|------|------|
| 读（入站） | L4晋升条目列表 | 只读 | ag-mem-24 → ag-mem-26 |
| 读（入站） | L4经验查询请求 | 只读 | ag-mem15~19 → ag-mem-26 |
| 读（入站） | 抽象/遗忘/容量回执、全局调度指令 | 只读 | 各模块/总控 → ag-mem-26 |
| 写（出站） | L4写入确认回执 | 模块专属写入 | ag-mem-26 → ag-mem-24 |
| 写（出站） | L4查询结果列表 | 模块专属写入 | ag-mem-26 → 对应场景分槽 |
| 写（出站） | 抽象提炼/遗忘扫描触发指令 | 模块专属写入 | ag-mem-26 → ag-mem27 / ag-mem40 |
| 写（出站） | 周期状态指标上报 | 周期性写入 | ag-mem-26 → ag-mem48、ag-mem03 |

## 十、强制安全边界（不可绕过）
| 编号 | 约束规则 |
|:---:|------|
| S-01 | 所有入库L4经验必须完整执行去个性化脱敏，严禁留存任何可定位单一用户的隐私信息 |
| S-02 | L4长期层永久拒绝CAUTION警示条目，失败/策略失误经验无法晋升至长期存储 |
| S-03 | L4条目启用强遗忘保护机制，淘汰阈值远宽松于L3，仅极低I值且复用不足才允许遗忘评估 |
| S-04 | L4重要度重算必须剔除用户价值V维度，仅依靠安全显著性S、复用频次C计算通用经验权重 |
| S-05 | 从L4晋升至L5核心层的经验，必须额外经过ag-mem-43独立安全底线校验，本模块不省略该流程 |

## 十一、自动化功能测试用例全覆盖
| 用例编号 | 前置条件 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M26-01 | NORMAL，使用率50%，3条无警示晋升条目 | L3晋升条目列表 | 全部脱敏写入成功，I值剔除V重算，回执脱敏计数正常 |
| TC-M26-02 | NORMAL，单条CAUTION警示晋升条目 | 含警示标签的晋升条目 | 直接过滤拒绝写入，回执附带错误描述 |
| TC-M26-03 | NORMAL，场景分槽查询请求 | 指定槽位检索指令 | 返回匹配条目，每条携带has_abstract提炼标记 |
| TC-M26-04 | NORMAL，同槽累计写入至第20条有效条目 | 第20条同分槽晋升条目入库 | 自动下发抽象提炼指令至ag-mem-27，计数清零 |
| TC-M26-05 | NORMAL，距离上次提炼满72小时 | 定时触发检测逻辑 | 遍历所有分槽未提炼条目，批量下发提炼指令 |
| TC-M26-06 | CAPACITY_CRITICAL，遗忘清理后使用率仍96% | 批量晋升写入请求 | 执行遗忘清理后依旧容量超限，拒绝所有写入，告警上报 |

## 十二、交付验收自检清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗二层五层存储层级定位准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 5种内部状态+完整切换触发条件定义清晰 | ✅ |
| 全部输入输出附带结构体、收发模块、优先级 | ✅ |
| L4存储配置参数完整，包含定量/定时抽象阈值 | ✅ |
| 去个性化脱敏字段规则、I值重算公式完整 | ✅ |
| 伪代码覆盖警示过滤、脱敏、重算、定量/定时抽象、查询、遗忘扫描全链路 | ✅ |
| 异常场景覆盖7类典型故障处理逻辑 | ✅ |
| 内部调度总线读写权限划分清晰 | ✅ |
| 5条强制安全约束无逻辑漏洞 | ✅ |
| 6条测试用例覆盖全部核心业务分支 | ✅ |

## 模块联动补充说明（对接上层L3、下层L5）
1. 仅接收ag-mem-24推送的**无CAUTION标签**、满30天生命周期的L3合格经验；失败警示经验永久阻断晋升L4；
2. 本模块仅下发抽象提炼任务给ag-mem-27，不负责规则生成、存储，仅同步更新条目「已抽象」标记；
3. L4无自主删除能力，所有条目淘汰、清理仅下发指令至ag-mem-40遗忘单元执行；
4. 满足L5晋升门槛的经验由本模块主动推送至ag-mem-28，晋升前强制走独立安全校验单元ag-mem-43；
5. 脱敏流程为入库前置强制步骤，不存在跳过脱敏写入的业务分支。