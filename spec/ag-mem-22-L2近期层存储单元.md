## V1.1 模块升级总说明
### V1.1 重大升级变更点
1. 废弃V1.0五场景静态分槽存储架构，重构为`funnel_id`动态子漏斗独立分桶存储，各领域经验物理隔离，互不抢占资源；
2. 内置每个子漏斗专属独立哈希索引桶，写入自动维护标签映射，清除同步回收失效索引，大幅降低全库检索扫描开销；
3. 容量管控由单一全局阈值，升级为「全局配额池 + 单funnel独立硬上限」双约束，单一领域不会耗尽全部L2存储空间；
4. 7天超期条目分流逻辑按funnel专属L2→L3阈值判定，抛弃统一静态阈值表，由ag-mem-01动态下发各领域自定义阈值；
5. 全链路输入输出统一新增`funnel_id`、`hash_tag_list`、`index_bucket_id`、`result_validated`，与ag-mem-01/14/21/38全模块字段互通兼容；
6. 新增哈希索引容量自清理机制，单漏斗标签达上限自动淘汰低频标签，平衡检索性能与内存占用；
7. 定时扫描、容量清理逻辑完全按单个funnel隔离执行，单一漏斗资源拥堵不影响其他领域正常读写；
8. 原有L2核心业务能力（晋升写入、查询、7天超期分流、容量预警）完整保留，仅适配动态漏斗+索引体系扩展分支，无原有逻辑丢失；
9. 增加非法funnel拦截校验，所有存储操作依赖总控漏斗合法注册表，杜绝游离漏斗破坏系统宏观自收敛机制。

# ag-mem-22-L2近期层存储单元 接口规格（V1.1 版，适配动态funnel分桶+独立哈希索引）
---
## 基本信息
| 项 | 内容 |
|----|------|
| 模块编号 | ag-mem-22 |
| 模块名称 | L2近期层存储单元 |
| 所属分区 | 三、漏斗二：任务经验漏斗 / 五层存储 |
| 核心职责 | 五层存储第二层，存储L1晋升、7日内活跃动态子漏斗经验，全局占漏斗二总容量25%。**废弃V1.0固定ag-mem-15~19分槽，全链路以funnel_id分桶隔离存储**，每条条目绑定专属`index_bucket_id`与`hash_tag_list`，内置单漏斗独立哈希索引字典，支撑标签高速检索。<br>接收ag-mem-21分funnel晋升条目，维护单funnel独立容量上限与7天生命周期；定时扫描超期条目，高I值条目推送至ag-mem-24（L3）晋升，低价值条目移交ag-mem-42清除，同步归集失效哈希标签回写索引清理指令。<br>对外支持按funnel精准检索，向ag-mem-23推送新条目用于热度统计；仅负责分桶持久化、哈希索引维护、容量管控，不参与晋升/遗忘判定逻辑。 |
| 依赖模块 | ag-mem-01（总控F0，拉取全局funnel注册表、索引资源池、funnel专属L2→L3阈值）、ag-mem-21（L1衰减单元，下发分funnel晋升条目，携带哈希索引全套字段）、ag-mem-23（L2热度统计，接收新增条目元数据）、ag-mem-24（L3中期存储，接收L2超期高价值晋升条目）、ag-mem-42（冗余删除归档，接收低价值/超期待清除条目）、ag-mem-48（全局容量管控，查询单funnel/全局L2容量占用） |
| 被依赖模块 | ag-mem-21（返回分funnel写入确认回执）、ag-mem-23（接收新增funnel条目通知）、ag-mem-03（漏斗二调度单元，转发带funnel过滤的经验查询）、ag-mem-38（晋升判定单元，读取funnel条目与哈希标签） |

## 内部状态定义
| 状态 | 标识 | 含义 | 触发条件 |
|------|------|------|----------|
| 正常服务 | `NORMAL` | 各funnel分桶存储、哈希索引读写就绪，接收晋升写入与查询 | 初始化完成，所有子漏斗索引桶挂载完成 |
| 容量预警 | `CAPACITY_WARNING` | 任意单funnel使用率≥80% 或 全局L2总使用率≥80%，异步温和清理过期低价值条目 | 单漏斗/全局占用达80%阈值 |
| 容量紧急 | `CAPACITY_CRITICAL` | 单funnel≥95%或全局≥95%，暂停该漏斗新写入，强制同步清理过期+低重要度条目 | 单漏斗/全局占用达95%阈值 |
| 暂停服务 | `SYSTEM_PAUSED` | ag-mem-01下发全局熔断，冻结所有写入、索引变更操作，仅开放只读查询 | 全局紧急熔断指令 |

