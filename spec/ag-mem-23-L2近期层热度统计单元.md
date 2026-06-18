## V1.1 模块升级总说明
### 重大变更点
1. 废弃V1.0基于固定ag-mem-15~19分槽的统计维度，重构为`funnel_id`动态子漏斗独立隔离统计，各领域热度数据互不干扰；
2. 新增哈希标签热度联动统计：条目命中同步更新单条目标签分布、漏斗全局标签聚合热度，为检索权重、晋升判定提供标签维度参考；
3. 全链路输入输出统一新增`funnel_id`、`index_bucket_id`、`hash_tag_list`，与ag-mem-01/22/38/33全模块字段互通兼容；
4. 内存存储结构分层重构，顶层以funnel为根分区，实现单漏斗独立计数、独立标签聚合；
5. 定时清理逻辑升级为全漏斗批量快照比对，一次性删除所有已从L2移除条目的失效统计；
6. 新增全局熔断管控分支，熔断状态阻断所有统计写入，仅保留存量查询能力；
7. 原有核心统计能力（条目命中计数、24h/7d/全生命周期窗口统计、周期上报）完整保留，仅增加funnel与标签扩展分支，无原有业务逻辑丢失；
8. 增加非法漏斗自动创建+异常标记机制，兼容漏斗新建、合并过程中时序错位产生的临时未知funnel事件。



# ag-mem-23-L2近期层热度统计单元 接口规格（V1.1 适配funnel分桶+哈希索引架构）
---
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-23 |
| 模块名称 | L2近期层热度统计单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层存储 |
| 核心职责 | 面向动态funnel分桶架构，独立统计每个子漏斗内经验条目检索命中频次。接收ag-mem-22推送的分funnel新增条目通知、查询命中事件；按`funnel_id`隔离维护热度统计内存表，每条统计绑定`entry_id + funnel_id`二元主键，同步记录条目关联hash标签访问热度。<br>对外提供单/批量条目热度窗口数据，作为ag-mem-33（C复用频次统计）、ag-mem-38（晋升双条件判定）的权重输入；支持按funnel维度聚合热度指标，用于全局漏斗资源调度。<br>仅做命中事件采集、窗口计数聚合、标签热度统计，不读取原始经验正文、不参与晋升/清理决策；内存级临时统计，不持久化落地。 |
| 依赖模块 | ag-mem-01（总控F0，下发全局熔断指令、读取合法funnel注册表）、ag-mem-22（L2存储，推送分funnel新增条目、命中事件、全漏斗有效条目ID快照）、ag-mem-33（复用频次C值单元，批量拉取L2分层热度数据）、ag-mem-38（晋升判定单元，查询单funnel条目热度权重） |
| 被依赖模块 | ag-mem-33（输出分funnel窗口命中统计）、ag-mem-38（输出条目热度权重+标签热度分布）、ag-mem-03（漏斗二调度，接收周期性全漏斗热度汇总上报） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 空闲等待 | `IDLE` | 无更新/查询任务，等待funnel维度事件推送 | 初始化完成，各funnel统计分区创建完毕 |
| 统计更新中 | `UPDATING` | 批量更新某funnel下条目命中计数、标签热度 | 收到ag-mem-22分funnel命中条目列表 |
| 查询响应中 | `RESPONDING` | 处理外部模块批量热度查询，按funnel隔离返回数据 | ag-mem-33/ag-mem-38发起热度查询请求 |
| 暂停服务 | `SYSTEM_PAUSED` | 全局熔断，停止所有统计写入、仅存量数据只读查询 | 接收ag-mem-01全局熔断调度指令 |

## 输入数据（V1.1 删除固定分槽编号，新增funnel、hash_tag、index_bucket字段）
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分funnel L2新条目通知 | Struct(funnel_id, index_bucket_id, new_entry_ids:List, hash_tag_list:List[List], write_ts) | ag-mem-22 L2存储单元 | L2完成分funnel晋升条目写入后 | 普通 |
| 分funnel查询命中条目事件列表 | List[Struct(entry_id, funnel_id, hit_ts, hash_tag_list, index_bucket_id)] | ag-mem-22 L2存储单元 | L2分funnel检索命中条目后实时推送 | **高** |
| 分funnel热度批量查询请求 | Struct(target_funnel_id, entry_id_list, stat_window:24h/7d/all, query_type:single/batch) | ag-mem-33 / ag-mem-38 | 计算C值、晋升权重时拉取热度 | **高** |
| 全漏斗有效条目快照查询回执 | Map<funnel_id, List<entry_id>> | ag-mem-22 | 每24小时定时清理失效统计时拉取 | 普通 |
| 全局调度/熔断指令 | Enum(暂停/恢复/全局熔断) | ag-mem-01 总控漏斗F0 | 系统紧急管控、模式切换 | **紧急** |

