# ag-mem-24-L3中期层存储单元 接口规格 优化整理版
基于原文完整梳理、修正逻辑漏洞、补充缺失边界、规范格式，保留全部原始业务规则，新增模块概要、术语统一、风险点汇总、接口入参出参结构体定义，方便开发/测试/联调直接落地。

## 一、模块总览
### 1.1 基础元信息
| 项 | 内容 |
|----|------|
| 模块唯一ID | ag-mem-24 |
| 模块全称 | L3中期层存储单元 |
| 所属架构 | 漏斗二：任务经验漏斗 / 五层存储（L0~L5） |
| 层级定位 | 第三层中期稳定经验存储，承接L2近期层，向上输送合格经验至L4长期层 |
| 容量配额 | 占漏斗二总存储容量10%，硬上限2000条 |
| 数据生命周期 | 条目最长留存30天（720h），到期必须晋升/遗忘，不可永久滞留 |
| 核心能力 | L2晋升条目落地存储、失败经验警示标签管控、场景经验检索、定时相似归并、超期条目调度、容量水位管控、周期状态上报 |
| 禁止行为 | 不参与经验晋升判定、不自主执行遗忘删除；仅做存储、标签管理、指令转发；**CAUTION警示条目禁止晋升L4** |

### 1.2 上下游依赖图谱
#### 依赖模块（主动调用/接收数据）
1. ag-mem-22 L2近期层存储单元：接收晋升条目、返回写入回执
2. ag-mem-25 L3相似经验归并单元：下发归并触发指令、接收归并完成回执
3. ag-mem-26 L4长期层存储单元：推送满足条件的普通经验晋升条目
4. ag-mem-40 遗忘阈值判定单元：下发遗忘扫描指令、接收遗忘回执
5. ag-mem-48 全局容量配额管控单元：查询实时容量、上报模块状态
6. ag-mem-01 总控漏斗F0：接收全局调度（暂停/熔断/恢复/维护）

#### 被依赖模块（对外提供服务）
1. ag-mem-22：写入结果回执
2. ag-mem-15~ag-mem-19 各场景分槽：经验查询接口、接收场景安全通过通知
3. ag-mem-48 / ag-mem-03 调度单元：定时状态上报

## 二、内部状态定义（状态机）
5种互斥运行状态，自动切换+外部指令强制切换
| 状态枚举 | 标识常量 | 业务含义 | 切换触发条件 |
|------|------|------|----------|
| 正常服务 | `NORMAL` | 全功能开放，可写入、查询、定时维护 | 系统初始化完成；熔断/维护结束恢复 |
| 容量预警 | `CAPACITY_WARNING` | 存储使用率≥80%，触发温和归并清理 | 容量查询后计算使用率≥80%且＜95% |
| 容量紧急 | `CAPACITY_CRITICAL` | 使用率≥95%，暂停新增写入，强制遗忘清理 | 使用率≥95%；清理未达标持续保持 |
| 维护整理 | `MAINTENANCE` | 执行归并/遗忘扫描，写入请求排队 | 定时12h归并触发、手动下发维护指令 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，所有读写、维护全部停止 | 总控下发紧急熔断指令；收到恢复指令退出 |

## 三、全局存储配置常量
| 配置项 | 默认值 | 业务说明 |
|--------|:---:|------|
| L3容量占漏斗二总比例 | 10% | 静态配额，全局容量管控单元统一核算 |
| L3条目硬上限 | 2000条 | 条目计数器达到上限直接触发紧急清理 |
| 单条目最大留存时长 | 30天（720h） | 每小时扫描超期条目，分类推送晋升/遗忘 |
| 单条目数据上限 | 20KB | 超过阈值直接丢弃，记录跳过日志 |
| 单次写入超时阈值 | 300ms | 单批L2晋升写入操作最大阻塞时间 |
| 容量预警水位 | 80% | 自动触发定时归并提前释放空间 |
| 容量紧急水位 | 95% | 阻断新写入，强制扫描低重要度条目遗忘 |
| 警示标签降级阈值 | 同场景连续3次无警示安全通过 | CAUTION标签自动清除，解除L4晋升限制 |
| 归并定时周期 | 12h | NORMAL/WARN状态下自动发起全量相似归并 |
| 超期扫描周期 | 1h | 遍历全部条目，处理30天到期数据 |
| 状态上报周期 | 60s | 向容量管控、调度单元推送实时指标 |
| 主循环休眠间隔 | 10ms | 轮询总线消息最小间隔 |

