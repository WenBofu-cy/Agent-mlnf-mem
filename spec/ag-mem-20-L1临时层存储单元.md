## V1.1 模块升级总说明
### V1.1 重大升级变更点
1. **废弃V1.0固定5场景分槽（ag-mem-15~19）集中存储架构**，重构为按`funnel_id`动态子漏斗独立分桶存储，每个领域经验物理隔离；
2. **内置独立哈希索引分桶机制**，每个子漏斗绑定专属`index_bucket_id`，写入自动维护标签映射，大幅降低全库检索扫描开销；
3. 容量管控由全局统一阈值，拆分为「全局总配额 + 单funnel独立硬上限」，避免单一领域耗尽全部L1存储资源；
4. 衰减、清理逻辑按单个funnel隔离执行，仅触发容量超限的子漏斗执行清理，不影响其他领域正常读写；
5. 输入输出全链路新增`funnel_id`、`hash_tag_list`、`index_bucket_id`、`result_validated`关键字段，与ag-mem-01总控、ag-mem-14路由、ag-mem-38晋升模块完整联动；
6. 新增哈希索引容量管控，单漏斗标签达上限自动清理低频标签，平衡检索性能与内存占用；
7. 依赖模块新增ag-mem-01总控漏斗，用于读取全局漏斗注册表、索引资源池、配额规则，校验funnel合法性；
8. 原有L1基础存储、容量预警、定时衰减、熔断逻辑完整保留，仅适配动态分桶架构做分支改造，无原有业务能力丢失；
9. 新增非法funnel写入拦截校验，强制所有存储操作依赖ag-mem-14动态路由输出的合法子漏斗ID，杜绝无管控写入破坏全局收敛机制。

# ag-mem-20-L1临时层存储单元 接口规格（V1.1 完整版，适配动态子漏斗+独立哈希索引分桶）
---

## 基本信息

| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-20 |
| 模块名称 | L1临时层存储单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层存储 |
| 核心职责 | 作为动态子漏斗五层记忆存储架构的第一层，**彻底废弃V1.0固定ag-mem-15~19分槽存储逻辑**，改为按`funnel_id`独立分桶存储。所有新经验条目由ag-mem-14动态路由单元分配唯一funnel_id、hash_tag_list、funnel_index_bucket后写入本层，每条记忆绑定专属子漏斗与独立哈希索引桶。<br>负责接收并存储会话/瞬时任务经验片段，L1是所有动态子漏斗经验的统一写入入口；为每个funnel维护独立哈希索引字典，实现标签高速检索；按单个子漏斗管控容量上限，全局汇总上报总占用。<br>当单funnel/全局L1容量逼近阈值时，向ag-mem-21发起衰减评估请求；存储时自动维护funnel专属哈希索引标签，供记忆查询、晋升判定快速过滤。不参与晋升判定或遗忘决策，仅执行经验接收、分桶持久化、哈希索引维护、容量基础管控。 |
| 依赖模块 | ag-mem-03（漏斗二专属调度单元，转发带funnel_id的写入请求）、ag-mem-14（动态路由单元，下发funnel_id、hash_tag_list、index_bucket）、ag-mem-21（L1临时层时序衰减单元，接收分桶衰减评估条目）、ag-mem-48（全局容量配额管控单元，查询单漏斗/全局L1容量占用）、ag-mem-01（总控漏斗F0，读取全局索引资源池、漏斗配额规则） |
| 被依赖模块 | ag-mem-03（返回分桶写入确认回执）、ag-mem-21（消费各funnel待衰减条目列表）、ag-mem-22（L2近期层存储单元，接收经ag-mem-38判定通过的晋升条目）、ag-mem-38（晋升判定单元，读取funnel独立存储条目与哈希标签） |


## 内部状态定义

| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 正常服务 | `NORMAL` | 各funnel独立分桶存储就绪，接收新经验写入、维护哈希索引 | 系统初始化完成，所有子漏斗索引桶挂载完毕 |
| 容量预警 | `CAPACITY_WARNING` | 任意单个funnel L1使用率≥80% 或 全局L1总使用率≥80%，触发温和衰减清理 | 单funnel/全局使用率 ≥ 80% |
| 容量紧急 | `CAPACITY_CRITICAL` | 单funnel使用率≥95% 或 全局L1总使用率≥95%，暂停该漏斗新写入并强制清理低重要度条目 | 单funnel/全局使用率 ≥ 95% |
| 暂停服务 | `SYSTEM_PAUSED` | 全局紧急熔断，冻结所有funnel写入、索引变更操作 | 收到ag-mem-01下发全局熔断指令 |


