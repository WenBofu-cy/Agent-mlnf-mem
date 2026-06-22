# MemoryBus 总线报文规范 V1.1
**EM-Core Agent · 认知层与记忆层数据交互标准**
> 版本：V1.1 ｜ 日期：2026-06-22
> 适用中枢：ECC（认知大脑） ↔ MLNF-Mem（记忆中枢）
> 架构同源：EM-Core HR人形机器人 / EM-Core AD自动驾驶

## 一、总线定位
MemoryBus 是 ECC 认知大脑与 MLNF-Mem 记忆中枢之间的唯一数据通道。所有记忆检索、经验写入、I值同步、会话锁管控、审计日志归档均通过本总线完成。

**核心约束：**
1. ECC-06（记忆交互单元）和 ECC-12（全局网关）为 MemoryBus 常规请求发起端。会话锁指令（LOCK / UNLOCK / QUERY_LOCK_STATUS）仅由 ECC-12 发起。MLNF-Mem 仅被动响应，不主动推送数据。
2. 所有报文采用异步非阻塞模式。全部操作统一为请求-响应双报文模型，响应报文送达后ECC回复MSG_ACK确认；本总线不定义 NOTIFY 报文类型。
3. 多会话报文基于 session_id 分片隔离处理。同一 session 内写入串行排队，跨 session 写入并行分片处理；锁相关指令全局快照隔离串行执行。
4. 会话锁指令优先级高于所有常规读写操作，即时处理，不进入排队队列。
5. CerebellumBus 接收全会话OK回执后，ECC-12 方可通过 MemoryBus 下发UNLOCK指令；跨设备SESSION_SWITCH携带force_takeover=true时，ECC同步下发强制UNLOCK，无视原设备在线状态。
6. L3风险等级任务申请记忆锁自动升级为EXCLUSIVE排他锁，禁止并行读取。

## 二、报文通用格式
### 2.1 通用报文头
```json
{
  "header": {
    "msg_id": "mem-20260621-143105-0001",
    "msg_type": "REQUEST | RESPONSE",
    "source": "ECC-06",
    "target": "MLNF-01",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.000Z",
    "ext_version": "1.1"
  },
  "body": {}
}
```

### 2.2 头部字段定义
| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| msg_id | string | ✅ | 全局唯一报文ID，格式 `mem-{date}-{time}-{seq}`，用于幂等去重 |
| msg_type | enum | ✅ | REQUEST（请求）/ RESPONSE（响应） |
| source | string | ✅ | 发送方模块编号。REQUEST 为 ECC-06 或 ECC-12；RESPONSE 为 MLNF 对应模块 |
| target | string | ✅ | 接收方模块编号 |
| session_id | string | ✅ | 会话隔离标识，多会话并发时用于锁路由 |
| timestamp | ISO8601 | ✅ | 报文生成时间，须保留三位毫秒精度（.sssZ）。接收端若与本地时间偏差超过 ±60s，直接丢弃并返回ERROR，同步上报时钟异常告警 |
| ext_version | string | ✅ | 协议版本，固定 "1.1"。主版本不同直接丢弃，次版本向前兼容 |

### 2.3 标准响应报文
```json
{
  "header": {
    "msg_id": "mem-20260621-143105-0002",
    "msg_type": "RESPONSE",
    "source": "MLNF-01",
    "target": "ECC-06",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.015Z",
    "ext_version": "1.1",
    "ref_msg_id": "mem-20260621-143105-0001"
  },
  "body": {
    "status": "OK | ERROR | LOCKED | NOT_FOUND | PERMISSION_DENIED | TIMEOUT",
    "sign_mlnf01": "mlnf_sig_xxxxxx",
    "data": {},
    "error": {
      "code": "MEM_LOCK_ACTIVE",
      "message": "当前会话只读锁活跃，写入操作被拦截",
      "offset_ms": 6500
    }
  }
}
```

### 2.4 MSG_ACK确认报文
```json
{
  "header": {
    "msg_id": "mem-ack-xxxx",
    "msg_type": "RESPONSE",
    "source": "ECC-06",
    "target": "MLNF-01",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:06.000Z",
    "ext_version": "1.1"
  },
  "body": {
    "operation": "MSG_ACK",
    "ref_msg_id": "mem-20260621-143105-0001"
  }
}
```

