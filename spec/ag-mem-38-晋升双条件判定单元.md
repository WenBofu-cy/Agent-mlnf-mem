## V1.1 模块升级总说明
### V1.1 重大升级变更点
1. **废弃V1.0固定5场景分槽架构**，全链路使用`funnel_id`动态子漏斗作为存储分桶唯一标识；
2. **新增核心晋升前置约束：客观结果验证标记result_validated**，L2及以上层级强制校验，杜绝无执行结果的无效记忆污染中长期层；
3. **集成哈希索引管控逻辑**，L3及以上晋升要求条目携带有效检索标签，保障全系统记忆查询性能；
4. 所有晋升拒绝场景标准化输出`block_reason`拦截原因，支持总控漏斗全局统计、复盘记忆沉淀失败数据；
5. 层级晋升阈值由固定分槽静态表，改为按动态子漏斗独立可配置阈值，适配不同领域经验差异化沉淀需求；
6. 扩展状态机，新增`PRE_CHECK`前置校验阶段，分层执行漏斗合法性、结果标记、索引标签三重前置过滤；
7. 总线输入输出结构同步新增`funnel_id`、`result_validated`、`hash_tag_list`关键字段，上下游存储、路由模块无缝联动；
8. 模块依赖新增ag-mem-01总控漏斗，用于读取全局存量漏斗集合、上报全系统晋升统计数据；
9. 原有警示标签、时长/I值双条件校验逻辑完整保留，仅适配动态漏斗架构做字段兼容改造，无原有能力丢失。


# ag-mem-38-晋升双条件判定单元 接口规格（V1.1 完整版，适配动态子漏斗+结果校验+哈希索引）
---

## 基本信息

| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-38 |
| 模块名称 | 晋升双条件判定单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制 |
| 核心职责 | 接收来自 ag-mem-21（L1衰减评估）、ag-mem-22（L2超期处理）、ag-mem-24（L3超期处理）以及 ag-mem-37（重要度增量定时刷新后触发的晋升复检）的晋升候选条目，**适配V1.1动态子漏斗架构，废弃固定分槽编号，统一使用funnel_id作为分桶标识**。<br>新增V1.1核心前置校验：`result_validated`客观结果验证标记（L2及以上晋升必须校验通过）；<br>完整校验三层晋升规则：1.客观结果验证 2.留存时长阈值 3.综合重要度I值阈值；<br>同步关联哈希索引标签，仅允许带有效业务索引的经验参与中长期晋升；<br>满足全部条件的条目整理为“晋升候选清单”，发送至 ag-mem-39（层级单向搬运写入单元）执行物理晋升；不满足条件的条目携带标准化`block_reason`拦截原因返回来源层级继续保留或进入遗忘评估。<br>同时校验条目是否带有警示标签（CAUTION）——警示条目默认禁止晋升至 L4，需先通过 ag-mem-43（失败经验安全仲裁）解除警示后方可参与晋升判定。不参与搬运执行或内容修改，仅负责晋升条件的客观校验、拦截原因标记与清单生成。 |
| 依赖模块 | ag-mem-01（总控漏斗F0，读取全局子漏斗层级阈值、哈希索引全局规则）、ag-mem-21（L1衰减评估，推送L1晋升候选）、ag-mem-22（L2存储，推送L2超期晋升候选）、ag-mem-24（L3存储，推送L3超期晋升候选）、ag-mem-26（L4存储，推送L4超期晋升候选）、ag-mem-35（三维权重系数配置单元，获取各层级晋升阈值）、ag-mem-37（定时刷新后触发的晋升复检）、ag-mem-14（动态路由单元，校验funnel_id合法性、关联哈希索引标签有效性） |
| 被依赖模块 | ag-mem-39（层级单向搬运写入单元，接收晋升候选清单）、ag-mem-21/22/24/26（返回带block_reason的完整判定回执）、ag-mem-01（上报全局晋升统计数据） |


## 内部状态定义

| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 空闲等待 | `IDLE` | 无判定任务，等待晋升候选推送 | 系统初始化完成，无待处理条目 |
| 前置校验阶段 | `PRE_CHECK` | 校验funnel_id、result_validated、哈希索引标签有效性（V1.1新增） | 收到晋升候选条目列表 |
| 判定进行中 | `JUDGING` | 逐条校验留存时长、I值阈值、警示标签规则 | 前置校验全部通过 |
| 结果输出 | `OUTPUTTING` | 判定完成，输出晋升清单、带拦截原因的回执 | 全部条目判定完成 |
| 暂停服务 | `SYSTEM_PAUSED` | 系统紧急熔断 | 收到ag-mem-01全局熔断指令 |


## 输入数据

| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L1晋升候选列表 | List（条目ID + funnel_id + I值 + 留存时长 + result_validated + hash_tag_list + 警示标签） | ag-mem-21 L1衰减评估单元 | L1衰减评估判定满足基础晋升条件时 | **高** |
| L2晋升候选列表 | List（条目ID + funnel_id + I值 + 留存时长 + result_validated + hash_tag_list + 警示标签） | ag-mem-22 L2存储单元 | L2条目留存超7天推送晋升候选 | **高** |
| L3晋升候选列表 | List（条目ID + funnel_id + I值 + 留存时长 + result_validated + hash_tag_list + 警示标签） | ag-mem-24 L3存储单元 | L3条目留存超30天推送晋升候选 | **高** |
| L4晋升候选列表 | List（条目ID + funnel_id + I值 + 留存时长 + result_validated + hash_tag_list + 警示标签） | ag-mem-26 L4存储单元 | L4条目满足L5前置条件推送候选 | **高** |
| 全局层级晋升阈值配置 | Struct（按funnel_id隔离：L1→L2, L2→L3, L3→L4, L4→L5晋升I值阈值 + 各层级最小留存时长） | ag-mem-35 三维权重系数配置单元 | 系统初始化加载，每次判定前实时拉取 | **高** |
| 全局调度指令 | Enum（暂停/恢复/熔断） | ag-mem-01 总控漏斗 F₀ | 模式切换或紧急事件时 | **紧急** |


## 输出数据（V1.1新增block_reason标准化拦截字段）

| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 晋升候选清单 | Struct（源层级 + 目标层级 + 条目列表：条目ID + funnel_id + I值 + 留存时长 + 晋升原因 + hash_tag_list） | ag-mem-39 层级单向搬运写入单元 | 判定完成，存在满足全部条件的条目时 | **高** |
| 判定完成回执 | Struct（源层级 + 判定条目总数 + 晋升候选数 + 拒绝数 + 拒绝原因分布：{block_reason: 数量}） | 来源模块（ag-mem-21/22/24/26） | 单批次判定完成必返回 | **高** |
| 警示条目拦截通知 | Struct（条目ID + funnel_id + 当前层级 + block_reason + 建议处理：需先通过安全仲裁） | ag-mem-24 L3存储单元、ag-mem-43 安全仲裁单元 | 检测到CAUTION警示标签且目标层级为L4时 | **高** |
| 全局晋升统计上报 | Struct（各funnel晋升通过率、各层级晋升数量、result_validated拦截总量、索引无效拦截总量） | ag-mem-01 总控漏斗 | 周期性（每120秒）或状态变更时 | 普通 |


## V1.1 三层晋升判定规则（核心升级：新增result_validated前置校验）
### 一、强制前置校验（所有L2及以上晋升，不满足直接拦截）
1. **result_validated 校验**
   - L1→L2：无强制要求，`result_validated=false`可正常晋升
   - L2→L3 / L3→L4 / L4→L5：**必须 result_validated = true**，无客观成功/失败执行结果的记忆直接拦截，`block_reason="result_not_validated"`
2. **funnel_id合法性校验**
   条目携带的funnel_id必须存在于ag-mem-01存量漏斗列表，无效ID直接拦截，`block_reason="invalid_funnel_id"`
3. **哈希索引有效性校验**
   中长期晋升（L3及以上）要求hash_tag_list非空，无业务检索标签的记忆拦截，`block_reason="empty_hash_index_tag"`