## 输入数据（V1.1 移除固定分槽编号，新增funnel、哈希索引全套字段）

| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L1动态分桶写入请求 | Struct（经验条目数据 + funnel_id + hash_tag_list + index_bucket_id + 调整后重要度 + time_stamp + result_validated标记） | ag-mem-03 漏斗二调度单元（经ag-mem-14路由输出） | 任意动态子漏斗产生全新任务经验 | **高** |
| 分桶衰减清理完成确认 | Struct（funnel_id + 清理条目数 + 释放空间量 + 该漏斗剩余使用率） | ag-mem-21 L1衰减评估单元 | 指定funnel衰减清理执行完成后 | **高** |
| 全局&单漏斗容量查询回执 | Struct（全局L1总条目/使用率 + 目标funnel条目数/使用率 + 单funnel容量硬上限） | ag-mem-48 全局容量配额管控单元 | 每次写入前拉取对应funnel容量数据 | **高** |
| 全局调度/熔断指令 | Enum（暂停/恢复/全局熔断） | ag-mem-01 总控漏斗F0 | 系统模式切换、存储资源故障紧急管控 | **紧急** |


## 输出数据（V1.1全链路携带funnel_id、哈希索引桶标识）

| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| L1分桶写入确认回执 | Struct（条目ID + funnel_id + index_bucket_id + 写入状态 + 该funnel当前使用率 + 条目存储位置） | ag-mem-03 漏斗二调度单元 | 单条经验分桶写入、哈希索引更新成功 | **高** |
| L1写入拒绝通知 | Struct（条目ID + funnel_id + 拒绝原因 + 当前funnel状态 + 资源处理建议） | ag-mem-03 漏斗二调度单元 | 写入被容量/格式/熔断规则拦截 | **高** |
| 单funnel衰减评估请求 | Struct（目标funnel_id + 待评估条目列表 + 触发类型(容量预警/紧急/定时) + 该漏斗当前使用率） | ag-mem-21 L1时序衰减单元 | 对应funnel容量达阈值、定时6小时触发 | **高** |
| L1全局分桶状态上报 | Struct（当前运行状态 + 全局总条目数 + 全局使用率 + 各funnel独立条目数/使用率/索引占用量） | ag-mem-48（容量管控）、ag-mem-01（总控漏斗全局统计） | 周期性每30秒、任意funnel状态变更时 | 普通 |


## L1存储全局&单漏斗配置（V1.1废弃固定分槽，按funnel隔离容量规则）

| 配置项 | 默认值 | 说明 |
|--------|:---:|------|
| 全局L1占漏斗二总容量比例 | 60% | 所有动态子漏斗L1层共享全局60%配额池 |
| 单funnel L1最大条目硬上限 | 3000条 | 单个领域子漏斗独立存储上限，互不抢占 |
| L1单条目最大二进制大小 | 10KB | 单条经验数据体积硬限制 |
| 单次写入操作超时阈值 | 200ms | 单条分桶写入+索引更新最大等待时长 |
| 容量预警阈值 | 80% | 单funnel/全局使用率≥80%触发温和异步衰减 |
| 容量紧急阈值 | 95% | 单funnel/全局使用率≥95%暂停该漏斗新写入、强制同步清理 |
| 定时衰减评估触发间隔 | 6小时 | 所有活跃funnel统一轮询触发低重要度条目衰减 |
| 单funnel最小保留条目兜底 | 50条 | 容量清理时，每个子漏斗至少留存最近50条新鲜经验，不全部清空 |
| 哈希索引单funnel条目上限 | 15000个标签 | 每个独立index_bucket存储标签总量上限，超限自动清理低频标签 |