## 输出数据（全链路携带funnel分桶标识+哈希标签热度）
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分funnel条目热度窗口统计包 | Struct(funnel_id, entry_hit_map:Map<entry_id, HitStat>, tag_hit_agg:Map<tag, hit_count>) | ag-mem-33、ag-mem-38 | 处理完热度查询请求 | **高** |
| 全漏斗热度汇总状态上报 | Struct(global_total_stat_item, each_funnel_agg:Map<funnel_id, total_hit_24h/7d/all>, memory_usage) | ag-mem-03 漏斗二调度单元 | 每60秒周期性上报、状态变更时 | 普通 |

## V1.1 分层统计数据结构（按funnel物理隔离）
```
# 顶层：按funnel隔离独立统计分区
funnel_heat_root = {
    "F001": {
        index_bucket_id: "idx-F001",
        entry_stat_map: {  # 二元主键：funnel_id+entry_id
            "L2-F001-0001": {
                total_hit: int,          # 全生命周期总命中
                hit_24h: int,            # 近24小时命中
                hit_7d: int,             # 近7天命中
                last_hit_ts: timestamp,  # 最近一次命中时间
                first_hit_ts: timestamp, # 首次命中时间
                tag_hit_dist: Dict[str, int] # 本条目标签命中分布
            }
        },
        global_tag_agg: Dict[str, int] # 当前funnel全局标签总命中统计
    }
}
```

## 配套统计约束规则
1. **funnel数据隔离**：不同子漏斗的热度数据完全独立，禁止跨funnel聚合计数、查询；
2. **标签热度联动更新**：条目命中时，同步累加条目自身所有hash_tag的漏斗维度全局标签计数；
3. **内存淘汰机制**：单funnel统计内存占用超阈值时，LRU淘汰30天无命中条目统计记录；
4. **滑动窗口近似统计**：不存储每条命中完整时间戳，仅做计数累加，上层ag-mem-33统一做时间窗口归一化；
5. **失效清理周期**：每24小时拉取L2全漏斗有效条目快照，删除已从L2移除条目的全部热度统计；
6. **熔断只读规则**：熔断状态下仅支持存量数据查询，禁止新增/更新命中计数、初始化新条目统计。