### 2.5 响应状态码
| 状态码 | 说明 | 对应 error.code 示例 |
|--------|------|----------------------|
| OK | 操作成功 | — |
| ERROR | 内部错误 | MEM_ENTRY_CORRUPTED |
| LOCKED | 会话只读锁活跃，写入/晋升操作被拦截 | MEM_LOCK_ACTIVE |
| NOT_FOUND | 查询的记忆条目不存在 | — |
| PERMISSION_DENIED | ECC-05 权限校验未通过 | MEM_PERMISSION_DENIED |
| TIMEOUT | 总线通信超时（10s） | MEM_BUS_TIMEOUT |

status 字段承载宏观业务状态；error.code 承载微观错误码，仅在 status 非 OK 时出现；offset_ms 仅时钟偏移异常时填充，代表本地与报文时间戳差值。

## 三、核心操作报文定义
### 3.1 记忆检索查询（ECC-06 → MLNF-52）
```json
{
  "header": {
    "msg_type": "REQUEST",
    "source": "ECC-06",
    "target": "MLNF-52"
  },
  "body": {
    "operation": "QUERY",
    "sign_ecc12": "e12_sig_xxxxxx",
    "query": {
      "funnel": "FUNNEL_ONE | FUNNEL_TWO",
      "funnel_id": "u_1 | f_3",
      "layer": "L1 | L2 | L3 | L4 | L5 | ALL",
      "match_type": "EXACT | SEMANTIC | RANGE",
      "conditions": {
        "task_type": "代码开发",
        "time_range": {"from": "2026-06-01", "to": "2026-06-21"},
        "i_value_min": 0.6,
        "max_results": 10
      },
      "semantic_vector": []
    }
  }
}
```
**响应：**
```json
{
  "header": {
    "msg_type": "RESPONSE",
    "source": "MLNF-52",
    "target": "ECC-06",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.015Z"
  },
  "body": {
    "status": "OK",
    "sign_mlnf52": "mlnf_sig_xxxxxx",
    "data": {
      "results": [
        {
          "mem_id": "mem-f3-l4-20260618-0038",
          "funnel": "FUNNEL_TWO",
          "funnel_id": "f_3",
          "layer": "L4",
          "i_value": 0.85,
          "content": {},
          "match_score": 0.92,
          "timestamp": "2026-06-18T10:30:00Z"
        }
      ],
      "total_matched": 5,
      "has_more": false,
      "query_time_ms": 3
    }
  }
}
```

### 3.2 经验写入（ECC-12 → MLNF-01）
```json
{
  "header": {
    "msg_type": "REQUEST",
    "source": "ECC-12",
    "target": "MLNF-01"
  },
  "body": {
    "operation": "WRITE",
    "sign_ecc12": "e12_sig_xxxxxx",
    "funnel": "FUNNEL_TWO",
    "funnel_id": "f_3",
    "layer": "L1",
    "entry": {
      "task_type": "代码开发",
      "action_sequence": ["打开IDE", "定位第45行", "修改参数"],
      "result": "SUCCESS",
      "s_value": 0.3,
      "v_value": 0.9,
      "c_value": 0.5,
      "timestamp": "2026-06-21T14:31:05.000Z",
      "source_session": "session_x"
    }
  }
}
```
**响应：**
```json
{
  "header": {
    "msg_type": "RESPONSE",
    "source": "MLNF-01",
    "target": "ECC-12",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.020Z"
  },
  "body": {
    "status": "OK | ERROR | LOCKED | PERMISSION_DENIED",
    "sign_mlnf01": "mlnf_sig_xxxxxx",
    "data": {"mem_id": "mem-f3-l1-20260621-0001"},
    "error": null
  }
}
```