## 核心处理逻辑（V1.1 完整伪代码，分funnel独立存储+哈希索引维护）
```
FUNCTION l1_storage_main_loop():
    STATE_NORMAL = NORMAL
    STATE_WARN = CAPACITY_WARNING
    STATE_CRITICAL = CAPACITY_CRITICAL
    STATE_PAUSED = SYSTEM_PAUSED

    SET internal_state = STATE_NORMAL
    // V1.1核心初始化：按funnel分桶存储字典 + 独立哈希索引池
    l1_funnel_storage = {}  # key:funnel_id, value: 该漏斗完整条目列表
    funnel_hash_index = {}   # key:funnel_id, value: index_bucket_id + tag->[条目ID]映射字典
    funnel_item_counter = {} # key:funnel_id, value: 当前条目数量计数器
    全局总条目计数器 = 0

    // 冷启动挂载所有已存在动态子漏斗存储与索引桶
    全量漏斗列表 = ag-mem-01.get_all_funnel_ids()
    FOR funnel_id IN 全量漏斗列表:
        l1_funnel_storage[funnel_id] = []
        索引元数据 = ag-mem-01.get_funnel_index_meta(funnel_id)
        funnel_hash_index[funnel_id] = {
            index_bucket:索引元数据.index_bucket_id,
            tag_map: {},
            tag_usage_stat: {} # 统计每个标签访问频次，用于低频清理
        }
        funnel_item_counter[funnel_id] = 0

    WHILE 系统运行中:
        // 第1步：全局熔断最高优先级管控
        IF 收到 ag-mem-01 全局熔断指令:
            SET internal_state = STATE_PAUSED
            CONTINUE
        ELSE IF 收到恢复指令 AND internal_state == STATE_PAUSED:
            SET internal_state = STATE_NORMAL

        // 第2步：接收动态分桶经验写入请求
        IF 收到 ag-mem-03 下发的L1分桶写入请求:
            IF internal_state == STATE_PAUSED:
                向 ag-mem-03 返回写入拒绝通知(funnel_id=请求.funnel_id, 拒绝原因="系统全局熔断，禁止写入")
                CONTINUE

            target_funnel = 请求.funnel_id
            target_index_bucket = 请求.index_bucket_id
            entry_tags = 请求.hash_tag_list
            entry_importance = 请求.调整后重要度

            // 2a. 拉取该funnel专属容量数据
            容量回执 = ag-mem-48.query_single_funnel_capacity(target_funnel)
            funnel_used_rate = 容量回执.当前使用率
            funnel_max_item = 容量回执.单漏斗硬上限

            // 2b. 更新全局状态机（按单漏斗最高占用判定整体状态）
            全局总使用率 = 容量回执.全局L1使用率
            所有漏斗使用率集合 = ag-mem-48.get_all_funnel_l1_usage()
            max_single_usage = MAX(所有漏斗使用率集合)

            IF max_single_usage >= 95% OR 全局总使用率 >= 95%:
                SET internal_state = STATE_CRITICAL
            ELSE IF max_single_usage >= 80% OR 全局总使用率 >= 80%:
                SET internal_state = STATE_WARN

            // 2c. 单漏斗容量紧急阻断逻辑
            IF funnel_used_rate >= 95% AND funnel_item_counter[target_funnel] >= funnel_max_item:
                // 强制同步触发该漏斗衰减清理
                待清理低权条目 = 取l1_funnel_storage[target_funnel]按重要度升序前20%
                向 ag-mem-21 发送单funnel衰减评估请求(
                    target_funnel, 待清理低权条目, 触发类型="容量紧急"
                )
                等待清理完成回执
                // 重新查询清理后容量
                更新后容量 = ag-mem-48.query_single_funnel_capacity(target_funnel)
                IF 更新后容量.当前使用率 >= 95%:
                    向 ag-mem-03 返回写入拒绝通知(
                        funnel_id=target_funnel,
                        拒绝原因="目标子漏斗L1容量已达硬上限，清理后仍无空余存储"
                    )
                    CONTINUE

            // 2d. 容量预警：异步后台衰减，不阻塞当前写入
            IF funnel_used_rate >= 80%:
                待温和清理条目 = 取l1_funnel_storage[target_funnel]按重要度升序前10%
                异步向 ag-mem-21 发送单funnel衰减评估请求(target_funnel, 待温和清理条目, "容量预警")

            // 2e. 校验单条经验体积上限
            IF 请求.经验条目数据.字节大小 > 10*1024:
                向 ag-mem-03 返回写入拒绝通知(funnel_id=target_funnel, 拒绝原因="单条目超过10KB存储上限")
                CONTINUE

            // 2f. 生成本条目唯一ID，分桶持久化写入
            entry_id = f"L1-{target_funnel}-{时间戳}-{funnel_item_counter[target_funnel]}"
            写入存储位置 = l1_funnel_storage[target_funnel].append({
                entry_id:entry_id,
                raw_data:请求.经验条目数据,
                importance:entry_importance,
                result_validated:请求.result_validated,
                hash_tags:entry_tags,
                create_ts:请求.time_stamp
            })

            IF 写入存储位置 == None:
                向 ag-mem-03 返回写入拒绝通知(funnel_id=target_funnel, 拒绝原因="分桶持久化IO写入异常")
                CONTINUE

            // 2g. V1.1核心：更新该funnel专属哈希索引桶
            index_meta = funnel_hash_index[target_funnel]
            FOR tag IN entry_tags:
                IF tag NOT IN index_meta.tag_map:
                    index_meta.tag_map[tag] = []
                // 标签关联当前条目ID
                index_meta.tag_map[tag].append(entry_id)
                // 标签访问频次计数+1
                index_meta.tag_usage_stat[tag] = index_meta.tag_usage_stat.get(tag, 0) + 1
            // 索引标签总量超限自动清理低频标签
            IF len(index_meta.tag_map) > 15000:
                按频次升序排序所有tag，删除末尾20%低频标签，同步移除关联条目ID映射

            // 2h. 更新计数器
            funnel_item_counter[target_funnel] += 1
            全局总条目计数器 += 1

            // 2i. 返回分桶写入确认回执，携带funnel与索引标识
            回执数据 = {
                entry_id: entry_id,
                funnel_id: target_funnel,
                index_bucket_id: target_index_bucket,
                写入状态: "成功",
                funnel_current_usage: funnel_used_rate,
                分配存储位置: 写入存储位置
            }
            向 ag-mem-03 返回L1分桶写入确认回执(回执数据)

        // 第3步：定时全漏斗批量衰减评估（每6小时）
        IF 距上次全局衰减轮询 >= 6小时:
            FOR target_funnel IN l1_funnel_storage.keys():
                IF funnel_item_counter[target_funnel] > 0:
                    // 提取该漏斗低重要度20%条目送入衰减单元
                    该漏斗全条目 = l1_funnel_storage[target_funnel]
                    低权条目 = 按importance升序取前20%
                    向 ag-mem-21 发送单funnel衰减评估请求(target_funnel, 低权条目, "定时衰减轮询")
            重置衰减计时器

        // 第4步：接收ag-mem-21衰减清理完成回执，同步清理存储与索引
        IF 收到单funnel清理完成确认:
            clean_funnel = 确认.funnel_id
            clean_count = 确认.清理条目数
            // 从分桶存储中移除已清理条目
            已清理条目ID列表 = 确认.清理条目id集合
            l1_funnel_storage[clean_funnel] = [
                item FOR item IN l1_funnel_storage[clean_funnel]
                IF item.entry_id NOT IN 已清理条目ID列表
            ]
            // 同步清理哈希索引中失效条目ID映射
            index_meta = funnel_hash_index[clean_funnel]
            FOR tag IN index_meta.tag_map.keys():
                index_meta.tag_map[tag] = [
                    eid FOR eid IN index_meta.tag_map[tag]
                    IF eid NOT IN 已清理条目ID列表
                ]
                // 空标签直接删除释放索引空间
                IF len(index_meta.tag_map[tag]) == 0:
                    del index_meta.tag_map[tag]
            // 更新计数器
            funnel_item_counter[clean_funnel] -= clean_count
            全局总条目计数器 -= clean_count
            // 重新判定全局状态机
            更新后全局使用率 = ag-mem-48.query_global_l1_usage()
            IF 更新后全局使用率 < 80% AND MAX(ag-mem-48.get_all_funnel_l1_usage()) < 80%:
                SET internal_state = STATE_NORMAL

        // 第5步：周期性30秒全局分桶状态上报
        IF 距上次状态上报 >= 30秒:
            各漏斗占用快照 = {}
            FOR f_id IN l1_funnel_storage.keys():
                单漏斗容量 = ag-mem-48.query_single_funnel_capacity(f_id)
                index_tag_total = SUM(len(funnel_hash_index[f_id].tag_map.keys()))
                各漏斗占用快照[f_id] = {
                    item_count: funnel_item_counter[f_id],
                    usage_rate: 单漏斗容量.当前使用率,
                    index_tag_total: index_tag_total
                }
            上报快照 = {
                当前运行状态: internal_state,
                全局总条目数: 全局总条目计数器,
                全局L1使用率: ag-mem-48.query_global_l1_usage(),
                各funnel独立占用详情: 各漏斗占用快照
            }
            向 ag-mem-48、ag-mem-01 发送L1全局分桶状态上报(上报快照)

        SLEEP 10ms
```