## 输入数据（V1.1 删除来源分槽编号，新增funnel、索引、result标记）
| 输入项 | 数据类型 | 来源模块 | 触发条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 单funnel L2晋升条目列表 | List[Struct]（entry_id + funnel_id + 经验原始数据 + I重要度 + hash_tag_list + index_bucket_id + result_validated + l1_create_ts + promote_ts） | ag-mem-21 L1时序衰减单元 | L1评估通过，分漏斗批量推送晋升数据 | **高** |
| L2分funnel查询请求 | Struct（查询条件 + 目标funnel_id过滤 + 最大返回条数） | ag-mem-03 漏斗二调度单元 | 业务检索指定领域子漏斗近期经验 | **高** |
| L2清理完成回执 | Struct（funnel_id + 清理条目数 + 释放存储空间 + 剩余漏斗使用率） | ag-mem-42 冗余删除单元 | 单漏斗批量条目清理完毕 | **高** |
| 单漏斗/全局L2容量查询回执 | Struct（全局总条目/使用率 + 目标funnel条目数/使用率 + 单funnel硬上限） | ag-mem-48 全局容量配额单元 | 每条晋升写入前拉取容量数据 | **高** |
| 全局调度/熔断指令 | Enum（暂停/恢复/全局熔断） | ag-mem-01 总控漏斗F0 | 系统模式切换、存储资源故障管控 | **紧急** |

## 输出数据（全链路携带funnel与哈希索引标识）
| 输出项 | 数据类型 | 目标模块 | 输出条件 | 优先级 |
|--------|----------|----------|----------|:---:|
| 分funnel写入确认回执 | Struct（funnel_id + 接收条目总数 + 成功写入条数 + 当前漏斗使用率 + index_bucket_id） | ag-mem-21 L1衰减单元 | 单漏斗晋升条目全部处理完成 | **高** |
| L2分桶查询结果集 | Struct（匹配条目列表，每条携带entry_id/funnel_id/hash_tag_list/I值/最近访问时间） | ag-mem-03 漏斗二调度单元 | 检索请求处理完毕 | **高** |
| 新增funnel条目通知 | Struct（funnel_id + 新增entry_id集合 + index_bucket_id） | ag-mem-23 L2热度统计单元 | 存在成功写入的晋升条目 | 普通 |
| L2超期晋升至L3条目清单 | List[Struct]（entry_id/funnel_id/hash_tag_list/index_bucket_id/I值/result_validated） | ag-mem-24 L3中期存储单元 | 定时扫描出7天超期且I≥funnel L2→L3阈值条目 | **高** |
| 待清除条目+失效哈希标签包 | Struct（funnel_id + clean_entry_ids + merged_clean_tags） | ag-mem-42 删除单元；同步回写本层索引清理指令 | 超期低价值/容量清理条目 | **高** |
| L2全局分桶状态上报 | Struct（全局总条目/使用率 + 各funnel独立条目数/使用率/索引标签总量） | ag-mem-48、ag-mem-01总控F0 | 每30秒周期性上报、状态变更时 | 普通 |

## L2存储全局&单漏斗配置（V1.1 分全局池+单漏斗独立配额）
| 配置项 | 默认值 | 说明 |
|--------|:---:|------|
| L2占用漏斗二总存储比例 | 25% | 所有动态funnel共享全局25%配额池 |
| 单funnel L2条目硬上限 | 1500条 | 单个领域漏斗独立存储上限，互不抢占资源 |
| L2条目最大留存生命周期 | 7天（168小时） | 写入L2满7天必须执行晋升或清除 |
| 单条目最大二进制体积 | 15KB | 超过尺寸直接跳过，条目留存L1不晋升 |
| 单次写入操作超时阈值 | 200ms | 分桶持久化+索引更新最大等待时长 |
| 容量预警阈值 | 80% | 单漏斗/全局占用≥80%触发异步温和清理 |
| 容量紧急阈值 | 95% | 单漏斗/全局占用≥95%暂停写入、强制同步清理 |
| 定时超期扫描间隔 | 1小时 | 全活跃funnel轮询7天超期条目 |
| 单funnel哈希索引标签上限 | 12000个 | 独立index_bucket标签总量超限自动清理低频标签 |
| L2→L3通用基准I阈值 | 0.62 | 新建无自定义阈值funnel默认使用该值 |