## 核心处理逻辑（V1.1 伪代码，funnel分桶+标签热度聚合）
```
FUNCTION l2_heat_statistics_main_loop():
    STATE_IDLE = IDLE
    STATE_UPDATE = UPDATING
    STATE_RESPOND = RESPONDING
    STATE_PAUSED = SYSTEM_PAUSED

    SET internal_state = STATE_IDLE
    # 顶层分funnel独立热度根存储
    funnel_heat_root = {}
    last_daily_clean_ts = NOW()
    last_report_ts = NOW()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级拦截
        IF 收到 ag-mem-01 全局熔断指令:
            SET internal_state = SYSTEM_PAUSED
            CONTINUE
        ELSE IF 收到恢复指令 AND internal_state == SYSTEM_PAUSED:
            SET internal_state = STATE_IDLE

        // 2. 接收分funnel新条目通知，初始化空统计记录
        IF 收到 ag-mem-22 分funnel L2新条目通知:
            IF internal_state == SYSTEM_PAUSED:
                CONTINUE
            target_fid = 请求.funnel_id
            idx_bucket = 请求.index_bucket_id
            new_entry_ids = 请求.new_entry_ids
            entry_tags_batch = 请求.hash_tag_list

            // 漏斗不存在则新建独立统计分区
            IF target_fid NOT IN funnel_heat_root:
                funnel_heat_root[target_fid] = {
                    index_bucket_id: idx_bucket,
                    entry_stat_map: {},
                    global_tag_agg: {}
                }
            funnel_stat = funnel_heat_root[target_fid]

            // 逐条初始化条目热度记录（命中计数初始0）
            FOR idx, eid IN enumerate(new_entry_ids):
                tag_list = entry_tags_batch[idx]
                IF eid NOT IN funnel_stat.entry_stat_map:
                    funnel_stat.entry_stat_map[eid] = {
                        total_hit: 0,
                        hit_24h: 0,
                        hit_7d: 0,
                        last_hit_ts: None,
                        first_hit_ts: NOW(),
                        tag_hit_dist: {t:0 for t in tag_list}
                    }
            CONTINUE

        // 3. 接收分funnel查询命中事件，更新条目+标签热度计数
        IF 收到 ag-mem-22 分funnel查询命中条目事件列表:
            IF internal_state == SYSTEM_PAUSED:
                CONTINUE
            SET internal_state = STATE_UPDATE
            current_ts = NOW()

            FOR hit_item IN 请求.命中条目列表:
                eid = hit_item.entry_id
                fid = hit_item.funnel_id
                hit_tags = hit_item.hash_tag_list
                idx_bucket = hit_item.index_bucket_id

                // 校验漏斗合法性，不存在则初始化分区
                IF fid NOT IN funnel_heat_root:
                    funnel_heat_root[fid] = {
                        index_bucket_id: idx_bucket,
                        entry_stat_map: {},
                        global_tag_agg: {}
                    }
                funnel_stat = funnel_heat_root[fid]

                // 条目无记录则自动初始化
                IF eid NOT IN funnel_stat.entry_stat_map:
                    funnel_stat.entry_stat_map[eid] = {
                        total_hit: 0,
                        hit_24h: 0,
                        hit_7d: 0,
                        last_hit_ts: None,
                        first_hit_ts: current_ts,
                        tag_hit_dist: {t:0 for t in hit_tags}
                    }
                entry_stat = funnel_stat.entry_stat_map[eid]

                // 更新条目维度命中计数
                entry_stat.total_hit += 1
                entry_stat.hit_24h += 1
                entry_stat.hit_7d += 1
                entry_stat.last_hit_ts = current_ts

                // 更新本条目标签分布计数
                FOR tag in hit_tags:
                    entry_stat.tag_hit_dist[tag] += 1
                    // 更新漏斗全局标签总热度
                    funnel_stat.global_tag_agg[tag] = funnel_stat.global_tag_agg.get(tag, 0) + 1

            SET internal_state = STATE_IDLE

        // 4. 响应分funnel批量热度查询请求
        IF 收到分funnel热度批量查询请求:
            SET internal_state = RESPONDING
            target_fid = 请求.target_funnel_id
            query_entry_list = 请求.entry_id_list
            stat_window = 请求.stat_window
            query_result = {
                funnel_id: target_fid,
                entry_hit_map: {},
                tag_hit_agg: {}
            }

            // 漏斗无统计分区，返回全0空数据
            IF target_fid NOT IN funnel_heat_root:
                FOR eid in query_entry_list:
                    query_result["entry_hit_map"][eid] = {
                        total_hit:0, hit_24h:0, hit_7d:0, last_hit_ts:None
                    }
                向请求方返回分funnel条目热度窗口统计包(query_result)
                SET internal_state = IDLE
                CONTINUE

            funnel_stat = funnel_heat_root[target_fid]
            query_result["tag_hit_agg"] = funnel_stat.global_tag_agg

            // 按窗口筛选返回计数
            FOR eid in query_entry_list:
                IF eid in funnel_stat.entry_stat_map:
                    raw = funnel_stat.entry_stat_map[eid]
                    if stat_window == "24h":
                        hit_data = {"total_hit":raw.hit_24h, "hit_window":raw.hit_24h, "last_hit_ts":raw.last_hit_ts}
                    elif stat_window == "7d":
                        hit_data = {"total_hit":raw.hit_7d, "hit_window":raw.hit_7d, "last_hit_ts":raw.last_hit_ts}
                    else:
                        hit_data = {"total_hit":raw.total_hit, "hit_window":raw.total_hit, "last_hit_ts":raw.last_hit_ts}
                    query_result["entry_hit_map"][eid] = hit_data
                ELSE:
                    query_result["entry_hit_map"][eid] = {"total_hit":0, "hit_window":0, "last_hit_ts":None}

            // 输出热度统计包至ag-mem-33/ag-mem-38
            向请求来源模块返回分funnel条目热度窗口统计包(query_result)
            SET internal_state = IDLE

        // 5. 每24小时定时清理已删除条目统计记录
        IF (NOW() - last_daily_clean_ts) >= 86400:
            // 拉取L2全漏斗有效条目快照
            valid_funnel_entry_map = ag-mem-22.query_all_l2_valid_entries()
            // 遍历本地所有funnel统计分区
            for fid in list(funnel_heat_root.keys()):
                local_entry_set = set(funnel_heat_root[fid]["entry_stat_map"].keys())
                valid_entry_set = set(valid_funnel_entry_map.get(fid, []))
                // 找出已失效条目，批量删除统计
                del_eids = local_entry_set - valid_entry_set
                for eid in del_eids:
                    del funnel_heat_root[fid]["entry_stat_map"][eid]
            last_daily_clean_ts = NOW()

        // 6. 每60秒周期性全漏斗热度汇总上报至ag-mem-03
        IF (NOW() - last_report_ts) >= 60:
            global_total_items = 0
            each_funnel_agg = {}
            for fid, stat in funnel_heat_root.items():
                entry_count = len(stat["entry_stat_map"])
                global_total_items += entry_count
                sum_24h = sum([v["hit_24h"] for v in stat["entry_stat_map"].values()])
                sum_7d = sum([v["hit_7d"] for v in stat["entry_stat_map"].values()])
                sum_all = sum([v["total_hit"] for v in stat["entry_stat_map"].values()])
                each_funnel_agg[fid] = {
                    stat_item_count: entry_count,
                    total_hit_24h: sum_24h,
                    total_hit_7d: sum_7d,
                    total_hit_all: sum_all
                }
            report_pkg = {
                current_state: internal_state,
                global_total_stat_item: global_total_items,
                each_funnel_agg: each_funnel_agg
            }
            向 ag-mem-03 发送全漏斗热度汇总状态上报(report_pkg)
            last_report_ts = NOW()

        SLEEP 20ms
```