### 3.3 会话锁指令（ECC-12 → MLNF-01）
**发起方**：仅 ECC-12。
```json
{
  "header": {
    "msg_type": "REQUEST",
    "source": "ECC-12",
    "target": "MLNF-01"
  },
  "body": {
    "operation": "LOCK | UNLOCK | QUERY_LOCK_STATUS",
    "sign_ecc12": "e12_sig_xxxxxx",
    "session_id": "session_x",
    "lock_type": "READ_ONLY | EXCLUSIVE",
    "reason": "LLM_CALL_START | SESSION_ALL_TASKS_DONE | CRASH_RECOVERY_CHECK | L3_RISK_TASK"
  }
}
```
**响应：**
```json
{
  "header": {
    "msg_type": "RESPONSE",
    "source": "MLNF-01",
    "target": "ECC-12",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.030Z"
  },
  "body": {
    "status": "OK",
    "sign_mlnf01": "mlnf_sig_xxxxxx",
    "data": {
      "session_id": "session_x",
      "lock_status": "LOCKED | UNLOCKED",
      "lock_type": "READ_ONLY | EXCLUSIVE",
      "locked_at": "2026-06-21T14:31:05.000Z",
      "lock_expire_at": "2026-06-21T14:33:05.000Z",
      "pending_writes": 3
    }
  }
}
```

### 3.4 I值指标推送（ECC-06 → MLNF-30）
```json
{
  "header": {
    "msg_type": "REQUEST",
    "source": "ECC-06",
    "target": "MLNF-30"
  },
  "body": {
    "operation": "I_VALUE_UPDATE",
    "sign_ecc12": "e12_sig_xxxxxx",
    "mem_id": "mem-f3-l2-20260618-0038",
    "update_ts": "2026-06-21T14:31:05.000Z",
    "metrics": {
      "s_value": 0.3,
      "v_value": 0.9,
      "c_value": 0.7
    },
    "trigger": "TASK_COMPLETED"
  }
}
```
**响应：**
```json
{
  "header": {
    "msg_type": "RESPONSE",
    "source": "MLNF-30",
    "target": "ECC-06",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.040Z"
  },
  "body": {
    "status": "OK",
    "sign_mlnf30": "mlnf_sig_xxxxxx",
    "data": {"mem_id": "mem-f3-l2-20260618-0038", "i_value_new": 0.72}
  }
}
```

### 3.5 审计日志归档（ECC-12 → MLNF-59）
**发起方**：ECC-12（内容由 ECC-05 生成，经 ECC-12 统一转发）。
```json
{
  "header": {
    "msg_type": "REQUEST",
    "source": "ECC-12",
    "target": "MLNF-59"
  },
  "body": {
    "operation": "AUDIT_LOG",
    "sign_ecc12": "e12_sig_xxxxxx",
    "log_type": "SECURITY_INTERCEPT | MEMORY_WRITE | LOCK_OPERATION | L3_BLOCK",
    "session_id": "session_x",
    "entry": {
      "action": "MEMORY_WRITE_BLOCKED",
      "reason": "SESSION_LOCK_ACTIVE",
      "mem_id": "mem-f3-l2-20260618-0038",
      "timestamp": "2026-06-21T14:31:05.000Z"
    }
  }
}
```
**响应：**
```json
{
  "header": {
    "msg_type": "RESPONSE",
    "source": "MLNF-59",
    "target": "ECC-12",
    "session_id": "session_x",
    "timestamp": "2026-06-21T14:31:05.050Z"
  },
  "body": {
    "status": "OK",
    "sign_mlnf59": "mlnf_sig_xxxxxx",
    "data": {"log_id": "audit-20260621-143105-0001"}
  }
}
```

## 四、错误码定义
| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| MEM_LOCK_ACTIVE | 会话只读锁活跃，写入/晋升被拦截 | 等待锁释放后重试 |
| MEM_QUOTA_EXCEEDED | 全局存储配额已满 | 触发低重要度记忆清理，清理完成发起QUERY同步存量至ECC缓存 |
| MEM_LAYER_QUOTA_FULL | 目标层级配额已满 | 触发层级记忆晋升/清理，完成后同步存量至ECC缓存 |
| MEM_FUNNEL_NOT_FOUND | 目标子漏斗不存在 | 触发子漏斗创建流程 |
| MEM_ENTRY_CORRUPTED | 记忆条目数据损坏 | MLNF 自动从备份分区恢复，恢复失败则标记条目不可用 |
| MEM_PERMISSION_DENIED | ECC-05 权限校验未通过 | 记录审计日志 |
| MEM_BUS_TIMEOUT | 总线通信超时（10s） | 重试，全局最多3次 |
| MEM_SIGN_INVALID | 报文签名校验失败 | 丢弃报文，强制写入安全审计日志；单会话1分钟累计5次触发120s临时拉黑 |
| MEM_VERSION_MISMATCH | 协议主版本不匹配 | 丢弃报文，记录兼容性日志 |
| MEM_MSG_DUPLICATE | 重复报文（msg_id 已处理） | 直接返回上次缓存成功响应，不重复执行业务逻辑 |
| MEM_PAYLOAD_OVERSIZE | 请求报文Payload超过2MB上限 | 丢弃报文，拒绝执行操作 |
| MEM_LOCK_EXPIRED | 会话锁持有超时自动释放 | 重新发起LOCK申请 |
| MEM_I_VALUE_STALE | I_VALUE_UPDATE携带时间戳早于存量指标时间 | 丢弃更新，返回OK不修改数据 |
| MEM_SESSION_FLOOD | 单会话短时间msg_id请求超限 | 临时丢弃该会话入站报文30s，写入安全日志 |