## 核心配套规则
### 1. 分桶哈希索引维护规则
1. 每个funnel绑定唯一`index_bucket_id`，索引物理隔离，禁止跨漏斗共享标签映射；
2. 条目写入同步将`hash_tag_list`全部写入对应索引桶，建立tag→[entry_id]映射，统计标签访问频次；
3. 条目被清除时自动归集全部关联标签，合并去重生成清理包，同步删除索引桶内失效entry_id映射；
4. 单漏斗索引标签达12000上限，自动删除20%低频访问标签释放索引空间。

### 2. 7天超期条目分流规则（按funnel专属阈值判定）
1. 条目在L2留存≥168小时：
   - I ≥ funnel专属L2→L3阈值：打包晋升至ag-mem-24 L3层；
   - I ＜ funnel专属L2→L3阈值：移交ag-mem-42永久清除，同步清理哈希索引。
2. 容量紧急/预警清理优先筛选「超7天+低I值」条目，无超期条目时再清理存量最低重要度条目。

### 3. 容量管控隔离规则
清理、写入、查询逻辑完全按funnel隔离，单一漏斗容量拥堵不会阻塞其他领域funnel正常读写。

## 核心处理逻辑（V1.1 伪代码，funnel分桶+哈希索引一体化）
```
FUNCTION l2_storage_main_loop():
    STATE_NORMAL = NORMAL
    STATE_WARN = CAPACITY_WARNING
    STATE_CRITICAL = CAPACITY_CRITICAL
    STATE_PAUSED = SYSTEM_PAUSED

    SET internal_state = STATE_NORMAL
    // 初始化分桶存储、独立哈希索引池
    l2_funnel_store = {}        # key:funnel_id, value:该漏斗完整条目集合
    funnel_hash_index = {}       # key:funnel_id, value:{index_bucket, tag_map, tag_usage_stat}
    funnel_item_counter = {}     # key:funnel_id, 条目计数
    global_total_count = 0

    // 冷启动加载全局漏斗列表，初始化空存储与索引桶
    all_funnel_ids = ag-mem-01.get_all_funnel_ids()
    for fid in all_funnel_ids:
        l2_funnel_store[fid] = []
        index_meta = ag-mem-01.get_funnel_index_meta(fid)
        funnel_hash_index[fid] = {
            index_bucket: index_meta.index_bucket_id,
            tag_map: {},
            tag_usage_stat: {}
        }
        funnel_item_counter[fid] = 0

    // 拉取所有funnel L2→L3晋升阈值快照
    funnel_l2_thresh_map = ag-mem-01.get_all_funnel_l2_promote_threshold()

    WHILE 系统运行中:
        // 1. 全局熔断最高优先级管控
        IF 收到 ag-mem-01 全局熔断指令:
            SET internal_state = STATE_PAUSED
            CONTINUE
        ELSE IF 收到恢复指令 AND internal_state == SYSTEM_PAUSED:
            SET internal_state = STATE_NORMAL

        // 2. 接收单funnel晋升写入请求
        IF 收到 ag-mem-21 下发单funnel晋升条目列表:
            target_fid = 请求.funnel_id
            entry_batch = 请求.晋升条目列表
            receive_total = len(entry_batch)
            success_write = 0
            new_entry_ids = []

            IF internal_state == SYSTEM_PAUSED:
                回执 = {funnel_id:target_fid, receive_total:receive_total, success_write:0, 拒绝原因:"全局熔断禁止写入"}
                向 ag-mem-21 返回分funnel写入确认回执(回执)
                CONTINUE

            // 拉取当前漏斗容量数据
            cap_resp = ag-mem-48.query_single_funnel_capacity(target_fid)
            funnel_usage = cap_resp.当前使用率
            funnel_max_item = cap_resp.单漏斗硬上限
            global_usage = ag-mem-48.query_global_l2_usage()

            // 更新全局状态机
            all_funnel_usage = ag-mem-48.get_all_funnel_l2_usage()
            max_single_use = MAX(all_funnel_usage)
            IF max_single_use >= 95 OR global_usage >= 95:
                SET internal_state = CAPACITY_CRITICAL
            ELSE IF max_single_use >= 80 OR global_usage >= 80:
                SET internal_state = CAPACITY_WARNING
            ELSE:
                SET internal_state = NORMAL

            // 容量紧急：同步强制清理该漏斗过期低价值条目
            IF internal_state == CAPACITY_CRITICAL AND funnel_usage >= 95:
                all_fid_entries = l2_funnel_store[target_fid]
                // 筛选超7天低I条目优先清理
                expire_low_val = [e for e in all_fid_entries if (NOW()-e.promote_ts)/3600 >= 168 and e.I值 < 0.2]
                if len(expire_low_val) > 0:
                    clean_pack = {funnel_id:target_fid, clean_entry_ids:[x.entry_id for x in expire_low_val], merged_clean_tags=set()}
                    for e in expire_low_val:
                        for tag in e.hash_tag_list:
                            clean_pack.merged_clean_tags.add(tag)
                    向 ag-mem-42 发送待清除条目+失效标签包(clean_pack)
                    等待清理完成回执
                    // 同步删除本地存储与索引
                    for del_e in expire_low_val:
                        l2_funnel_store[target_fid].remove(del_e)
                        funnel_item_counter[target_fid] -= 1
                        global_total_count -= 1
                        // 清理索引tag映射
                        idx_meta = funnel_hash_index[target_fid]
                        for t in del_e.hash_tag_list:
                            if del_e.entry_id in idx_meta.tag_map[t]:
                                idx_meta.tag_map[t].remove(del_e.entry_id)
                                if len(idx_meta.tag_map[t]) == 0:
                                    del idx_meta.tag_map[t]

                // 清理后再次校验容量，仍超限则拦截本次写入
                new_cap = ag-mem-48.query_single_funnel_capacity(target_fid)
                if new_cap.当前使用率 >= 95:
                    回执 = {funnel_id:target_fid, receive_total:receive_total, success_write:0, 拒绝原因:"漏斗容量硬上限，清理后无空余空间"}
                    向 ag-mem-21 返回写入确认回执(回执)
                    CONTINUE

            // 容量预警：异步后台清理，不阻塞写入流程
            IF internal_state == CAPACITY_WARNING:
                all_fid_entries = l2_funnel_store[target_fid]
                mild_clean = [e for e in all_fid_entries if (NOW()-e.promote_ts)/3600 >= 168 and e.I值 < 0.2]
                if len(mild_clean) > 0:
                    clean_pack = {funnel_id:target_fid, clean_entry_ids:[x.entry_id for x in mild_clean], merged_clean_tags=set()}
                    for e in mild_clean:
                        for tag in e.hash_tag_list:
                            clean_pack.merged_clean_tags.add(tag)
                    异步向 ag-mem-42 发送清理包(clean_pack)

            // 逐条校验并写入分桶存储+哈希索引
            for entry in entry_batch:
                // 条目尺寸校验
                if entry.经验原始数据.字节大小 > 15*1024:
                    continue
                // 持久化写入分桶
                l2_funnel_store[target_fid].append(entry)
                // 更新哈希索引桶
                idx_meta = funnel_hash_index[target_fid]
                for tag in entry.hash_tag_list:
                    if tag not in idx_meta.tag_map:
                        idx_meta.tag_map[tag] = []
                    idx_meta.tag_map[tag].append(entry.entry_id)
                    idx_meta.tag_usage_stat[tag] = idx_meta.tag_usage_stat.get(tag, 0) + 1
                // 索引标签总量超限，清理20%低频标签
                if len(idx_meta.tag_map.keys()) > 12000:
                    sorted_tags = sorted(idx_meta.tag_usage_stat.items(), key=lambda x:x[1])
                    del_count = int(len(sorted_tags)*0.2)
                    for t, _ in sorted_tags[:del_count]:
                        del idx_meta.tag_map[t]
                        del idx_meta.tag_usage_stat[t]
                success_write += 1
                new_entry_ids.append(entry.entry_id)
                funnel_item_counter[target_fid] += 1
                global_total_count += 1

            // 推送新条目通知至热度统计单元
            if len(new_entry_ids) > 0:
                notify_pkg = {
                    funnel_id: target_fid,
                    new_entry_ids: new_entry_ids,
                    index_bucket_id: funnel_hash_index[target_fid]["index_bucket"]
                }
                向 ag-mem-23 发送L2新条目通知(notify_pkg)

            // 返回写入确认回执
            resp_receipt = {
                funnel_id: target_fid,
                receive_entry_total: receive_total,
                success_write_count: success_write,
                funnel_current_usage: cap_resp.当前使用率,
                index_bucket_id: funnel_hash_index[target_fid]["index_bucket"]
            }
            向 ag-mem-21 返回分funnel写入确认回执(resp_receipt)

        // 3. 处理分funnel检索请求
        IF 收到 ag-mem-03 下发L2分桶查询请求:
            filter_fid = 请求.目标funnel_id
            query_cond = 请求.查询条件
            max_return = 请求.最大返回条数
            match_result = []
            if filter_fid in l2_funnel_store:
                entry_list = l2_funnel_store[filter_fid]
                // 条件过滤，按I值降序截取上限
                temp_res = [e for e in entry_list if 匹配查询条件(e, query_cond)]
                temp_res.sort(key=lambda x:x.I值, reverse=True)
                match_result = temp_res[:max_return]
                // 更新每条条目访问时间，刷新索引标签访问频次
                idx_meta = funnel_hash_index[filter_fid]
                for item in match_result:
                    item.最近访问时间 = NOW()
                    for tag in item.hash_tag_list:
                        idx_meta.tag_usage_stat[tag] = idx_meta.tag_usage_stat.get(tag, 0) + 1
            // 组装结果返回调度单元
            query_resp = {
                funnel_id: filter_fid,
                match_entry_list: match_result
            }
            向 ag-mem-03 返回L2分桶查询结果集(query_resp)

        // 4. 每小时定时全funnel超期条目扫描分流
        IF 距上次定时扫描 >= 3600:
            promote_to_l3_batch = []
            global_clean_packs = []
            for fid in l2_funnel_store.keys():
                entry_list = l2_funnel_store[fid]
                l3_thresh = funnel_l2_thresh_map.get(fid, 0.62)
                expire_entries = [e for e in entry_list if (NOW()-e.promote_ts)/3600 >= 168]
                if len(expire_entries) == 0:
                    continue
                promote_fid_list = []
                clean_fid_list = []
                clean_tags_set = set()
                for e in expire_entries:
                    if e.I值 >= l3_thresh:
                        promote_fid_list.append(e)
                        promote_to_l3_batch.append(e)
                    else:
                        clean_fid_list.append(e)
                        for tag in e.hash_tag_list:
                            clean_tags_set.add(tag)
                // 打包当前漏斗清除条目
                if len(clean_fid_list) > 0:
                    clean_ids = [x.entry_id for x in clean_fid_list]
                    global_clean_packs.append({
                        funnel_id: fid,
                        clean_entry_ids: clean_ids,
                        merged_clean_tags: list(clean_tags_set)
                    })
                // 从本地存储移除超期条目
                remain_entries = [e for e in entry_list if e not in expire_entries]
                del_count = len(expire_entries)
                l2_funnel_store[fid] = remain_entries
                funnel_item_counter[fid] -= del_count
                global_total_count -= del_count
                // 同步清理该漏斗索引内失效条目映射
                idx_meta = funnel_hash_index[fid]
                del_eids = [x.entry_id for x in expire_entries]
                for tag in list(idx_meta.tag_map.keys()):
                    new_tag_map = [eid for eid in idx_meta.tag_map[tag] if eid not in del_eids]
                    if len(new_tag_map) == 0:
                        del idx_meta.tag_map[tag]
                    else:
                        idx_meta.tag_map[tag] = new_tag_map
            // 批量推送晋升条目至L3
            if len(promote_to_l3_batch) > 0:
                向 ag-mem-24 发送L2超期晋升至L3条目清单(promote_to_l3_batch)
            // 批量推送清除包至删除单元
            for pack in global_clean_packs:
                向 ag-mem-42 发送待清除条目+失效哈希标签包(pack)
            重置定时扫描计时器

        // 5. 每30秒全局状态上报
        IF 距上次状态上报 >= 30:
            funnel_snapshot = {}
            for fid in l2_funnel_store.keys():
                cap = ag-mem-48.query_single_funnel_capacity(fid)
                idx_tag_total = sum(len(v["tag_map"].keys()) for k,v in funnel_hash_index[fid].items())
                funnel_snapshot[fid] = {
                    item_count: funnel_item_counter[fid],
                    usage_rate: cap.当前使用率,
                    index_tag_amount: idx_tag_total
                }
            report_data = {
                current_state: internal_state,
                global_total_item: global_total_count,
                global_l2_usage: ag-mem-48.query_global_l2_usage(),
                each_funnel_detail: funnel_snapshot
            }
            向 ag-mem-48、ag-mem-01 发送L2全局分桶状态上报(report_data)

        SLEEP 10ms
```

