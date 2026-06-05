#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent-mlnf-mem 双漏斗记忆中枢 · 主入口
版本：V1.0 
原创提出者：文波福
开源协议：CC BY-NC 4.0

修改记录：
- V1.0：修复主循环方法名、补充外部总线注册、增加日志收集保护
- V1.0：统一模块主方法为 run_cycle()，增加系统健康监控
- V1.0：实现双总线架构，支持内部/外部通信隔离
- V1.0：演示用例支持结果验证，严格对齐接口规格
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_bus import InternalBus, MemoryBus, Message
from module_registry import MODULE_REGISTRY

# 分区一：顶层总控中枢
from ag_mem_01_f0_global_dispatch import F0GlobalDispatch
from ag_mem_02_funnel_one_dispatcher import FunnelOneDispatcher
from ag_mem_03_funnel_two_dispatcher import FunnelTwoDispatcher

# 分区二：漏斗一——用户画像漏斗
from ag_mem_04_driver_identity_recognition import UserIdentityRecognizer
from ag_mem_05_slot_creation_init import SlotCreationUnit
from ag_mem_06_slot_isolation_gate import SlotIsolationGate
from ag_mem_07_behavior_observation import BehaviorObservationRecorder
from ag_mem_08_context_scene_marker import ContextSceneMarker
from ag_mem_09_behavior_label_judge import PreferenceLabelJudge
from ag_mem_10_behavior_statistics import PreferenceStatistics
from ag_mem_11_driving_assist_reminder import PersonalizedSuggestionGenerator
from ag_mem_12_temporary_slot_cleanup import TemporarySlotCleanup
from ag_mem_13_long_term_inactive_reminder import LongTermInactiveReminder

# 分区三：漏斗二——任务经验漏斗 / 场景分槽管理
from ag_mem_14_scene_judgment_router import SceneJudgmentRouter
from ag_mem_15_dialogue_slot import DialogueSlot
from ag_mem_16_tool_call_slot import ToolCallSlot
from ag_mem_17_search_slot import SearchSlot
from ag_mem_18_creation_slot import CreationSlot
from ag_mem_19_general_slot import GeneralSlot

# 分区三：漏斗二——五层存储
from ag_mem_20_l1_temporary_storage import L1TemporaryStorage
from ag_mem_21_l1_decay_assessment import L1DecayAssessment
from ag_mem_22_l2_recent_storage import L2RecentStorage
from ag_mem_23_l2_heat_statistics import L2HeatStatistics
from ag_mem_24_l3_midterm_storage import L3MidTermStorage
from ag_mem_25_l3_similarity_merge import L3SimilarityMerge
from ag_mem_26_l4_long_term_storage import L4LongTermStorage
from ag_mem_27_l4_abstract_refine import L4AbstractionRefiner
from ag_mem_28_l5_core_storage import L5CoreStorage
from ag_mem_29_l5_lock_control import L5CoreLockControl
from ag_mem_30_l5_access_controller import L5AccessController

# 分区三：漏斗二——三维重要度计算引擎
from ag_mem_31_s_value_calculator import SValueCalculator
from ag_mem_32_v_value_calculator import VValueCalculator
from ag_mem_33_c_value_statistics import CValueStatistics
from ag_mem_34_i0_value_assignment import I0AssignmentUnit
from ag_mem_35_weight_coefficient_config import WeightConfigManager
from ag_mem_36_i_value_aggregator import IValueAggregator
from ag_mem_37_i_refresh_scheduler import IValueRefreshScheduler

# 分区三：漏斗二——晋升与遗忘执行机制
from ag_mem_38_promotion_judge import PromotionConditionJudge
from ag_mem_39_layer_transfer import LayerTransferUnit
from ag_mem_40_forget_threshold_judge import ForgetThresholdJudge
from ag_mem_41_min_usage_validator import MinUsageValidator
from ag_mem_42_redundant_pruner import RedundantMemoryPruner
from ag_mem_43_failure_arbitration import FailureArbitrationUnit

# 分区四：漏斗外挂扩展区
from ag_mem_44_knowledge_base import IndependentKnowledgeBase
from ag_mem_45_safety_rule_base import SafetyRuleBase
from ag_mem_46_emotion_intent_engine import EmotionIntentEngine
from ag_mem_47_question_cache import QuestionCache