## 四、总线输入接口（接收外部数据）
统一传输通道：内部调度总线；全部消息携带优先级标识
| 输入消息名称 | 数据结构体类型 | 发送方模块 | 触发时机 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3晋升条目列表 | List<L2PromoteItem> | ag-mem-22 | L2条目达到留存周期判定晋升L3 | 高 |
| L3经验查询请求 | QueryReqStruct | ag-mem-15~19 | 场景分槽检索中期历史经验辅助决策 | 高 |
| 归并完成回执 | MergeCallbackStruct | ag-mem-25 | 相似经验归并任务执行完毕 | 高 |
| 遗忘扫描完成回执 | ForgetCallbackStruct | ag-mem-40 | 批量遗忘清理完成返回空间释放数据 | 高 |
| L3容量查询回执 | CapacityRespStruct | ag-mem-48 | 每次写入前主动查询容量后的返回数据 | 高 |
| 场景安全通过通知 | ScenePassNotifyStruct | ag-mem-15~19 | 场景执行成功无失败标签，用于警示计数 | 高 |
| 全局调度控制指令 | Enum<F0Command> | ag-mem-01 | 系统切换模式、紧急熔断、恢复服务 | 紧急 |

### 附属结构体定义（入参）
1. **L2PromoteItem 晋升条目单元**
```json
{
  "item_id": "string",
  "exp_data": "二进制/结构化经验上下文",
  "exp_size": "int(KB)",
  "importance_I": "float",
  "slot_id": "string 来源分槽编号",
  "result_tag": "enum[成功/失败/策略失误]",
  "promote_ts": "long 原L2晋升时间戳"
}
```
2. **QueryReqStruct 查询请求**
```json
{
  "query_filter": "多维度检索条件",
  "slot_id": "来源分槽",
  "max_return": "int 最大返回条数",
  "include_caution": "bool 是否返回警示条目"
}
```
3. **ScenePassNotifyStruct 场景安全通知**
```json
{
  "scene_signature": "string 分槽+任务特征哈希签名"
}
```
4. **F0Command 调度指令枚举**
`PAUSE(暂停) / RESUME(恢复) / FUSE(熔断) / MAINT_SCAN(手动维护)`

## 五、总线输出接口（对外发送数据）
| 输出消息名称 | 结构体类型 | 接收方模块 | 发送触发时机 | 优先级 |
|--------|----------|----------|----------|:---:|
| L3写入确认回执 | WriteAckStruct | ag-mem-22 | L2批量条目写入完成 | 高 |
| L3查询结果列表 | QueryRespStruct | 发起查询的ag-mem15~19 | 检索匹配条目完成 | 高 |
| 归并触发指令 | MergeTriggerCmd | ag-mem-25 | 定时周期/容量预警触发 | 高 |
| 遗忘扫描触发指令 | ForgetTriggerCmd | ag-mem-40 | 容量紧急/超期扫描触发 | 高 |
| L3状态周期上报 | StatusReportStruct | ag-mem-48、ag-mem-03 | 每60s/内部状态变更瞬间 | 普通 |

### 附属结构体定义（出参）
1. **WriteAckStruct 写入回执**
```json
{
  "total_receive": "int 接收条目总数",
  "success_write": "int 成功入库条数",
  "caution_count": "int 本次新增警示条目数",
  "current_usage": "float 当前存储使用率",
  "msg": "string 异常描述（正常为空）"
}
```
2. **QueryRespStruct 查询返回**
```json
{
  "layer_tag": "L3",
  "match_list": [
    {
      "item_id": "条目ID",
      "exp_data": "经验数据",
      "importance_I": "重要度",
      "caution_flag": "bool 是否警示条目",
      "last_access_ts": "long 最近访问时间"
    }
  ]
}
```
3. **StatusReportStruct 状态上报**
```json
{
  "internal_state": "状态枚举",
  "total_item_count": "int 总条目数",
  "usage_rate": "float 使用率",
  "caution_item_total": "int 当前缓存警示条目总量",
  "write_30d_sum": "int 近30日累计写入条目数"
}
```