## 约束与异常处理（V1.1 新增funnel、哈希索引相关异常）
| 场景 | 处理方式 | 恢复条件 |
|------|----------|----------|
| 目标funnel_id不存在，无对应分桶存储 | 直接拒绝写入/查询，返回“子漏斗未注册” | ag-mem-14完成funnel新建同步至总控注册表 |
| 单funnel分桶IO持久化故障 | 仅阻断该漏斗读写，其余funnel正常服务，标记故障漏斗上报告警 | 底层存储分区修复完成 |
| 经验条目超过15KB尺寸上限 | 跳过本条，不写入L2，条目留存L1层等待下次衰减评估 | 压缩条目至15KB以内后重新晋升 |
| 单funnel容量紧急，清理后仍无空余存储 | 拦截所有新晋升写入，仅保留存量条目只读 | 定时7天超期清理释放存储空间 |
| 全局系统熔断触发 | 冻结所有写入、哈希索引新增/删除操作，存量条目仅支持查询 | ag-mem-01下发恢复指令并自检通过 |
| 单漏斗哈希索引标签达12000上限 | 自动清理20%低频访问标签，释放索引内存空间 | 正常业务写入补充新标签 |
| L2超期条目晋升写入ag-mem-24失败 | 该批条目回留L2存储，下一小时定时扫描重新判定 | L3存储服务恢复正常 |

