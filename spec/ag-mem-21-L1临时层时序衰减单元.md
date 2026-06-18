## V1.1 模块升级总说明
### 重大变更点
1. 彻底移除V1.0 ag-mem-15~19固定场景分槽逻辑，全流程以`funnel_id`作为分桶唯一标识，适配动态子漏斗架构；
2. 晋升阈值体系重构：抛弃静态分槽阈值表，由ag-mem-01下发每个funnel独立可调阈值，适配不同领域经验沉淀节奏；
3. 新增哈希索引协同逻辑：自动提取清除条目全部标签，回传给L1存储单元清理独立索引桶，避免残留无效索引占用资源；
4. 输入请求改为**单funnel独立批量评估**，不同子漏斗衰减任务相互隔离，单一领域容量拥堵不影响其他漏斗；
5. 输入输出结构体新增`funnel_id`、`hash_tag_list`、`index_bucket_id`、`result_validated`，与ag-mem-01、ag-mem-14、ag-mem-20、ag-mem-38全链路字段统一；
6. 依赖新增总控漏斗ag-mem-01，用于拉取全局漏斗注册表、动态阈值快照、接收全局熔断指令；
7. 原有时长+I值双维度判定核心规则完整保留，仅适配分桶架构、索引配套做分支扩展，原有衰减业务逻辑无丢失；
8. 增加非法funnel_id兜底处理机制，保证冷启动、漏斗合并过程中评估流程不中断。


# ag-mem-21-L1临时层时序衰减单元 接口规格（V1.1 版，适配funnel分桶+哈希索引+result_validated校验）
---
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-21 |
| 模块名称 | L1临时层时序衰减单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层存储 |
| 核心职责 | 接收 ag-mem-20（L1临时层存储单元）按单funnel拆分的衰减评估请求，**废弃V1.0固定场景分槽编号，统一使用funnel_id作为分桶标识**。<br>基于条目留存时长、I重要度、funnel专属晋升阈值、`result_validated`客观结果标记四层维度做衰减判定；同步关联条目哈希标签集合，为存储层同步失效索引标签提供清单。<br>判定条目三类去向：晋升至L2、留存L1、永久清除；晋升条目携带完整funnel_id、hash_tag_list、index_bucket下发L2存储；清除条目同步输出待清理哈希标签列表供ag-mem-20回收索引资源。<br>仅做元数据判定分流，不修改原始经验内容，不直接操作持久化存储。 |
| 依赖模块 | ag-mem-01（总控漏斗F0，拉取全量funnel列表、各funnel专属晋升阈值）、ag-mem-20（L1存储，分funnel下发待评估条目、哈希标签集合）、ag-mem-22（L2近期层存储，接收分funnel晋升条目）、ag-mem-35（三维权重配置，同步各funnel阈值基准）、ag-mem-42（冗余记忆删除归档，接收待清除条目） |
| 被依赖模块 | ag-mem-20（返回单funnel衰减完成回执、待清理哈希标签清单） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 空闲等待 | `IDLE` | 无评估任务，等待单funnel衰减请求 | 初始化完成，无待处理条目 |
| 评估进行中 | `EVALUATING` | 逐条按funnel维度校验时长、I值、result_validated、阈值 | 收到ag-mem-20分桶衰减请求 |
| 结果输出 | `OUTPUTTING` | 分流晋升/清除/保留条目，组装哈希清理标签清单 | 本funnel全部条目评估完毕 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止所有衰减评估 | 接收ag-mem-01全局熔断指令 |

## 输入数据（V1.1 移除分槽编号，新增funnel、索引、结果标记字段）
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 单funnel L1衰减评估请求 | Struct（funnel_id + 待评估条目列表 + 触发原因：定时/容量预警/容量紧急 + 该funnel当前使用率） | ag-mem-20 L1存储单元 | 单漏斗容量超限/6小时定时轮询 | **高** |
| L1条目完整元数据 | Struct（entry_id + funnel_id + importance(I值) + create_ts + hash_tag_list + index_bucket_id + result_validated） | ag-mem-20 随评估请求携带 | 每条待评估经验附属元数据 | **高** |
| 全局funnel晋升阈值快照 | Map<funnel_id, {L1_up_threshold, forget_threshold}> | ag-mem-01总控漏斗F0 | 初始化加载、配置变更时刷新 | 普通 |
| 全局调度/熔断指令 | Enum（暂停/恢复/熔断） | ag-mem-01 总控漏斗F0 | 系统紧急管控、模式切换 | **紧急** |