## 约束与异常处理（V1.1新增funnel、哈希索引相关异常）
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 命中事件携带未注册funnel_id | 自动新建该funnel独立统计分区，正常初始化条目热度 | ag-mem-14完成漏斗注册同步至总控注册表 |
| 批量查询目标funnel不存在 | 返回该漏斗下所有条目命中计数全为0的空统计包 | 对应funnel产生L2写入事件初始化分区 |
| 单funnel热度统计内存占用超阈值 | LRU自动淘汰30天无任何命中的条目统计记录，释放内存 | 内存使用率回落至安全阈值 |
| 24小时清理周期拉取L2有效条目快照超时 | 跳过本次清理，保留全部存量热度统计，等待次日周期 | ag-mem-22存储服务恢复正常 |
| 全局系统熔断触发 | 停止所有新增条目初始化、命中计数更新；仅开放存量数据查询 | ag-mem-01下发恢复指令 |
| 命中条目hash_tag_list为空 | 正常更新条目总命中计数，不更新漏斗全局标签热度聚合 | 上层路由模块补全领域哈希标签后新写入条目 |

## 总线契约（废弃固定分槽编号，统一funnel/index_bucket/hash_tag传输）
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 分funnel L2新条目通知（funnel_id/index_bucket/hash_tag_list） | 只读 | ag-mem-22 发送 |
| 内部调度总线 | 读 | 分funnel查询命中条目事件列表（携带完整索引标签字段） | 只读 | ag-mem-22 实时推送 |
| 内部调度总线 | 读 | 分funnel热度批量查询请求（指定target_funnel_id、统计窗口） | 只读 | ag-mem-33 / ag-mem-38 发送 |
| 内部调度总线 | 读 | ag-mem-01全局熔断/恢复调度指令 | 只读 | 顶层总控下发管控信号 |
| 内部调度总线 | 写 | 分funnel条目热度窗口统计包（条目计数+漏斗标签聚合热度） | 专属写入 | 向 ag-mem-33、ag-mem-38 返回 |
| 内部调度总线 | 写 | 全漏斗热度汇总状态上报（各funnel聚合指标） | 周期性写入 | 向 ag-mem-03 漏斗二调度单元上报 |