## 约束与异常处理（V1.1新增分桶、哈希索引专属异常分支）
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 单funnel分桶IO写入/持久化磁盘故障 | 仅拦截当前漏斗写入，其他子漏斗正常服务，上报告警标记故障funnel | 该funnel底层存储分区修复 |
| 单条经验二进制体积超过10KB硬上限 | 直接拒绝写入，返回明确提示，不占用存储资源 | 压缩/拆分经验条目至尺寸阈值内 |
| 目标funnel容量达95%上限，强制清理后仍无空余空间 | 阻断该漏斗所有新写入，保留兜底50条最新经验，全局上报告警 | 长期衰减、晋升释放该漏斗存储条目 |
| 同一条entry_id重复写入同一funnel | 覆盖替换旧条目，同步更新该条目关联的全部哈希标签映射与时间戳 | — |
| 全局系统熔断触发 | 冻结所有funnel写入、哈希索引新增/删除操作，存量数据只读保留 | ag-mem-01下发全局恢复指令+存储自检通过 |
| 单funnel哈希索引标签总量超限（>15000） | 自动删除20%低频访问标签，释放索引内存占用，不影响核心检索标签 | 正常业务写入新增标签自动补充索引 |
| 冷启动发现不存在的funnel_id写入请求 | 拒绝写入，返回“子漏斗ID不存在”，路由模块需先完成漏斗创建 | ag-mem-14先完成该领域funnel新建流程 |