## 总线契约（废弃分槽编号，统一funnel/索引字段传输）
| 总线 | 操作 | 数据内容 | 权限 | 说明 |
|------|------|----------|------|------|
| 内部调度总线 | 读 | 单funnel L2晋升条目列表（携带funnel_id、hash_tag_list、index_bucket、result_validated） | 只读 | ag-mem-21 发送 |
| 内部调度总线 | 读 | 分funnel经验查询请求（带funnel过滤条件） | 只读 | ag-mem-03 调度单元发送 |
| 内部调度总线 | 读 | 单漏斗/全局L2容量查询回执 | 只读 | ag-mem-48 容量管控返回 |
| 内部调度总线 | 读 | ag-mem-01全局熔断、调度控制指令 | 只读 | 顶层总控下发 |
| 内部调度总线 | 写 | 分funnel写入确认回执（携带funnel、索引桶ID） | 专属写入 | 向 ag-mem-21 返回 |
| 内部调度总线 | 写 | L2分桶查询结果集（带完整哈希标签、funnel标识） | 专属写入 | 向 ag-mem-03 返回 |
| 内部调度总线 | 写 | 新增funnel条目通知（携带index_bucket_id） | 专属写入 | 向 ag-mem-23 热度统计发送 |
| 内部调度总线 | 写 | L2超期晋升条目清单（完整索引字段） | 专属写入 | 向 ag-mem-24 L3存储发送 |
| 内部调度总线 | 写 | 待清除条目+失效哈希标签合并包 | 专属写入 | 向 ag-mem-42 删除单元发送 |
| 内部调度总线 | 写 | L2全局分桶状态上报（各漏斗存储/索引占用快照） | 周期性写入 | 同步上报 ag-mem-48、ag-mem-01 |