## 五、安全与并发约束
### 5.1 签名规则
- 签名算法：HMAC-SHA256。
- 签名密钥：ECC-12 与 MLNF-01/MLNF-30/MLNF-52/MLNF-59 各自持有预共享对称密钥。
- `sign_ecc12` 为 ECC-12 网关签名，**全部REQUEST报文强制携带**；对应RESPONSE报文必须携带对应MLNF模块签名`sign_mlnfXX`。
- 待签内容：将 header.msg_id、header.session_id、body.operation 字段值分别 Base64 编码，以 `.` 固定顺序拼接。严禁将 body.data 及其内部字段纳入签名计算范围。
- 校验顺序：接收方须按 格式与版本校验 → msg_id 滑动窗口去重 → 时间戳防重放 → 签名密码学校验 的顺序执行，任何一步失败立即返回 ERROR 并丢弃报文。

### 5.2 防重放、限流与重传约束
1. 滑动窗口去重：维护msg_id队列，硬性上限10000条或2MB内存；LRU淘汰旧记录，条目存放超300s自动过期删除，禁止无限缓存。
2. 会话洪水防护：单session 10s内新增msg_id超过200条，触发MEM_SESSION_FLOOD限流，30s内丢弃该会话全部入站报文。
3. 时钟偏移处理：timestamp偏差超±60s丢弃报文，响应携带offset_ms差值上报时钟异常。
4. 重传规则：MLNF发送RESPONSE后启动2s计时器，未收到MSG_ACK则间隔翻倍重传（2s→4s→8s），最多3次；一轮重传结束冷却60s才可再次发起；3次失败写入本地WAL等待会话恢复同步。
5. 重复报文处理：匹配已处理msg_id直接返回缓存成功响应，不重复执行业务。

### 5.3 并发、锁与写入约束
1. 同一session写入串行排队，跨session写入分片并行；LOCK/UNLOCK/QUERY_LOCK_STATUS全局快照串行隔离。
2. READ_ONLY锁最大持有120s，超时MLNF自动执行UNLOCK，生成审计日志；L3任务自动升级EXCLUSIVE排他锁。
3. 单次QUERY最大返回100条；单次WRITE单条REQUEST最多50条entry，超50条必须拆分为多条独立REQUEST，各自分配唯一msg_id。
4. I_VALUE_UPDATE校验update_ts，旧时间戳更新直接丢弃，避免旧数据覆盖新指标。
5. 单REQUEST Payload上限2MB，超限返回MEM_PAYLOAD_OVERSIZE拒绝执行。
6. 总线请求全局最大重试3次，单次请求超时阈值10s。

### 5.4 WAL持久化与崩溃恢复约束
1. WRITE、LOCK、UNLOCK、AUDIT_LOG 操作强制写入WAL预写日志。
2. QUERY、I_VALUE_UPDATE 可配置wal_write开关。
3. WAL本地持久化记录统一留存7天，到期自动清理。
4. 进程启动恢复逻辑：扫描未完成事务，恢复未落地记忆条目与锁状态，自动发起SESSION_RECOVER同步至ECC。

## 六、版本规范
1. 本规范 MemoryBus V1.1 与 CerebellumBus V1.1 协议版本对齐，整套 EM-Core 总线体系版本号统一。
2. 报文头部 ext_version 固定为 "1.1"，主版本不一致直接丢弃，次版本向前兼容。
3. MemoryBus 与 CerebellumBus 共用底层传输通道，业务报文隔离，支持联合升级。