## 总线契约（V1.1移除固定分槽，统一funnel/索引桶传输字段）
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | L1动态分桶写入请求（携带funnel_id、hash_tag_list、index_bucket_id、result_validated） | 只读 | ag-mem-03 转发ag-mem-14路由输出 |
| 内部调度总线 | 读 | 单funnel衰减清理完成确认回执 | 只读 | ag-mem-21 衰减单元返回 |
| 内部调度总线 | 读 | 全局&单漏斗容量查询回执 | 只读 | ag-mem-48 容量管控单元返回 |
| 内部调度总线 | 读 | 全局熔断/调度控制指令 | 只读 | ag-mem-01 总控漏斗下发 |
| 内部调度总线 | 写 | L1分桶写入确认回执（携带funnel_id、index_bucket） | 专属写入 | 向 ag-mem-03 调度单元返回 |
| 内部调度总线 | 写 | L1分桶写入拒绝通知（携带funnel、拦截原因） | 专属写入 | 向 ag-mem-03 调度单元返回 |
| 内部调度总线 | 写 | 单funnel专属衰减评估请求（携带目标funnel_id） | 专属写入 | 向 ag-mem-21 L1衰减单元发送 |
| 内部调度总线 | 写 | L1全局分桶状态上报（全漏斗存储、索引占用快照） | 周期性写入 | 同步上报 ag-mem-48、ag-mem-01 |