## 安全边界（V1.1新增动态漏斗、哈希索引隔离约束）
| 规则编号 | 内容 |
|:---:|------|
| S-01 | L2仅持久化存储原始经验数据，禁止修改条目正文，仅维护funnel分桶元数据与哈希索引映射关系 |
| S-02 | 所有存储、索引读写严格绑定唯一funnel_id，跨漏斗物理隔离，禁止混存、跨索引桶读写标签 |
| S-03 | 容量清理、超期分流仅在MAINT维护/定时周期执行，业务NORMAL读写高峰不销毁存储与索引资源 |
| S-04 | 7天生命周期为强制约束，超期条目必须执行晋升L3或永久清除，不允许无限滞留L2层 |
| S-05 | 查询请求必须携带funnel过滤条件，仅返回匹配漏斗内条目，禁止全局全库无过滤检索 |
| S-06 | 条目清除必须同步归集全部关联hash_tag，同步删除对应index_bucket内条目映射，杜绝索引脏数据残留 |
| S-07 | 不存在于ag-mem-01全局注册表的funnel_id，一律拒绝写入与查询，防止无管控游离漏斗破坏全局收敛机制 |
| S-08 | 全局熔断状态下，哈希索引仅可读，禁止新增、删除、修改标签映射 |

## 接口校验用例（适配funnel分桶、哈希索引、动态阈值）
| 用例编号 | 前置条件 | 输入 | 预期输出 |
|----------|----------|------|----------|
| TC-M22-01 | `NORMAL`，funnel=F002全局使用率50% | 3条晋升条目，hash_tag=["JS","异步"] | 全部写入F002独立分桶与索引桶，通知ag-mem-23，回执携带funnel_id与index_bucket |
| TC-M22-02 | `NORMAL`，funnel=F005使用率82%，存在7天超期低I条目 | 晋升条目写入请求 | 写入正常，异步发起该漏斗过期低价值条目清理并同步失效索引标签 |
| TC-M22-03 | `CAPACITY_CRITICAL`，funnel=F007达1500条硬上限 | 批量晋升写入请求 | 同步强制清理超期条目释放空间；清理后仍满则直接拒绝写入 |
| TC-M22-04 | `NORMAL`，查询请求指定funnel=F003过滤 | 检索关键词“接口测试” | 仅返回F003漏斗内匹配条目，更新条目访问时间与索引标签访问频次 |
| TC-M22-05 | `NORMAL`，1小时定时扫描触发，存在7天超期条目 | F001超期条目：I=0.7（阈值0.62）、F004超期条目I=0.3 | F001条目打包晋升至ag-mem-24；F004条目移交ag-mem-42并清理哈希索引 |
| TC-M22-06 | `SYSTEM_PAUSED`，任意funnel晋升写入请求 | 分funnel晋升条目列表 | 返回写入成功数=0，拒绝原因为全局熔断 |
| TC-M22-07 | `NORMAL`，条目体积16KB，funnel=F009 | 超大尺寸晋升条目 | 直接跳过，不写入L2，保留在L1 |
| TC-M22-08 | `NORMAL`，funnel=F010索引标签达12000上限 | 新增带4个全新标签的晋升条目 | 自动清理20%低频旧标签后，新增当前条目哈希映射 |