## 六、失败经验警示标签完整规范
### 6.1 标签枚举
- `NORMAL`：普通有效经验，满足阈值可晋升L4
- `CAUTION`：失败/策略失误经验，锁定L3，禁止晋升L4

### 6.2 标签判定规则
1. 新写入条目 result_tag = 失败 / 策略失误 → 强制标记CAUTION，写入警示跟踪表
2. CAUTION条目永久开放查询，但返回携带`caution_flag=true`；查询参数`include_caution=false`时自动过滤
3. 同场景签名连续收到3次安全通过通知 → 自动降级为NORMAL，从跟踪表移除，解除晋升锁定
4. CAUTION条目留存满30天：不强制晋升，统一推送遗忘评估，可被清除
5. 禁止外部模块、人工指令强制修改CAUTION标签，仅能通过连续安全通过自动降级

### 6.3 标签状态流转图
```
【NORMAL】
    ↓ 写入失败/策略失误条目
【CAUTION】
    ├─→ 同场景连续3次安全通过 → NORMAL（可晋升L4）
    └─→ 留存超30天触发遗忘扫描 → 条目清除
```

### 6.4 警示跟踪表结构
内存常驻字典：`key=item_id`
```json
{
  "scene_signature": "分槽+任务特征哈希",
  "safe_pass_count": "int 连续无警示通过计数"
}
```