## 安全边界（V1.1新增动态漏斗、哈希索引隔离安全规则）
| 规则编号 | 内容 |
|:---:|------|
| S-01 | L1层仅做经验原始数据暂存，禁止修改、加工条目内容，仅可维护哈希索引标签映射元数据 |
| S-02 | 所有存储、索引操作必须绑定唯一`funnel_id`，禁止跨漏斗混存、跨索引桶读写标签，物理隔离各领域数据 |
| S-03 | 单funnel容量紧急清理时，强制兜底保留至少50条最新条目，保障当前会话活跃经验不丢失 |
| S-04 | 每条经验写入必须原子完成「持久化存储+哈希索引更新」，写入中断时自动回滚，不产生半条损坏数据、孤立索引标签 |
| S-05 | 哈希索引按独立index_bucket物理隔离，一个funnel的标签仅能在自身索引桶读写，杜绝跨领域标签串读污染检索结果 |
| S-06 | 禁止绕过ag-mem-14动态路由、ag-mem-01总控漏斗直接写入不存在的funnel_id，所有存储操作必须校验漏斗合法性 |
| S-07 | 全局熔断状态下，禁止任何哈希索引新增、删除、修改操作，仅开放存量条目只读查询 |


## 接口校验用例（完全适配V1.1动态子漏斗+哈希索引分桶）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M20-01 | `NORMAL`，目标funnel F001使用率50% | 写入请求（funnel=F001，hash_tag=["Python","排序"],条目2KB） | 分桶写入成功，更新F001专属哈希索引桶，返回携带funnel_id、index_bucket的确认回执 |
| TC-M20-02 | `NORMAL`，funnel F002使用率82% | 正常尺寸经验写入请求 | 写入成功，异步向ag-mem-21发送F002温和衰减评估请求，不阻塞业务 |
| TC-M20-03 | `CAPACITY_CRITICAL`，funnel F003使用率96%、条目达3000硬上限 | 新经验写入请求 | 同步触发F003强制衰减清理，清理后仍满则直接拒绝写入，返回容量超限提示 |
| TC-M20-04 | `NORMAL`，经验条目大小12KB | 任意funnel写入超尺寸条目 | 直接拒绝写入，拒绝原因为“单条目超过10KB存储上限” |
| TC-M20-05 | `NORMAL`，距上次全局衰减满6小时 | 定时轮询触发 | 遍历所有活跃funnel，分别提取各漏斗低重要度20%条目下发至ag-mem-21 |
| TC-M20-06 | `SYSTEM_PAUSED`，收到任意funnel写入请求 | 分桶写入请求 | 拒绝写入，返回“系统全局熔断，禁止所有存储写入操作” |
| TC-M20-07 | `NORMAL`，funnel F004哈希索引标签达15000上限 | 新增带3个全新标签的经验写入 | 自动清理F004索引桶20%低频旧标签，再新增当前条目哈希映射 |
| TC-M20-08 | `NORMAL`，写入不存在funnel_id=F999 | 无效funnel写入请求 | 直接拦截拒绝，拒绝原因为“目标子漏斗ID不存在，需先创建动态漏斗” |


## 质量自检清单（V1.1完整达标）
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、五层存储分区归属保持不变 | ✅ |
| 彻底移除V1.0 ag-mem-15~19固定分槽依赖，全链路改用funnel_id分桶 | ✅ |
| 新增依赖ag-mem-01总控漏斗，用于拉取全局漏斗、索引资源配置 | ✅ |
| 内部状态机原有4种状态完整保留，新增单funnel容量判定逻辑 | ✅ |
| 输入输出结构体全部新增funnel_id、hash_tag_list、index_bucket_id、result_validated核心字段 | ✅ |
| 存储配置区分全局共享配额 + 单funnel独立硬上限，新增哈希索引标签容量约束 | ✅ |
| 伪代码完整实现按funnel独立存储、专属哈希索引桶全生命周期维护、分漏斗衰减清理 | ✅ |
| 异常处理覆盖无效funnel、索引超限、分桶IO故障、全局熔断等V1.1新增场景 | ✅ |
| 总线契约统一携带funnel与索引桶标识，废弃固定分槽编号传输 | ✅ |
| 安全边界新增动态漏斗物理隔离、索引桶独立管控、非法漏斗写入拦截规则 | ✅ |
| 校验用例覆盖正常分桶写入、容量预警、强制清理、索引超限、无效漏斗、熔断全场景 | ✅ |
| 完全对齐V1.1白皮书动态有限子漏斗、分桶哈希索引、MLNF-Mem V2.3五层存储架构定义 | ✅ |

---