## 质量自检清单（V1.1完整达标）
| 检查项 | 状态 |
|--------|:---:|
| 模块编号、五层存储分区归属不变，彻底移除V1.0固定ag-mem-15~19分槽逻辑 | ✅ |
| 新增依赖ag-mem-01总控F0，用于拉取全局漏斗注册表、索引元数据、funnel专属晋升阈值 | ✅ |
| 原有4种运行状态完整保留，容量判定逻辑升级为单漏斗+全局双维度校验 | ✅ |
| 输入输出全部替换funnel_id作为分桶标识，新增hash_tag_list、index_bucket_id、result_validated核心传输字段 | ✅ |
| 存储配置拆分全局共享配额+单funnel独立硬上限，新增哈希索引标签容量约束 | ✅ |
| 伪代码完整实现分funnel独立存储、专属哈希索引全生命周期维护、容量分级清理、每小时超期自动分流 | ✅ |
| 异常处理覆盖无效funnel、分桶IO故障、索引超限、熔断、L3晋升写入失败等新增场景 | ✅ |
| 总线契约全部移除旧分槽编号传输，统一携带funnel与索引桶标识 | ✅ |
| 安全边界新增漏斗物理隔离、索引脏数据清理、非法漏斗拦截、熔断只读约束 | ✅ |
| 校验用例覆盖正常写入、容量预警/紧急清理、分funnel精准查询、7天超期分流、索引超限、熔断、超大条目拦截全场景 | ✅ |
| 完全对齐V1.1动态有限子漏斗、分桶哈希索引、MLNF-Mem五层统一存储架构标准 | ✅ |

---