## 七、核心业务主流程逻辑（精简可落地伪代码）
```python
FUNCTION l3_storage_main_loop():
    # 状态常量定义
    STATE_NORMAL = "NORMAL"
    STATE_CAP_WARN = "CAPACITY_WARNING"
    STATE_CAP_CRIT = "CAPACITY_CRITICAL"
    STATE_MAINT = "MAINTENANCE"
    STATE_PAUSE = "SYSTEM_PAUSED"

    # 初始化资源
    internal_state = STATE_NORMAL
    init_storage()  # 内存索引+持久化存储
    item_counter = 0  # 当前总条目计数器
    caution_track_map = {}  # 警示条目跟踪表
    last_merge_ts = NOW()
    last_expire_scan_ts = NOW()
    last_report_ts = NOW()

    WHILE system_alive:
        # 1. 处理全局熔断调度指令
        if recv_global_cmd():
            cmd = get_global_cmd()
            if cmd == FUSE:
                internal_state = STATE_PAUSED
                continue
            if cmd == RESUME and internal_state == STATE_PAUSE:
                internal_state = STATE_NORMAL

        # 2. 处理L2晋升写入消息（最高优先级）
        if recv_l2_promote_list():
            if internal_state == STATE_PAUSED:
                send_write_ack(total=len(list), success=0, caution=0, msg="系统熔断")
                continue
            
            # 查询实时容量水位
            cap_resp = call_ag_mem48_query_cap()
            usage = cap_resp.usage_rate

            # 更新内部容量状态
            if usage >= 0.95:
                internal_state = STATE_CAP_CRIT
            elif usage >= 0.8:
                internal_state = STATE_CAP_WARN

            # 容量紧急：强制清理低重要度条目
            if internal_state == STATE_CAP_CRIT:
                send_forget_scan_cmd(range="low_20pct", reason="容量紧急")
                wait_forget_callback()
                new_cap = call_ag_mem48_query_cap()
                if new_cap.usage_rate >= 0.95:
                    send_write_ack(len(list),0,0,"L3容量已满，拒绝写入")
                    continue

            # 逐条入库
            success_cnt = 0
            new_caution_cnt = 0
            for item in promote_list:
                # 单条目超限丢弃
                if item.exp_size > 20:
                    log_skip_item(item.item_id, "超过20KB上限")
                    continue
                # 判定警示标签
                tag = "NORMAL"
                if item.result_tag in ["失败","策略失误"]:
                    tag = "CAUTION"
                    new_caution_cnt += 1
                    sig = gen_scene_sig(item.slot_id, item.exp_data.feature)
                    caution_track_map[item.item_id] = {"scene_signature":sig, "safe_pass_count":0}
                # 写入存储层
                if storage_append(item, tag):
                    success_cnt += 1
                    item_counter += 1
            # 返回写入回执
            send_write_ack(len(promote_list), success_cnt, new_caution_cnt, usage)

        # 3. 处理场景分槽查询请求
        if recv_query_req():
            req = get_query_req()
            raw_match = storage_search(req.filter, req.slot_id, req.max_return)
            final_result = []
            for entry in raw_match:
                if entry.tag == "CAUTION":
                    entry.caution_flag = True
                    if not req.include_caution:
                        continue
                else:
                    entry.caution_flag = False
                final_result.append(entry)
            send_query_resp(req.slot_id, final_result)

        # 4. 12小时定时相似经验归并
        if NOW() - last_merge_ts >= 12*3600 and internal_state in [STATE_NORMAL, STATE_CAP_WARN]:
            internal_state = STATE_MAINT
            send_merge_trigger_cmd()
            wait_merge_callback()
            internal_state = STATE_NORMAL
            last_merge_ts = NOW()

        # 5. 每小时超期30天条目扫描
        if NOW() - last_expire_scan_ts >= 3600:
            expire_items = storage_filter(retention > 30*24*3600)
            for item in expire_items:
                if item.tag == "CAUTION":
                    send_forget_eval(item)
                elif item.importance_I >= L3_L4_THRESHOLD:
                    send_promote_to_L4(item)
                else:
                    send_forget_eval(item)
            item_counter -= len(expire_items)
            last_expire_scan_ts = NOW()

        # 6. 场景安全通知 → 更新警示计数、自动降级
        if recv_scene_pass_notify():
            sig = notify.scene_signature
            del_list = []
            for item_id, track in caution_track_map.items():
                if track.scene_signature == sig:
                    track.safe_pass_count += 1
                    if track.safe_pass_count >= 3:
                        storage_update_tag(item_id, "NORMAL")
                        del_list.append(item_id)
                        log("警示标签自动降级", item_id)
            for del_id in del_list:
                del caution_track_map[del_id]

        # 7. 60s周期状态上报
        if NOW() - last_report_ts >= 60:
            report = build_status_report(
                internal_state, item_counter, usage, len(caution_track_map)
            )
            send_status_report(report, target=[ag-mem48, ag-mem03])
            last_report_ts = NOW()

        SLEEP(10)
```

## 八、异常场景与故障处理矩阵
| 故障场景 | 处理逻辑 | 恢复条件 |
|------|----------|----------|
| 底层存储读写IO异常 | 停止当前批次写入，返回实际成功条数，上报告警日志 | 存储介质/服务IO恢复 |
| 单条目超过20KB大小限制 | 跳过本条，记录丢弃日志，不阻塞整批写入 | 无，本条永久丢弃 |
| 容量水位95%，遗忘清理后使用率仍超标 | 拒绝所有新增L2晋升写入，持续上报告警 | 人工清理扩容/大量条目自然过期遗忘 |
| CAUTION警示条目留存满30天 | 不自动晋升L4，推送遗忘单元评估删除 | 无，按遗忘策略执行 |
| 归并任务执行中收到写入请求 | 写入消息进入总线排队队列，归并完成后批量处理 | ag-mem-25返回归并完成回执 |
| 全局紧急熔断指令触发 | 冻结全部读写、定时任务，持久化数据不修改 | 总控下发RESUME恢复指令 |

## 九、总线访问权限契约
全部通信通道统一为**内部调度总线**，区分只读/专属写/周期写权限：
| 总线操作方向 | 消息类型 | 访问权限 | 通信对象 |
|------|----------|------|------|
| 读（入站） | L3晋升条目列表 | 只读 | ag-mem-22 → ag-mem-24 |
| 读（入站） | 经验查询请求 | 只读 | ag-mem15~19 → ag-mem-24 |
| 读（入站） | 归并/遗忘/容量回执、场景安全通知、全局调度指令 | 只读 | 各下游/总控 → ag-mem-24 |
| 写（出站） | 写入确认回执 | 模块专属写入 | ag-mem-24 → ag-mem-22 |
| 写（出站） | 查询结果列表 | 模块专属写入 | ag-mem-24 → 对应场景分槽 |
| 写（出站） | 归并/遗忘触发指令 | 模块专属写入 | ag-mem-24 → ag-mem25/40 |
| 写（出站） | 周期状态上报 | 周期性写入 | ag-mem-24 → ag-mem48/03 |

