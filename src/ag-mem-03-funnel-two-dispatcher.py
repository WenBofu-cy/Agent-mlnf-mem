#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-03
模块名称: 漏斗二专属调度单元
所属分区: 一、顶层总控中枢
核心职责: 作为漏斗二（任务经验漏斗）的专属调度单元，负责任务经验分槽的全生命周期管理。
          接收 ag-mem-01 转发的任务经验查询与写入请求，依据任务场景标签判定目标分槽，
          管理对话交互槽、工具调用槽、信息检索槽、创作生成槽、通用任务槽五类分槽的激活
          与休眠。确保不同分槽之间的经验条目物理隔离，管理各分槽独立的晋升阈值与遗忘
          策略参数。不参与任何认知决策，仅执行漏斗二内部资源的调度与路由。

依赖模块:
    ag-mem-01(总控漏斗F0), ag-mem-14(任务场景判定与分槽路由单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-01, ag-mem-15~19(五个场景分槽), ag-mem-20~43(五层存储与重要度引擎)

安全约束:
  S-01: 漏斗二数据编译期禁止包含任何用户个人身份信息，仅存储脱敏后的任务经验与策略
  S-02: 不同场景分槽之间的经验数据物理隔离，跨槽查询需通过本模块统一路由
  S-03: 漏斗二存储空间不足时，必须优先保护 L4/L5 层关键经验，仅清理 L1/L2 低重要度条目
  S-04: 本模块仅负责分槽调度与数据路由，不直接操作经验内容
  S-05: 维护扫描期间不得中断正常查询服务，写入请求排队不得超过 5 秒
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class DispatcherState(Enum):
    IDLE = "idle"
    SCENE_JUDGE = "scene_judge"
    SLOT_CREATING = "slot_creating"
    ROUTING = "routing"
    MAINT_SCAN = "maint_scan"
    SYSTEM_PAUSED = "system_paused"


class SceneCategory(Enum):
    DIALOGUE = "对话交互"
    TOOL_CALL = "工具调用"
    SEARCH = "信息检索"
    CREATION = "创作生成"
    GENERAL = "通用任务"


@dataclass
class ExperienceRequest:
    request_id: str = ""
    operation_type: str = "query"  # query / write
    user_id: str = ""
    scene_label: Optional[SceneCategory] = None
    task_description: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class SceneJudgmentResult:
    request_id: str = ""
    scene_category: SceneCategory = SceneCategory.GENERAL
    confidence: float = 0.0
    target_slot_id: str = ""
    secondary_slots: List[str] = field(default_factory=list)


@dataclass
class DispatchResult:
    request_id: str = ""
    success: bool = True
    data: Any = None
    error_reason: str = ""
    target_slots: List[str] = field(default_factory=list)
    query_duration_ms: float = 0.0


@dataclass
class SlotStatus:
    slot_id: str = ""
    scene_category: SceneCategory = SceneCategory.GENERAL
    is_active: bool = True
    entry_count: int = 0
    storage_usage_bytes: int = 0
    last_active_time: float = field(default_factory=time.time)


# 场景类别到分槽模块ID的映射
SCENE_TO_SLOT_MAP = {
    SceneCategory.DIALOGUE: "ag-mem-15",
    SceneCategory.TOOL_CALL: "ag-mem-16",
    SceneCategory.SEARCH: "ag-mem-17",
    SceneCategory.CREATION: "ag-mem-18",
    SceneCategory.GENERAL: "ag-mem-19",
}

# 场景类别默认权重配置
SCENE_WEIGHT_CONFIG = {
    SceneCategory.DIALOGUE: {"v_weight": 1.2, "s_weight": 1.0, "c_weight": 1.0},
    SceneCategory.TOOL_CALL: {"v_weight": 1.0, "s_weight": 1.2, "c_weight": 1.0},
    SceneCategory.SEARCH: {"v_weight": 1.0, "s_weight": 1.0, "c_weight": 1.1},
    SceneCategory.CREATION: {"v_weight": 1.1, "s_weight": 1.0, "c_weight": 1.0},
    SceneCategory.GENERAL: {"v_weight": 1.0, "s_weight": 1.0, "c_weight": 1.0},
}


class FunnelTwoDispatcher:
    MAX_PENDING_WRITES = 50
    MAINT_SCAN_INTERVAL_SEC = 24 * 3600
    MERGE_SIMILARITY_THRESHOLD = 0.1
    MAX_ENTRIES_PER_SLOT = 10000

    def __init__(self):
        self.module_id = "ag-mem-03"
        self.module_name = "漏斗二专属调度单元"
        self.version = "V1.0"

        self.state = DispatcherState.IDLE
        self._active_slots: Dict[str, SlotStatus] = {}
        self._pending_writes: List[ExperienceRequest] = []
        self._last_maint_scan_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_experience_request = None
        self._query_scene_judgment = None
        self._query_capacity_info = None
        self._publish_dispatch_result = None
        self._publish_scene_judge_request = None
        self._publish_create_slot_command = None
        self._publish_activate_signal = None
        self._publish_maint_scan_command = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_experience_request_query(self, callback: Callable[[], Optional[ExperienceRequest]]):
        self._query_experience_request = callback

    def set_scene_judgment_query(self, callback: Callable[[], Optional[SceneJudgmentResult]]):
        self._query_scene_judgment = callback

    def set_capacity_info_query(self, callback: Callable[[], Dict[str, Any]]):
        self._query_capacity_info = callback

    def set_dispatch_result_publisher(self, callback: Callable[[DispatchResult], None]):
        self._publish_dispatch_result = callback

    def set_scene_judge_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_scene_judge_request = callback

    def set_create_slot_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_create_slot_command = callback

    def set_activate_signal_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_activate_signal = callback

    def set_maint_scan_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_maint_scan_command = callback

    def set_status_report_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_dispatcher_cycle(self) -> Optional[DispatchResult]:
        now = time.time()

        if self.state == DispatcherState.SYSTEM_PAUSED:
            return None

        # 维护扫描
        if now - self._last_maint_scan_time >= self.MAINT_SCAN_INTERVAL_SEC:
            self._perform_maintenance_scan()
            self._last_maint_scan_time = now

        # 处理积压的写入请求
        if self.state == DispatcherState.IDLE and self._pending_writes:
            request = self._pending_writes.pop(0)
            return self._process_request(request)

        # 接收新请求
        request = self._query_experience_request() if self._query_experience_request else None
        if request is None:
            return None

        return self._process_request(request)

    def _process_request(self, request: ExperienceRequest) -> DispatchResult:
        start_time = time.time()

        # 场景判定
        self.state = DispatcherState.SCENE_JUDGE
        scene_result = self._judge_scene(request)

        if scene_result.confidence < 0.3:
            # 低置信度，使用通用任务槽
            scene_result = SceneJudgmentResult(
                request_id=request.request_id,
                scene_category=SceneCategory.GENERAL,
                confidence=0.3,
                target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.GENERAL]
            )

        # 检查目标分槽是否存在
        target_slot = scene_result.target_slot_id
        if target_slot not in self._active_slots:
            self.state = DispatcherState.SLOT_CREATING
            self._create_slot(scene_result.scene_category, target_slot)

        # 数据路由
        self.state = DispatcherState.ROUTING
        if request.operation_type == "query":
            result = self._route_query(request, scene_result)
        else:
            result = self._route_write(request, scene_result)

        result.query_duration_ms = (time.time() - start_time) * 1000
        self.state = DispatcherState.IDLE

        if self._publish_dispatch_result:
            self._publish_dispatch_result(result)
        return result

    def _judge_scene(self, request: ExperienceRequest) -> SceneJudgmentResult:
        # 如果请求中已带场景标签，直接使用
        if request.scene_label and request.scene_label in SCENE_TO_SLOT_MAP:
            return SceneJudgmentResult(
                request_id=request.request_id,
                scene_category=request.scene_label,
                confidence=0.9,
                target_slot_id=SCENE_TO_SLOT_MAP[request.scene_label]
            )

        # 否则向 ag-mem-14 发起场景判定
        if self._publish_scene_judge_request:
            self._publish_scene_judge_request("ag-mem-14", {
                "task_description": request.task_description,
                "context": request.context,
                "user_id": request.user_id
            })

        # 尝试获取判定结果
        judgment = self._query_scene_judgment() if self._query_scene_judgment else None
        if judgment:
            return judgment

        # 默认返回通用任务
        return SceneJudgmentResult(
            request_id=request.request_id,
            scene_category=SceneCategory.GENERAL,
            confidence=0.5,
            target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.GENERAL]
        )

    def _create_slot(self, scene: SceneCategory, slot_id: str):
        if self._publish_create_slot_command:
            self._publish_create_slot_command(slot_id, {
                "scene_category": scene.value,
                "slot_id": slot_id
            })

        self._active_slots[slot_id] = SlotStatus(
            slot_id=slot_id,
            scene_category=scene
        )

        # 激活信号
        if self._publish_activate_signal:
            self._publish_activate_signal(slot_id, {"scene_category": scene.value})
            self._publish_activate_signal("ag-mem-20", {"slot_id": slot_id, "scene_category": scene.value})

        self._log_event("SLOT_CREATED", {"slot_id": slot_id, "scene": scene.value})

    def _route_query(self, request: ExperienceRequest, scene: SceneJudgmentResult) -> DispatchResult:
        target_slots = [scene.target_slot_id]
        if scene.secondary_slots:
            target_slots.extend(scene.secondary_slots)

        results = []
        for slot_id in target_slots[:2]:  # 最多查询主槽和第一个次选槽
            slot_data = self._query_slot(slot_id, request)
            if slot_data:
                results.extend(slot_data)

        return DispatchResult(
            request_id=request.request_id,
            success=True,
            data=results,
            target_slots=target_slots
        )

    def _route_write(self, request: ExperienceRequest, scene: SceneJudgmentResult) -> DispatchResult:
        target_slots = [scene.target_slot_id]

        # 多场景匹配
        if scene.secondary_slots:
            for sec_slot in scene.secondary_slots:
                target_slots.append(sec_slot)

        write_results = {}
        for slot_id in target_slots:
            write_results[slot_id] = self._write_to_slot(slot_id, request)

        return DispatchResult(
            request_id=request.request_id,
            success=any(r.get("success") for r in write_results.values()),
            data=write_results,
            target_slots=target_slots
        )

    def _query_slot(self, slot_id: str, request: ExperienceRequest) -> Optional[List[Dict[str, Any]]]:
        if self._publish_dispatch_result:
            # 实际会通过总线查询，此处简化
            pass
        return None

    def _write_to_slot(self, slot_id: str, request: ExperienceRequest) -> Dict[str, Any]:
        if slot_id in self._active_slots:
            self._active_slots[slot_id].last_active_time = time.time()
        return {"success": True, "slot_id": slot_id, "entry_id": f"EXP-{uuid.uuid4().hex[:8]}"}

    def _perform_maintenance_scan(self):
        self.state = DispatcherState.MAINT_SCAN

        # 归并扫描
        if self._publish_maint_scan_command:
            self._publish_maint_scan_command("ag-mem-25", {"scan_type": "merge"})

        # 遗忘扫描
        if self._publish_maint_scan_command:
            self._publish_maint_scan_command("ag-mem-40", {"scan_type": "forget"})

        # 休眠检测
        now = time.time()
        for slot_id, status in list(self._active_slots.items()):
            if (now - status.last_active_time) > 30 * 24 * 3600:
                status.is_active = False
                self._log_event("SLOT_DEACTIVATED", {"slot_id": slot_id})

        self.state = DispatcherState.IDLE

    def get_slot_config(self, slot_id: str) -> Optional[Dict[str, Any]]:
        if slot_id in SCENE_WEIGHT_CONFIG:
            return SCENE_WEIGHT_CONFIG.get(slot_id)
        # 通过slot_id反查场景
        for scene, sid in SCENE_TO_SLOT_MAP.items():
            if sid == slot_id:
                return SCENE_WEIGHT_CONFIG.get(scene)
        return None

    def get_state(self) -> DispatcherState:
        return self.state

    def emergency_shutdown(self):
        self.state = DispatcherState.SYSTEM_PAUSED
        self._pending_writes.clear()
        print(f"[{self.module_id}] 紧急熔断")

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(entry)
        if self._publish_event_log:
            self._publish_event_log(entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 漏斗二专属调度单元 (ag-mem-03) 演示")
    print("=" * 70)

    dispatcher = FunnelTwoDispatcher()

    print_separator("STEP 1: 查询请求 - 工具调用场景")
    dispatcher.set_experience_request_query(lambda: ExperienceRequest(
        request_id="REQ-001",
        operation_type="query",
        scene_label=SceneCategory.TOOL_CALL,
        task_description="调用天气API查询北京天气"
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  请求ID: {result.request_id}")
        print(f"  成功: {result.success}")
        print(f"  目标分槽: {result.target_slots}")

    print_separator("STEP 2: 写入请求 - 对话交互场景")
    dispatcher.set_experience_request_query(lambda: ExperienceRequest(
        request_id="REQ-002",
        operation_type="write",
        scene_label=SceneCategory.DIALOGUE,
        task_description="问候语对话",
        payload={"reply_template": "你好！有什么可以帮你的？"}
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  写入结果: {result.data}")

    print_separator("STEP 3: 无场景标签 - 自动判定")
    dispatcher.set_experience_request_query(lambda: ExperienceRequest(
        request_id="REQ-003",
        operation_type="query",
        task_description="帮我写一篇关于AI的短文"
    ))
    dispatcher.set_scene_judgment_query(lambda: SceneJudgmentResult(
        request_id="REQ-003",
        scene_category=SceneCategory.CREATION,
        confidence=0.85,
        target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.CREATION]
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  场景判定: 创作生成 (置信度 0.85)")
        print(f"  目标分槽: {result.target_slots}")

    print("\n✅ 漏斗二专属调度单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-03 漏斗二专属调度单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_dispatcher():
            d = FunnelTwoDispatcher()
            return d

        # TC-M03-01: 带场景标签的查询请求
        print("\n[TC-M03-01] 带场景标签的查询请求")
        try:
            d = setup_dispatcher()
            d.set_experience_request_query(lambda: ExperienceRequest(
                request_id="T01", operation_type="query",
                scene_label=SceneCategory.TOOL_CALL
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert result.success
            assert SCENE_TO_SLOT_MAP[SceneCategory.TOOL_CALL] in result.target_slots
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M03-02: 写入请求创建分槽
        print("\n[TC-M03-02] 写入请求创建分槽")
        try:
            d = setup_dispatcher()
            d.set_experience_request_query(lambda: ExperienceRequest(
                request_id="T02", operation_type="write",
                scene_label=SceneCategory.DIALOGUE
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert result.success
            assert SCENE_TO_SLOT_MAP[SceneCategory.DIALOGUE] in d._active_slots
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M03-03: 低置信度场景回退到通用任务
        print("\n[TC-M03-03] 低置信度场景回退到通用任务")
        try:
            d = setup_dispatcher()
            d.set_experience_request_query(lambda: ExperienceRequest(
                request_id="T03", operation_type="query"
            ))
            d.set_scene_judgment_query(lambda: SceneJudgmentResult(
                request_id="T03", scene_category=SceneCategory.GENERAL,
                confidence=0.2, target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.GENERAL]
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert SCENE_TO_SLOT_MAP[SceneCategory.GENERAL] in result.target_slots
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M03-04: 维护扫描
        print("\n[TC-M03-04] 维护扫描")
        try:
            d = setup_dispatcher()
            d._last_maint_scan_time = 0
            d.run_dispatcher_cycle()
            assert d.state in (DispatcherState.IDLE, DispatcherState.MAINT_SCAN)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M03-05: 紧急熔断
        print("\n[TC-M03-05] 紧急熔断")
        try:
            d = setup_dispatcher()
            d.emergency_shutdown()
            assert d.state == DispatcherState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M03-06: 无场景标签自动判定
        print("\n[TC-M03-06] 无场景标签自动判定为创作生成")
        try:
            d = setup_dispatcher()
            d.set_experience_request_query(lambda: ExperienceRequest(
                request_id="T06", operation_type="query",
                task_description="写一篇文章"
            ))
            d.set_scene_judgment_query(lambda: SceneJudgmentResult(
                request_id="T06", scene_category=SceneCategory.CREATION,
                confidence=0.8, target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.CREATION]
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert SCENE_TO_SLOT_MAP[SceneCategory.CREATION] in result.target_slots
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} PASS, {failed} FAIL")
        print("=" * 60)
    else:
        demo_main()
```