## 输出数据（全链路携带funnel与哈希索引信息）
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分funnel晋升条目列表 | List（entry_id + funnel_id + hash_tag_list + index_bucket_id + I值 + 留存时长 + result_validated + 晋升原因） | ag-mem-22 L2存储单元 | 存在满足晋升条件条目 | **高** |
| 分funnel清除条目+待清理索引标签清单 | Struct（clean_entry_ids:列表, clean_hash_tags:合并去重标签集合, funnel_id） | ag-mem-42 删除单元、同步返回ag-mem-20 | 存在需永久清除条目 | **高** |
| 保留条目确认清单 | Struct（funnel_id + retain_entry_count + retain_entry_ids + 本次评估时间戳） | ag-mem-20 L1存储单元 | 本funnel评估完成必返回 | **高** |
| 单funnel衰减评估完成回执 | Struct（funnel_id + total_eval_count + promote_count + clean_count + retain_count + eval_cost_ms） | ag-mem-20 L1存储单元 | 单漏斗全条目评估结束 | **高** |

## V1.1 四层衰减判定规则（替换原固定分槽阈值逻辑）
### 一、基础二维时长+I值判定矩阵（全局统一时长阈值）
| 留存时长 | I值区间 | 基础处理分支 | 叠加约束 |
|----------|---------|-------------|----------|
| < 24h | 任意I | 保留L1 | 不参与晋升判定，不校验result_validated |
| ≥24h | I ≥ funnel晋升阈值 | 待晋升L2 | L1→L2仅做弱校验，result_validated=true/false均可晋升 |
| ≥24h | 遗忘阈值 ≤ I < 晋升阈值 | 保留L1 | 无清除风险，下次衰减再复检 |
| ≥24h | I < 遗忘阈值 | 永久清除 | 低价值直接删除 |
| ≥72h | I < 晋升阈值 | 强制清除 | L1长期滞留无法晋升，直接清理 |

### 二、容量紧急场景阈值收紧规则（单funnel独立生效，不修改全局配置）
触发原因=容量紧急时：
1. 最小评估留存时长由24h下调至6h
2. 单funnel遗忘阈值临时上浮20%，更容易清理低价值条目
3. 晋升阈值保持funnel专属原值不变

### 三、funnel专属阈值规则（废弃ag-mem-15~19固定阈值表）
1. 新建funnel自动加载通用基准阈值：L1晋升阈值0.42，遗忘阈值0.06
2. 长期高频领域funnel阈值由ag-mem-35动态微调，通过ag-mem-01统一下发至本模块
3. 不存在的funnel_id统一兜底使用通用基准阈值

### 四、哈希索引配套规则
1. 所有被清除条目提取自身`hash_tag_list`，合并去重生成待清理标签集合，同步回传给ag-mem-20
2. ag-mem-20收到标签清单后，从该funnel独立index_bucket中删除失效条目ID映射，释放索引空间