### 二、各层级基础晋升双条件（留存时长 + I值）
| 晋升路径 | 留存时长达标条件 | 重要度I值达标条件 | 基础约束 |
|----------|:---:|:---:|------|
| L1→L2 | ≥ 24小时 | I ≥ 该funnel专属L1晋升阈值 | 无强制result校验，无索引强制要求 |
| L2→L3 | ≥ 7天（168小时） | I ≥ 该funnel专属L2晋升阈值 | 必须result_validated=true |
| L3→L4 | ≥ 30天（720小时） | I ≥ 该funnel专属L3晋升阈值 | result_validated=true + hash_tag_list非空 |
| L4→L5 | ≥ 90天（2160小时） | I ≥ 该funnel专属L4晋升阈值 + S值 ≥ 0.9 或 规则置信度 ≥ 0.85 | result_validated=true + 完整哈希索引集合 + 安全令牌校验 |

### 三、动态子漏斗阈值适配说明（彻底废弃V1.0固定分槽阈值表）
V1.1不再区分ag-mem-15~19固定分槽，阈值按`funnel_id`独立存储于ag-mem-35配置单元；
新建子漏斗自动继承通用基准阈值，可随领域使用频次动态微调；
无匹配funnel_id时自动使用全局通用基准阈值兜底。
通用基准阈值（新建漏斗默认初始值）：
| 晋升路径 | 通用基准I值阈值 |
|----------|:---:|
| L1→L2 | 0.42 |
| L2→L3 | 0.62 |
| L3→L4 | 0.82 |
| L4→L5 | 0.92 |

### 四、警示标签拦截规则（保留并适配funnel架构）
| 警示标签 | 允许晋升的目标层级 | 拦截block_reason |
|----------|:---:|------|
| NORMAL | L1→L2, L2→L3, L3→L4, L4→L5 | 无拦截 |
| CAUTION | 仅 L1→L2, L2→L3 | 目标L4时拦截：`block_reason="caution_tag_need_security_arbitrate"` |
| PERMANENT_CAUTION | 仅 L1→L2 | 晋升≥L3时拦截：`block_reason="permanent_caution_forbid_high_layer"` |