## 安全边界（V1.1新增动态漏斗、哈希索引隔离约束）
| 规则编号 | 内容 |
|:---:|------|
| S-01 | 热度统计仅存储元数据（命中次数、时间戳、标签计数），全程不读取、缓存、持久化任何原始经验正文内容 |
| S-02 | 严格按funnel_id做数据物理隔离，禁止跨漏斗读取、聚合、修改热度统计数据，保证领域数据隔离 |
| S-03 | 所有统计数据仅驻留内存，无持久化落地；系统重启后全量热度统计清零，重新积累 |
| S-04 | 本模块仅被动接收ag-mem-22推送事件，禁止主动反向查询L2存储条目正文，仅定时拉取条目ID快照用于失效清理 |
| S-05 | 哈希标签热度仅用于漏斗内部检索权重参考，禁止将标签聚合数据直接对外输出至ECC上层认知模块 |
| S-06 | 全局熔断状态下禁止新增任何条目统计、更新命中计数，仅允许存量热度数据只读查询 |
| S-07 | 不存在于ag-mem-01全局注册表的funnel_id仅做临时统计，上报时标记异常漏斗ID用于告警监控 |

## 接口校验用例（适配funnel分桶、哈希标签热度统计）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M23-01 | `IDLE`，funnel=F003分区未初始化 | 分funnel新条目通知（3条，hash_tag=["Java","多线程"]） | 新建F003独立统计分区，3条条目初始化命中计数全0，绑定对应index_bucket |
| TC-M23-02 | `IDLE`，funnel=F005存在存量条目 | 5条命中事件，携带hash_tag=["接口自动化"] | 5条条目总命中/24h/7d计数+1，同步累加F005全局标签热度 |
| TC-M23-03 | `IDLE`，查询请求指定funnel=F002，窗口=7d | 批量条目ID热度查询 | 返回F002专属统计包，包含条目7天命中计数+该漏斗全部标签聚合热度 |
| TC-M23-04 | `IDLE`，命中事件携带未注册funnel=F999 | 未知funnel命中条目列表 | 自动新建F999统计分区，正常更新命中计数，上报时标记异常漏斗 |
| TC-M23-05 | `IDLE`，24小时定时清理触发，F001内2条条目已从L2删除 | 拉取L2有效条目快照 | 从F001 entry_stat_map中移除2条失效条目全部统计记录 |
| TC-M23-06 | `SYSTEM_PAUSED`，收到新条目通知/命中事件 | 任意funnel热度更新事件 | 直接丢弃更新请求，存量数据可正常查询 |
| TC-M23-07 | `NORMAL`，命中条目hash_tag_list为空 | 单条无标签命中事件 | 条目命中计数正常累加，不更新漏斗全局标签聚合热度 |

## 质量自检清单（V1.1完整达标）
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、五层存储分区归属不变，彻底移除V1.0固定分槽编号逻辑 | ✅ |
| 新增依赖ag-mem-01总控F0，用于接收全局熔断、校验漏斗合法性 | ✅ |
| 原有4种运行状态完整保留，所有统计逻辑按funnel分桶隔离改造 | ✅ |
| 输入输出全部携带funnel_id、index_bucket_id、hash_tag_list分桶索引字段 | ✅ |
| 重构统计存储结构，顶层按funnel分区，内置条目计数+标签全局热度双层统计 | ✅ |
| 伪代码完整实现新条目初始化、命中计数更新、标签热度聚合、批量查询、每日失效清理、周期上报全流程 | ✅ |
| 异常处理覆盖未知funnel、内存淘汰、快照拉取超时、熔断、空标签条目等新增场景 | ✅ |
| 总线契约全部移除旧分槽传输字段，统一funnel/索引桶/哈希标签传输规范 | ✅ |
| 安全边界新增funnel物理隔离、标签数据隔离、熔断只读约束 | ✅ |
| 校验用例覆盖分区初始化、命中计数更新、分funnel查询、未知漏斗、定时清理、熔断、空标签全场景 | ✅ |
| 完全对齐V1.1动态子漏斗、分桶哈希索引MLNF-Mem五层统一架构标准 | ✅ |

---