## 核心处理逻辑（V1.1伪代码，分funnel独立评估+索引标签归集）
```
FUNCTION l1_decay_assessment_main_loop():
    STATE_IDLE = IDLE
    STATE_EVAL = EVALUATING
    STATE_OUTPUT = OUTPUTTING
    STATE_PAUSED = SYSTEM_PAUSED

    SET internal_state = STATE_IDLE
    // 初始化拉取全局所有funnel专属阈值
    global_funnel_threshold_map = ag-mem-01.get_all_funnel_l1_threshold()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级
        IF 收到 ag-mem-01 熔断指令:
            SET internal_state = STATE_PAUSED
            CONTINUE
        ELSE IF 收到恢复指令 AND internal_state == SYSTEM_PAUSED:
            SET internal_state = STATE_IDLE

        // 2. 接收单funnel衰减评估请求
        IF 收到 ag-mem-20 下发的单funnel衰减请求:
            SET internal_state = STATE_EVAL
            target_funnel = 请求.funnel_id
            entry_list = 请求.待评估条目列表
            trigger_type = 请求.触发原因
            eval_start_ts = NOW()

            promote_list = []
            clean_entry_ids = []
            retain_entry_ids = []
            all_clean_tags = set() // 归集所有待删除条目哈希标签，去重

            // 判定是否容量紧急，调整阈值参数
            is_cap_critical = (trigger_type == "容量紧急")
            base_min_hour = 24
            threshold_scale = 1.0
            IF is_cap_critical:
                base_min_hour = 6
                threshold_scale = 1.2

            // 获取当前funnel专属阈值，不存在则使用通用基准
            funnel_thresh = global_funnel_threshold_map.get(target_funnel, {
                L1_up_threshold:0.42,
                forget_threshold:0.06
            })
            promote_thresh = funnel_thresh.L1_up_threshold
            forget_thresh = funnel_thresh.forget_threshold * threshold_scale

            // 逐条评估本funnel下所有条目
            FOR entry IN entry_list:
                entry_id = entry.entry_id
                i_val = entry.importance
                create_ts = entry.create_ts
                tag_list = entry.hash_tag_list
                funnel_id = entry.funnel_id
                index_bucket = entry.index_bucket_id
                res_valid = entry.result_validated
                retain_hour = (NOW() - create_ts) / 3600

                // 规则1：滞留超72小时且未达标，强制清除
                IF retain_hour >= 72 AND i_val < promote_thresh:
                    clean_entry_ids.append(entry_id)
                    // 归集哈希标签用于索引清理
                    for tag in tag_list:
                        all_clean_tags.add(tag)
                    CONTINUE

                // 规则2：未达最小评估时长，直接保留
                IF retain_hour < base_min_hour:
                    retain_entry_ids.append(entry_id)
                    CONTINUE

                // 规则3：满足晋升条件，送入L2晋升队列
                IF i_val >= promote_thresh:
                    promote_list.append({
                        entry_id: entry_id,
                        funnel_id: funnel_id,
                        hash_tag_list: tag_list,
                        index_bucket_id: index_bucket,
                        I值: i_val,
                        留存时长: retain_hour,
                        result_validated: res_valid,
                        晋升原因: f"满足funnel{funnel_id} L1→L2双条件：时长≥{base_min_hour}h + I≥{promote_thresh}"
                    })
                    CONTINUE

                // 规则4：低于遗忘阈值，清除
                IF i_val < forget_thresh:
                    clean_entry_ids.append(entry_id)
                    for tag in tag_list:
                        all_clean_tags.add(tag)
                    CONTINUE

                // 其余情况：留存L1
                retain_entry_ids.append(entry_id)

            // 3. 评估完成，进入输出阶段
            SET internal_state = STATE_OUTPUT
            total_eval = len(entry_list)
            promote_cnt = len(promote_list)
            clean_cnt = len(clean_entry_ids)
            retain_cnt = len(retain_entry_ids)
            eval_cost = NOW() - eval_start_ts

            // 3a. 推送晋升条目至L2存储单元
            IF promote_cnt > 0:
                向 ag-mem-22 发送分funnel晋升条目列表(promote_list)

            // 3b. 推送清除条目至删除单元，同步标签清单回L1清理索引
            IF clean_cnt > 0:
                clean_package = {
                    funnel_id: target_funnel,
                    clean_entry_ids: clean_entry_ids,
                    clean_hash_tags: list(all_clean_tags)
                }
                向 ag-mem-42 发送清除条目列表(clean_package.clean_entry_ids)
                // 同步告知L1需要清理的哈希标签，更新独立索引桶
                向 ag-mem-20 发送待清理索引标签清单(clean_package)

            // 3c. 告知L1需要保留的条目
            retain_confirm = {
                funnel_id: target_funnel,
                retain_entry_count: retain_cnt,
                retain_entry_ids: retain_entry_ids,
                eval_ts: NOW()
            }
            向 ag-mem-20 发送保留条目确认(retain_confirm)

            // 3d. 返回完整衰减完成回执
            finish_receipt = {
                funnel_id: target_funnel,
                total_eval_count: total_eval,
                promote_count: promote_cnt,
                clean_count: clean_cnt,
                retain_count: retain_cnt,
                eval_cost_ms: eval_cost
            }
            向 ag-mem-20 发送单funnel衰减评估完成回执(finish_receipt)

            SET internal_state = STATE_IDLE

        SLEEP 10ms
```

## 约束与异常处理（V1.1新增funnel、索引相关异常）
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 条目funnel_id不存在，无法匹配专属阈值 | 自动使用通用基准阈值0.42/0.06，记录日志 | ag-mem-01完成漏斗创建同步注册表 |
| 条目I值缺失/异常（<0或>1） | 统一判定为保留L1，标记异常日志，不清除不晋升 | ag-mem-30重要度模块修正数值 |
| 晋升条目写入ag-mem-22失败返回拒绝 | 该批晋升条目全部转入保留列表，下一轮衰减重新判定 | L2存储服务恢复正常 |
| 待评估条目列表为空（当前funnel无数据） | 直接返回回执，各项计数为0，无额外输出 | — |
| 全局系统熔断触发 | 停止所有funnel衰减评估，缓存未处理请求，恢复后批量执行 | ag-mem-01下发恢复指令 |
| 单条目hash_tag_list为空 | 正常参与衰减判定，清除时无标签需要同步清理索引 | 路由模块补全领域标签后新写入条目 |