# 分区五：存储与系统运维
from ag_mem_48_global_quota_controller import GlobalQuotaController
from ag_mem_49_cold_storage_manager import ColdStorageManager
from ag_mem_50_memory_import_export import MemoryImportExportUnit
from ag_mem_51_change_log_tracer import ChangeLogTracer


class AgentMlnfMem:
    """MLNF‑Mem 双漏斗记忆中枢 主控类（最终稳定版）"""

    def __init__(self):
        # 内部调度总线（51 个模块专用）
        self.internal_bus = InternalBus(validate_modules=False)  # 集成后可启用校验
        # 对外总线（ECC ↔ MLNF 专用）
        self.external_bus = MemoryBus(validate_modules=False)

        self._module_map: dict = {}  # 模块ID→实例映射表
        self.cycle_count = 0
        self.running = True

        # ========== 实例化模块 ==========
        self._init_modules()

        # 注入总线引用 + 设置 module_id（严格对齐注册表）
        self._inject_bus_and_module_id()

        # 注册所有模块到内部总线，并注册 ag-mem-01 到外部总线
        self._register_all_modules()

        # 绑定最小可运行回调
        self._wire_callbacks()

        print("Agent-mlnf-mem 双漏斗记忆中枢 初始化完成")
        print(f"  模块总数: 51 (已加载: {self._loaded_module_count()})")

    def _init_modules(self):
        """实例化全部 51 个模块"""
        # 分区一：顶层总控中枢
        self.f0 = F0GlobalDispatch()
        self.f1_dispatcher = FunnelOneDispatcher()
        self.f2_dispatcher = FunnelTwoDispatcher()

        # 分区二：漏斗一——用户画像漏斗
        self.identity_recognizer = UserIdentityRecognizer()
        self.slot_creator = SlotCreationUnit()
        self.isolation_gate = SlotIsolationGate()
        self.behavior_recorder = BehaviorObservationRecorder()
        self.scene_marker = ContextSceneMarker()
        self.preference_judge = PreferenceLabelJudge()
        self.preference_stats = PreferenceStatistics()
        self.suggestion_generator = PersonalizedSuggestionGenerator()
        self.temp_cleanup = TemporarySlotCleanup()
        self.inactive_reminder = LongTermInactiveReminder()

        # 分区三：场景分槽
        self.scene_router = SceneJudgmentRouter()
        self.dialogue_slot = DialogueSlot()
        self.tool_slot = ToolCallSlot()
        self.search_slot = SearchSlot()
        self.creation_slot = CreationSlot()
        self.general_slot = GeneralSlot()

        # 五层存储
        self.l1_storage = L1TemporaryStorage()
        self.l1_decay = L1DecayAssessment()
        self.l2_storage = L2RecentStorage()
        self.l2_heat = L2HeatStatistics()
        self.l3_storage = L3MidTermStorage()
        self.l3_merge = L3SimilarityMerge()
        self.l4_storage = L4LongTermStorage()
        self.l4_refiner = L4AbstractionRefiner()
        self.l5_storage = L5CoreStorage()
        self.l5_lock = L5CoreLockControl()
        self.l5_access = L5AccessController()

        # 三维重要度
        self.s_calc = SValueCalculator()
        self.v_calc = VValueCalculator()
        self.c_stats = CValueStatistics()
        self.i0_assign = I0AssignmentUnit()
        self.weight_config = WeightConfigManager()
        self.i_aggregator = IValueAggregator()
        self.i_refresher = IValueRefreshScheduler()

        # 晋升与遗忘
        self.promotion_judge = PromotionConditionJudge()
        self.layer_transfer = LayerTransferUnit()
        self.forget_judge = ForgetThresholdJudge()
        self.min_usage_validator = MinUsageValidator()
        self.redundant_pruner = RedundantMemoryPruner()
        self.failure_arbitration = FailureArbitrationUnit()

        # 分区四
        self.knowledge_base = IndependentKnowledgeBase()
        self.safety_rules = SafetyRuleBase()
        self.emotion_engine = EmotionIntentEngine()
        self.question_cache = QuestionCache()

        # 分区五
        self.quota_controller = GlobalQuotaController()
        self.cold_storage = ColdStorageManager()
        self.import_export = MemoryImportExportUnit()
        self.change_log = ChangeLogTracer()

    def _inject_bus_and_module_id(self):
        """注入总线引用（所有模块使用 self.bus），并为 ag-mem-01 注入外部总线"""
        id_map = {
            "f0": "ag-mem-01",
            "f1_dispatcher": "ag-mem-02",
            "f2_dispatcher": "ag-mem-03",
            "identity_recognizer": "ag-mem-04",
            "slot_creator": "ag-mem-05",
            "isolation_gate": "ag-mem-06",
            "behavior_recorder": "ag-mem-07",
            "scene_marker": "ag-mem-08",
            "preference_judge": "ag-mem-09",
            "preference_stats": "ag-mem-10",
            "suggestion_generator": "ag-mem-11",
            "temp_cleanup": "ag-mem-12",
            "inactive_reminder": "ag-mem-13",
            "scene_router": "ag-mem-14",
            "dialogue_slot": "ag-mem-15",
            "tool_slot": "ag-mem-16",
            "search_slot": "ag-mem-17",
            "creation_slot": "ag-mem-18",
            "general_slot": "ag-mem-19",
            "l1_storage": "ag-mem-20",
            "l1_decay": "ag-mem-21",
            "l2_storage": "ag-mem-22",
            "l2_heat": "ag-mem-23",
            "l3_storage": "ag-mem-24",
            "l3_merge": "ag-mem-25",
            "l4_storage": "ag-mem-26",
            "l4_refiner": "ag-mem-27",
            "l5_storage": "ag-mem-28",
            "l5_lock": "ag-mem-29",
            "l5_access": "ag-mem-30",
            "s_calc": "ag-mem-31",
            "v_calc": "ag-mem-32",
            "c_stats": "ag-mem-33",
            "i0_assign": "ag-mem-34",
            "weight_config": "ag-mem-35",
            "i_aggregator": "ag-mem-36",
            "i_refresher": "ag-mem-37",
            "promotion_judge": "ag-mem-38",
            "layer_transfer": "ag-mem-39",
            "forget_judge": "ag-mem-40",
            "min_usage_validator": "ag-mem-41",
            "redundant_pruner": "ag-mem-42",
            "failure_arbitration": "ag-mem-43",
            "knowledge_base": "ag-mem-44",
            "safety_rules": "ag-mem-45",
            "emotion_engine": "ag-mem-46",
            "question_cache": "ag-mem-47",
            "quota_controller": "ag-mem-48",
            "cold_storage": "ag-mem-49",
            "import_export": "ag-mem-50",
            "change_log": "ag-mem-51",
        }
        for attr_name, mid in id_map.items():
            module = getattr(self, attr_name)
            module.bus = self.internal_bus          # 内部总线统一注入到 self.bus
            module.module_id = mid                  # 设置 module_id
            self._module_map[mid] = module

        # 仅给顶层总控模块注入对外总线
        self.f0.external_bus = self.external_bus

    def _register_all_modules(self):
        """将所有模块注册到内部总线，并将 ag-mem-01 注册到外部总线"""
        for mid in MODULE_REGISTRY:
            self.internal_bus.register_module(mid)
        # 确保外部总线能够路由到总控模块
        self.external_bus.register_module("ag-mem-01")

    def _loaded_module_count(self) -> int:
        """统计已成功加载并设置了 module_id 的模块数"""
        return len(self._module_map)

    # ========== 回调绑定（通用点对点路由） ==========
    def _wire_callbacks(self):
        """
        最小可运行回调绑定：
        - 任何发往某模块的点对点消息，如果该模块有 handle_message 方法，则自动转发。
        - 复杂业务链路按 Spec 逐步补全。
        """
        for mid, module in self._module_map.items():
            if hasattr(module, 'handle_message'):
                self.internal_bus.subscribe_to_module(mid, module.handle_message)

    # ========== 主循环（使用各模块规格定义的原方法名） ==========
    def run_cycle(self):
        """执行一个主循环周期"""
        # 1. 先处理外部输入（ECC 请求）
        self.external_bus.process_batch(100)

        # 阶段零：顶层全局调度
        self.f0.f0_dispatch_main_loop()
        self.f1_dispatcher.funnel_one_dispatcher_main_loop()
        self.f2_dispatcher.funnel_two_dispatcher_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段一：用户画像漏斗
        self.identity_recognizer.user_identity_main_loop()
        self.slot_creator.slot_creation_main_loop()
        self.isolation_gate.slot_isolation_gate_main_loop()
        self.behavior_recorder.behavior_observation_main_loop()
        self.scene_marker.scene_marker_main_loop()
        self.preference_judge.preference_labeling_main_loop()
        self.preference_stats.preference_statistics_main_loop()
        self.suggestion_generator.suggestion_generator_main_loop()
        self.temp_cleanup.slot_cleanup_main_loop()
        self.inactive_reminder.inactive_slot_reminder_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段二：场景判定与经验写入 L1
        self.scene_router.scene_judgment_main_loop()
        self.dialogue_slot.dialogue_slot_main_loop()
        self.tool_slot.tool_call_slot_main_loop()
        self.search_slot.search_slot_main_loop()
        self.creation_slot.creation_slot_main_loop()
        self.general_slot.general_slot_main_loop()
        self.l1_storage.l1_storage_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段三：L1 衰减评估
        self.l1_decay.l1_decay_assessment_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段四：三维重要度计算
        self.s_calc.s_value_calculator_main_loop()
        self.v_calc.v_value_calculator_main_loop()
        self.c_stats.c_value_statistics_main_loop()
        self.i0_assign.i0_assignment_main_loop()
        self.i_aggregator.i_value_aggregator_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段五：晋升与遗忘
        self.promotion_judge.promotion_judge_main_loop()
        self.layer_transfer.layer_transfer_main_loop()
        self.forget_judge.forget_threshold_judge_main_loop()
        self.min_usage_validator.min_usage_validator_main_loop()
        self.redundant_pruner.redundant_memory_pruner_main_loop()
        self.failure_arbitration.failure_arbitration_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段六：L2~L5 存储维护
        self.l2_storage.l2_storage_main_loop()
        self.l2_heat.l2_heat_statistics_main_loop()
        self.l3_storage.l3_storage_main_loop()
        self.l3_merge.l3_similarity_merge_main_loop()
        self.l4_storage.l4_storage_main_loop()
        self.l4_refiner.l4_abstraction_main_loop()
        self.l5_storage.l5_storage_main_loop()
        self.l5_lock.l5_lock_control_main_loop()
        self.l5_access.l5_access_control_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段七：系统运维
        self.i_refresher.i_refresh_scheduler_main_loop()
        self.quota_controller.global_quota_controller_main_loop()
        self.cold_storage.cold_storage_manager_main_loop()
        self.import_export.memory_import_export_main_loop()
        self.change_log.change_log_tracer_main_loop()
        self.internal_bus.process_batch(100)

        # 阶段八：外挂扩展区
        self.knowledge_base.knowledge_base_main_loop()
        self.safety_rules.safety_rule_engine_main_loop()
        self.emotion_engine.emotion_intent_engine_main_loop()
        self.question_cache.question_cache_main_loop()
        self.internal_bus.process_batch(100)

        # 2. 最后处理外部输出（返回结果给 ECC）
        self.external_bus.process_batch(100)

        # 收集双总线日志并送入变更日志模块（使用 hasattr 保护）
        if hasattr(self.internal_bus, 'collect_pending_logs') and hasattr(self.external_bus, 'collect_pending_logs'):
            internal_logs = self.internal_bus.collect_pending_logs()
            external_logs = self.external_bus.collect_pending_logs()
            all_logs = internal_logs + external_logs
            if all_logs and hasattr(self.change_log, 'append_logs'):
                self.change_log.append_logs(all_logs)

        self.cycle_count += 1

    def run_forever(self, interval_sec: float = 0.1):
        """持续运行主循环（默认100ms间隔，CPU占用低）"""
        print("启动主循环...")
        try:
            while self.running:
                self.run_cycle()
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n收到中断信号，正在安全关闭...")
            self.shutdown()

    def shutdown(self):
        """安全关闭所有模块（逆序关闭，保证数据一致性）"""
        self.running = False
        for mid in reversed(list(MODULE_REGISTRY.keys())):
            module = self._module_map.get(mid)
            if module and hasattr(module, 'shutdown'):
                try:
                    module.shutdown()
                except Exception as e:
                    print(f"  关闭模块 {mid} 异常: {e}")
        print("Agent-mlnf-mem 已安全关闭")

    def get_health_status(self) -> dict:
        """获取系统健康状态（用于生产环境监控）"""
        return {
            "cycle_count": self.cycle_count,
            "internal_pending": self.internal_bus.pending_count(),
            "external_pending": self.external_bus.pending_count(),
            "loaded_modules": self._loaded_module_count(),
            "running": self.running,
        }

    # ========== 可验证演示用例 ==========
    def demo_user_profile_flow(self):
        """演示：用户画像流程（可实际看到处理结果）"""
        print("\n" + "=" * 60)
        print("  演示：用户画像流程")
        print("=" * 60)

        # 订阅身份识别结果
        def on_identity_result(msg: Message):
            result = msg.data
            print(f"  ✅ 身份识别成功: user_id={result.get('user_id')}")
            print(f"  置信度: {result.get('confidence', 0.0):.2f}")
            print(f"  推荐槽位: {result.get('recommended_slot_type', '通用槽')}")

        self.internal_bus.subscribe("ag-mem-04.identity_result", on_identity_result)

        # 构造符合规格的身份数据
        identity_data = {
            "session_id": "S001",
            "login_credential": {
                "user_id": "U001",
                "auth_token": "valid_token_2026",
                "expire_time": time.time() + 3600,
            },
            "device_fingerprint": {
                "device_id": "DEV-2026-06-06",
                "browser": "Chrome 125",
                "os": "Windows 11",
                "timezone": "Asia/Shanghai",
            },
        }

        # 发送请求并处理
        self.internal_bus.publish_to_module(
            target_module="ag-mem-04",
            event_type="identity_query",
            source_module="ag-ecc-01",
            data=identity_data,
        )
        self.internal_bus.process_all()
        print("  身份识别流程执行完成")

    def demo_experience_write_flow(self):
        """演示：经验写入流程（可实际看到写入结果）"""
        print("\n" + "=" * 60)
        print("  演示：经验写入流程")
        print("=" * 60)

        # 订阅 L1 写入结果
        def on_l1_write_result(msg: Message):
            result = msg.data
            print(f"  ✅ L1 临时层写入成功")
            print(f"  经验条目ID: {result.get('entry_id')}")
            print(f"  L1 当前使用率: {result.get('l1_usage_pct', 0.0):.2%}")

        self.internal_bus.subscribe("ag-mem-20.write_result", on_l1_write_result)

        # 构造符合 ag-mem-20 规格的写入请求
        write_data = {
            "experience_data": {
                "tool": "weather_api",
                "query": "北京 2026年6月6日 天气",
                "result": "晴，22-30℃，南风3级",
            },
            "source_slot_id": "ag-mem-16",
            "adjusted_i_value": 0.62,
            "timestamp": time.time(),
        }

        # 发送请求并处理
        self.internal_bus.publish_to_module(
            target_module="ag-mem-20",
            event_type="write_entry",
            source_module="ag-mem-16",
            data=write_data,
        )
        self.internal_bus.process_all()
        print("  经验写入流程执行完成")


# ========== 程序入口 ==========
def main():
    print("=" * 70)
    print("  Agent-mlnf-mem 双漏斗记忆中枢 V1.0 最终稳定版")
    print("  原创提出者：文波福")
    print("=" * 70)

    agent = AgentMlnfMem()

    # 运行演示用例
    agent.demo_user_profile_flow()
    agent.demo_experience_write_flow()

    # 运行 3 个主循环周期
    print("\n运行 3 个主循环周期...")
    for i in range(3):
        agent.run_cycle()
        print(f"  周期 {i+1} 完成")

    # 打印系统健康状态
    health = agent.get_health_status()
    print(f"\n✅ Agent-mlnf-mem 演示完成")
    print(f"  总运行周期: {health['cycle_count']}")
    print(f"  待处理消息: 内部={health['internal_pending']}, 外部={health['external_pending']}")
    print(f"  已加载模块: {health['loaded_modules']}/51")


if __name__ == "__main__":
    main()