## 核心处理逻辑（V1.1完整伪代码，集成动态漏斗+result校验+哈希索引）
```
FUNCTION promotion_judge_main_loop():
    STATE_IDLE = IDLE
    STATE_PRE_CHECK = PRE_CHECK
    STATE_JUDGE = JUDGING
    STATE_OUTPUT = OUTPUTTING
    STATE_PAUSED = SYSTEM_PAUSED

    SET internal_state = STATE_IDLE
    加载全局层级晋升阈值配置（按funnel_id分组，从 ag-mem-35 获取）
    初始化晋升统计计数器（分funnel、分层级统计）

    WHILE 系统运行中:
        // 第1步：全局熔断管控
        IF 收到 ag-mem-01 紧急熔断指令:
            SET internal_state = STATE_PAUSED
            CONTINUE
        ELSE IF 收到恢复指令 AND internal_state == STATE_PAUSED:
            SET internal_state = STATE_IDLE

        // 第2步：接收晋升候选列表
        IF 收到晋升候选列表:
            SET internal_state = STATE_PRE_CHECK
            源层级 = 列表.源层级
            目标层级 = 根据源层级确定目标层级(源层级)
            晋升候选清单 = []
            拒绝条目列表 = []

            FOR EACH 条目 IN 列表.条目列表:
                // ========== V1.1 新增前置三重校验 ==========
                # 校验1：funnel_id是否合法存在
                存量漏斗集合 = ag-mem-01.get_all_funnel_ids()
                IF 条目.funnel_id NOT IN 存量漏斗集合:
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "invalid_funnel_id",
                        拒绝描述: "子漏斗ID不存在，无法参与晋升判定"
                    })
                    CONTINUE

                # 校验2：L2及以上强制result_validated校验
                IF 目标层级 IN ["L3","L4","L5"] AND 条目.result_validated == False:
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "result_not_validated",
                        拒绝描述: "未经过客观执行结果验证，禁止晋升至L3及以上层级"
                    })
                    CONTINUE

                # 校验3：L3及以上要求哈希索引标签非空
                IF 目标层级 IN ["L4","L5"] AND len(条目.hash_tag_list) == 0:
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "empty_hash_index_tag",
                        拒绝描述: "无哈希检索索引标签，禁止中长期晋升"
                    })
                    CONTINUE
                // ========== 前置校验结束 ==========

                // 2a. 获取当前funnel专属晋升阈值
                funnel_threshold_map = 晋升阈值配置表.get(条目.funnel_id, 通用基准阈值)
                CASE (源层级, 目标层级) OF:
                    (L1, L2): 晋升I值阈值 = funnel_threshold_map.L1_up
                    (L2, L3): 晋升I值阈值 = funnel_threshold_map.L2_up
                    (L3, L4): 晋升I值阈值 = funnel_threshold_map.L3_up
                    (L4, L5): 晋升I值阈值 = funnel_threshold_map.L4_up

                // 2b. 校验永久警示标签
                IF 条目.警示标签 == "PERMANENT_CAUTION" AND 目标层级 != "L2":
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "permanent_caution_forbid_high_layer",
                        拒绝描述: "永久警示标签，仅允许晋升至L2"
                    })
                    CONTINUE

                // 2c. CAUTION标签拦截L4晋升
                IF 条目.警示标签 == "CAUTION" AND 目标层级 == "L4":
                    // 发送拦截通知至安全仲裁模块
                    向 ag-mem-24 发送警示条目拦截通知(条目.条目ID, 条目.funnel_id, 源层级)
                    向 ag-mem-43 发送失败经验仲裁请求(条目)
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "caution_tag_need_security_arbitrate",
                        拒绝描述: "存在风险警示标签，晋升L4前需完成安全仲裁"
                    })
                    CONTINUE

                // 2d. 校验留存时长条件
                最小留存时长 = 获取层级最小留存时长(源层级, 目标层级)
                IF 条目.留存时长 < 最小留存时长:
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "retention_time_insufficient",
                        拒绝描述: f"留存时长不足，最低要求{最小留存时长}小时"
                    })
                    CONTINUE

                // 2e. 校验重要度I值阈值
                IF 条目.I值 < 晋升I值阈值:
                    拒绝条目列表.append({
                        条目ID: 条目.条目ID,
                        block_reason: "importance_insufficient",
                        拒绝描述: f"I值不达标，当前{条目.I值}，阈值{晋升I值阈值}"
                    })
                    CONTINUE

                // 2f. L4→L5专属特殊校验
                IF 源层级 == "L4" AND 目标层级 == "L5":
                    IF 条目.S值 < 0.9 AND 条目.规则置信度 < 0.85:
                        拒绝条目列表.append({
                            条目ID: 条目.条目ID,
                            block_reason: "l5_s_confidence_not_match",
                            拒绝描述: "S值与规则置信度未满足L5核心层准入标准"
                        })
                        CONTINUE

                // 全部校验通过，加入晋升候选清单
                晋升候选清单.append({
                    条目ID: 条目.条目ID,
                    funnel_id: 条目.funnel_id,
                    I值: 条目.I值,
                    留存时长: 条目.留存时长,
                    hash_tag_list: 条目.hash_tag_list,
                    晋升原因: f"全部校验通过：留存{条目.留存时长}h + I值{条目.I值} ≥ {晋升I值阈值} + 结果验证标记有效",
                })

            // 第3步：输出完整判定结果
            SET internal_state = STATE_OUTPUT

            IF len(晋升候选清单) > 0:
                向 ag-mem-39 发送晋升候选清单(源层级, 目标层级, 晋升候选清单)

            // 组装标准化回执，携带完整block_reason分布
            原因统计字典 = {}
            FOR item IN 拒绝条目列表:
                reason = item["block_reason"]
                原因统计字典[reason] = 原因统计字典.get(reason, 0) + 1

            回执数据 = {
                源层级=源层级,
                判定条目总数=len(列表.条目列表),
                晋升候选数=len(晋升候选清单),
                拒绝数=len(拒绝条目列表),
                拒绝原因分布=原因统计字典
            }
            向 请求来源模块 返回判定完成回执(回执数据)

            // 上报全局统计至总控漏斗ag-mem-01
            更新晋升全局统计(源层级, 目标层级, 晋升候选清单, 拒绝条目列表)
            SET internal_state = STATE_IDLE

        // 第4步：周期性全局统计上报
        IF 距上次状态上报 >= 120秒:
            全局统计数据 = 生成分层级、分funnel晋升统计快照()
            向 ag-mem-01 上报判定状态(全局统计数据)
            重置局部统计计数器

        SLEEP 10ms


FUNCTION 获取层级最小留存时长(源层级, 目标层级):
    时长表 = {
        ("L1", "L2"): 24,
        ("L2", "L3"): 168,
        ("L3", "L4"): 720,
        ("L4", "L5"): 2160
    }
    RETURN 时长表.GET((源层级, 目标层级), 24)
```