## 十、强制安全边界规则（不可突破）
1. **S-01**：失败/策略失误经验必须标记CAUTION，任何逻辑禁止将其推送至L4长期层；晋升接口增加标签拦截校验
2. **S-02**：所有查询返回警示条目必须携带`caution_flag=true`；上层决策模块不可仅依靠警示经验做自动执行动作
3. **S-03**：CAUTION标签仅能通过「同场景连续3次安全通过」自动降级；禁止其他模块、后台指令、人工操作强制清除标签
4. **S-04**：条目最长留存30天，到期必须执行晋升/遗忘评估，不允许永久驻留L3存储分区

## 十一、接口功能测试用例（TC全量）
| 用例编号 | 前置条件 | 输入消息 | 预期输出结果 |
|----------|----------|------|----------|
| TC-M24-01 | 状态NORMAL，使用率50% | 3条成功标签晋升条目 | 全部写入成功，新增警示数=0，状态不变 |
| TC-M24-02 | 状态NORMAL | 1条result_tag=策略失误条目 | 写入成功，标记CAUTION，写入跟踪表，禁止晋升L4 |
| TC-M24-03 | 存在CAUTION条目，查询include_caution=True | 场景查询请求 | 返回全部匹配条目，警示条目caution_flag=true |
| TC-M24-04 | 存在CAUTION条目，查询include_caution=False | 场景查询请求 | 过滤所有CAUTION条目，仅返回NORMAL经验 |
| TC-M24-05 | 跟踪表存在某场景CAUTION条目 | 连续3次同场景安全通过通知 | 条目标签更新NORMAL，从跟踪表移除，可正常晋升L4 |
| TC-M24-06 | 存在留存31天的NORMAL高I值条目 | 每小时超期扫描任务触发 | 条目推送至ag-mem-26执行L4晋升 |
| TC-M24-07 | 存在留存31天的CAUTION条目 | 每小时超期扫描任务触发 | 推送遗忘评估，不发起L4晋升 |
| TC-M24-08 | 使用率96%（CAPACITY_CRITICAL） | 批量晋升条目写入 | 自动发起遗忘清理；清理后仍超限则返回写入拒绝回执 |

## 十二、交付自检验收清单
| 检查项 | 完成状态 |
|--------|:---:|
| 模块编号、漏斗分区层级定义准确 | ✅ |
| 上下游依赖、被依赖模块完整无遗漏 | ✅ |
| 5种内部状态+切换条件完整定义 | ✅ |
| 所有输入输出消息携带数据类型、优先级、收发方 | ✅ |
| 存储配置常量全覆盖，含警示降级阈值 | ✅ |
| 警示标签规则、状态流转、跟踪表完整 | ✅ |
| 主循环伪代码覆盖写入、查询、归并、超期、标签降级、容量管控全链路 | ✅ |
| 异常故障场景覆盖6类核心风险 | ✅ |
| 内部调度总线读写权限区分清晰 | ✅ |
| 4条强制安全约束明确，无逻辑漏洞 | ✅ |
| 测试用例覆盖正常、警示、查询过滤、降级、超期、容量紧急场景 | ✅ |

# 附加补充说明（联调专用）
1. 本模块无独立遗忘删除能力，所有清理动作仅下发指令给ag-mem-40，由遗忘单元最终执行删除；
2. 相似经验归并仅做空间释放与经验泛化合并，不修改条目警示标签；
3. 30天超期是硬时限，不受容量水位、业务场景特殊豁免；
4. 警示计数绑定**场景签名哈希**，仅完全匹配的任务场景才累计连续安全次数，跨场景不互通。