## 总线契约（全部替换分槽编号为funnel_id，新增索引标签传输）
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 单funnel衰减评估请求（携带funnel_id、条目元数据含hash_tag、index_bucket、result_validated） | 只读 | ag-mem-20 发送 |
| 内部调度总线 | 读 | 全局funnel阈值映射表 | 只读 | ag-mem-01总控漏斗下发 |
| 内部调度总线 | 读 | 全局熔断调度指令 | 只读 | ag-mem-01下发 |
| 内部调度总线 | 写 | 分funnel晋升条目列表（携带funnel、索引桶、哈希标签） | 专属写入 | 向 ag-mem-22 L2存储发送 |
| 内部调度总线 | 写 | 清除条目+待清理哈希标签集合 | 专属写入 | 向ag-mem-42发送删除列表，同步回ag-mem-20清理索引 |
| 内部调度总线 | 写 | 保留条目确认清单（绑定funnel_id） | 专属写入 | 向 ag-mem-20 返回 |
| 内部调度总线 | 写 | 单funnel衰减完成统计回执 | 专属写入 | 向 ag-mem-20 返回 |

## 安全边界（V1.1新增动态漏斗、索引隔离约束）
| 规则编号 | 内容 |
|:---:|------|
| S-01 | 衰减仅读取条目元数据（时长、I值、funnel、哈希标签、result标记），禁止读取、修改原始经验正文内容 |
| S-02 | 所有晋升、清除、保留操作严格隔离funnel，禁止跨漏斗混处理条目，保证分桶数据隔离 |
| S-03 | 容量紧急阈值上浮仅本次评估临时生效，不持久化修改ag-mem-01/ag-mem-35全局阈值配置 |
| S-04 | 条目清除必须交由ag-mem-42执行安全持久化删除；本模块仅输出ID清单，不直接操作存储、哈希索引 |
| S-05 | 清除条目自动归集哈希标签并回传给L1存储单元，必须同步销毁对应funnel索引桶内的条目映射，防止索引残留脏数据 |
| S-06 | 不允许处理未在ag-mem-01注册表内的funnel_id条目，无匹配漏斗时仅兜底阈值评估，不发起晋升写入 |

## 接口校验用例（适配funnel分桶、哈希索引、动态阈值）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M21-01 | `IDLE`，funnel=F001，条目留存26h、I=0.45、晋升阈值0.42 | 单funnel衰减请求，条目hash_tag=["Python","排序"] | 加入晋升列表，携带funnel_id与完整哈希标签下发ag-mem-22 |
| TC-M21-02 | `IDLE`，funnel=F002，留存30h，I=0.05，遗忘阈值0.06 | 常规衰减触发 | 归入清除列表，提取标签回传给ag-mem-20清理索引桶 |
| TC-M21-03 | `IDLE`，条目留存10h，I=0.6 | 任意funnel衰减请求 | 直接加入保留列表，不参与晋升判定 |
| TC-M21-04 | `IDLE`，条目留存80h，I=0.35，funnel晋升阈值0.42 | 常规衰减 | 滞留超72小时强制清除，归集标签清理索引 |
| TC-M21-05 | `IDLE`，触发类型=容量紧急，最小时长6h，遗忘阈值上浮20% | 条目留存8h，I=0.10，原遗忘阈值0.06→上浮0.072 | 低于临时阈值，归入清除列表 |
| TC-M21-06 | `IDLE`，目标funnel无任何待评估条目 | 空条目列表衰减请求 | 返回回执各项计数为0，无晋升/清除输出 |
| TC-M21-07 | `SYSTEM_PAUSED`，收到任意funnel衰减请求 | 分桶衰减评估请求 | 不执行评估，等待恢复指令后处理队列 |
| TC-M21-08 | `IDLE`，条目绑定不存在funnel=F999 | 衰减请求携带无效funnel_id | 使用通用基准阈值评估，正常分流，记录异常日志 |

## 质量自检清单（V1.1完整达标）
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、五层存储分区不变，完整移除V1.0固定分槽编号逻辑 | ✅ |
| 新增依赖ag-mem-01总控漏斗拉取全局funnel专属阈值 | ✅ |
| 状态机原有4种状态完全保留，流程适配单funnel独立评估 | ✅ |
| 输入输出全部替换funnel_id标识，新增hash_tag_list、index_bucket、result_validated字段 | ✅ |
| 废弃固定5分槽静态阈值表，改为按funnel动态阈值体系 | ✅ |
| 新增哈希索引标签归集、同步清理配套逻辑，联动ag-mem-20索引桶 | ✅ |
| 伪代码完整实现分漏斗评估、阈值动态调整、标签归集、分流输出全链路 | ✅ |
| 异常处理覆盖无效funnel、I值异常、晋升写入失败、熔断、空列表等场景 | ✅ |
| 总线契约统一传输funnel与哈希索引相关字段，无旧分槽编号传输 | ✅ |
| 安全边界新增漏斗隔离、索引脏数据清理、非法漏斗兜底规则 | ✅ |
| 校验用例覆盖正常晋升、低价值清除、时长不足、滞留超时、容量紧急、无效漏斗、熔断场景 | ✅ |
| 完全对齐V1.1动态子漏斗、哈希索引分桶、MLNF-Mem五层存储架构 | ✅ |

---