## 约束与异常处理（V1.1新增动态漏斗、索引、result校验异常分支）
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 条目funnel_id不存在/无效 | 直接拦截，block_reason=invalid_funnel_id | 条目关联合法动态子漏斗 |
| L2+条目无result_validated=true | 拦截晋升，标记结果未验证 | 执行层回写有效成功/失败执行标记 |
| L3+条目无哈希索引标签 | 拦截中长期晋升，仅可停留在L1/L2 | 路由单元生成有效hash_tag写入条目 |
| 条目缺少funnel_id字段 | 归入通用兜底漏斗阈值判定，记录异常日志 | 上层路由模块补全funnel_id |
| 条目I值异常（<0.05或>1.0） | 视为不满足晋升条件，拒绝晋升，记录异常日志 | ag-mem-30重要度计算模块修正I值 |
| 警示标签CAUTION且目标为L4 | 自动拦截，异步发起ag-mem-43安全仲裁，不阻塞当前批次判定 | 安全仲裁通过后下一轮复检自动放行 |
| 同一批次中包含重复条目ID | 仅判定一次，以首次出现的为准，后续重复跳过 | — |
| 全局熔断触发 | 暂停所有晋升判定，缓存未处理条目列表，恢复后续跑 | ag-mem-01下发解除熔断指令 |


## 总线契约（V1.1更新字段：全部替换分槽编号为funnel_id，新增result、索引字段传输）
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L1/L2/L3/L4 晋升候选列表（携带funnel_id、result_validated、hash_tag_list） | 只读 | ag-mem-21/22/24/26 发送 |
| 内部调度总线 | 读 | 按funnel分组的层级晋升阈值配置 | 只读 | ag-mem-35 提供 |
| 内部调度总线 | 读 | 全局存量子漏斗ID集合 | 只读 | ag-mem-01 总控漏斗提供 |
| 内部调度总线 | 写 | 晋升候选清单（携带funnel_id、hash_tag_list） | 专属写入 | 向 ag-mem-39 层级搬运单元发送 |
| 内部调度总线 | 写 | 判定完成回执（标准化block_reason拒绝原因） | 专属写入 | 向各来源存储单元返回 |
| 内部调度总线 | 写 | 警示条目拦截通知（携带funnel_id） | 事件触发写入 | 向 ag-mem-24、ag-mem-43 安全仲裁发送 |
| 内部调度总线 | 写 | 全局分漏斗晋升统计快照 | 周期性写入 | 向 ag-mem-01 总控漏斗全局管控 |


## 安全边界（V1.1新增结果验证、索引隔离安全规则）
| 规则编号 | 内容 |
|:---:|------|
| P-01 | 永久警示标签PERMANENT_CAUTION条目仅允许晋升至L2，禁止进入L3及以上长期记忆层 |
| P-02 | CAUTION风险警示条目禁止直接晋升至L4核心经验层，必须完成ag-mem-43三道失败经验安全仲裁 |
| P-03 | 晋升三大前置校验（funnel合法、结果验证、索引标签）优先级高于时长/I值双条件，前置不通过直接拦截，不进入基础判定 |
| P-04 | L2及以上中长期记忆强制绑定客观执行结果，杜绝纯对话、无实际执行的无效记忆污染长期层 |
| P-05 | 所有晋升阈值按动态子漏斗隔离，不同领域漏斗使用独立阈值，不使用全局一刀切固定分槽阈值 |
| P-06 | 哈希索引标签作为中长期记忆准入门槛，保证检索性能，避免无标签冗余记忆占用长期存储 |
| P-07 | 所有拦截行为统一输出标准化block_reason，可统计、可追溯、可复盘记忆晋升失败原因 |


## 接口校验用例（适配V1.1动态漏斗+result校验+哈希索引）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M38-01 | `IDLE`，合法funnel、L2条目全部校验通过 | L2晋升候选（funnel_id=F001，留存=180h, I=0.65, result_validated=true，hash_tag=["Python","代码"]） | 通过判定，加入晋升候选清单 |
| TC-M38-02 | `IDLE`，L3条目result_validated=false | L3晋升候选（result_validated=false，其余条件全部达标） | 拒绝晋升，block_reason="result_not_validated" |
| TC-M38-03 | `IDLE`，L4条目无哈希标签 | L4晋升候选（hash_tag_list为空，result=true、时长I值达标） | 拒绝晋升，block_reason="empty_hash_index_tag" |
| TC-M38-04 | `IDLE`，条目携带无效funnel_id | 晋升候选funnel_id=F999（不存在） | 直接拦截，block_reason="invalid_funnel_id" |
| TC-M38-05 | `IDLE`，L3条目I值低于funnel专属阈值 | L3晋升候选（I=0.75，funnel阈值0.82） | 拒绝晋升，block_reason="importance_insufficient" |
| TC-M38-06 | `IDLE`，L3条目留存时长不足 | L3晋升候选（留存=600h，最低720h） | 拒绝晋升，block_reason="retention_time_insufficient" |
| TC-M38-07 | `IDLE`，L3条目带CAUTION标签，目标L4 | 候选条目警示标签=CAUTION | 拦截并推送安全仲裁通知，block_reason="caution_tag_need_security_arbitrate" |
| TC-M38-08 | `IDLE`，L4条目满足全部L5准入条件 | L4晋升候选（result=true、索引完整、S=0.96、置信度0.9） | 通过判定，进入L5晋升候选清单 |
| TC-M38-09 | `IDLE`，L1条目无result_validated标记 | L1晋升候选result_validated=false | 正常放行晋升L2，无拦截（L1无结果强制校验） |


## 质量自检清单（V1.1完整达标）
| 检查项 | 状态 |
|--------|:---:|
| 彻底移除V1.0固定分槽编号依赖，全链路使用funnel_id标识子漏斗 | ✅ |
| 新增V1.1核心前置校验：result_validated客观结果验证（L2+强制） | ✅ |
| 新增funnel_id合法性前置校验，拦截无效子漏斗条目 | ✅ |
| 新增哈希索引标签准入校验，管控中长期记忆存储性能 | ✅ |
| 所有拒绝输出标准化block_reason字段，支持全局统计复盘 | ✅ |
| 阈值逻辑改为按funnel动态独立配置，废弃固定5分槽阈值表 | ✅ |
| 状态机新增PRE_CHECK前置校验阶段，区分新三层校验流程 | ✅ |
| 输入输出结构体全部补充funnel_id、result_validated、hash_tag_list字段 | ✅ |
| 异常处理覆盖无效漏斗、无结果标记、空索引标签等V1.1新增场景 | ✅ |
| 校验用例覆盖动态漏斗、结果校验、索引校验、各类拦截分支、L1豁免规则 | ✅ |
| 完全对齐V1.1白皮书晋升机制、哈希索引、动态子漏斗架构定义 | ✅ |

---