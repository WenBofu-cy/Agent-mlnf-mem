#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent-mlnf-mem 双漏斗记忆中枢 · 主入口
版本：V1.0
原创提出者：文波福
开源协议：CC BY-NC 4.0

职责：
  - 实例化全部 51 个模块
  - 通过 MemoryBus 完成模块间的回调注入
  - 实现主循环：逐模块调用 run_xxx_cycle()，总线负责消息路由
  - 提供端到端演示场景
"""

import time
import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_bus import MemoryBus

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
    """
    MLNF-Mem 双漏斗记忆中枢 主控类
    负责模块实例化、回调绑定与主循环调度
    """

    def __init__(self):
        self.bus = MemoryBus()
        self.cycle_count = 0
        self.running = True

        # ========== 实例化模块 ==========
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

        # 分区三：漏斗二——场景分槽管理
        self.scene_router = SceneJudgmentRouter()
        self.dialogue_slot = DialogueSlot()
        self.tool_slot = ToolCallSlot()
        self.search_slot = SearchSlot()
        self.creation_slot = CreationSlot()
        self.general_slot = GeneralSlot()

        # 分区三：漏斗二——五层存储
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

        # 分区三：漏斗二——三维重要度计算引擎
        self.s_calc = SValueCalculator()
        self.v_calc = VValueCalculator()
        self.c_stats = CValueStatistics()
        self.i0_assign = I0AssignmentUnit()
        self.weight_config = WeightConfigManager()
        self.i_aggregator = IValueAggregator()
        self.i_refresher = IValueRefreshScheduler()

        # 分区三：漏斗二——晋升与遗忘执行机制
        self.promotion_judge = PromotionConditionJudge()
        self.layer_transfer = LayerTransferUnit()
        self.forget_judge = ForgetThresholdJudge()
        self.min_usage_validator = MinUsageValidator()
        self.redundant_pruner = RedundantMemoryPruner()
        self.failure_arbitration = FailureArbitrationUnit()

        # 分区四：漏斗外挂扩展区
        self.knowledge_base = IndependentKnowledgeBase()
        self.safety_rules = SafetyRuleBase()
        self.emotion_engine = EmotionIntentEngine()
        self.question_cache = QuestionCache()

        # 分区五：存储与系统运维
        self.quota_controller = GlobalQuotaController()
        self.cold_storage = ColdStorageManager()
        self.import_export = MemoryImportExportUnit()
        self.change_log = ChangeLogTracer()

        # 执行回调绑定
        self._wire_callbacks()

        print("Agent-mlnf-mem 双漏斗记忆中枢 初始化完成")
        print(f"  模块总数: 51 (已加载: {self._loaded_module_count()})")

    def _loaded_module_count(self) -> int:
        """统计已加载的模块数"""
        count = 0
        for attr_name in dir(self):
            if attr_name.startswith('_'):
                continue
            attr = getattr(self, attr_name)
            if hasattr(attr, 'module_id'):
                count += 1
        return count

    # ========== 回调绑定 ==========
    def _wire_callbacks(self):
        """通过 MemoryBus 完成模块间的回调注入"""
        # 此方法需要根据每个模块的 spec 文档逐一绑定
        # 由于代码量巨大，这里展示核心绑定逻辑，实际使用时需完整实现

        # 例：ag-mem-01 向 ag-mem-02 发送调度指令
        self.bus.subscribe_to_module("ag-mem-02", lambda msg: self._handle_f1_message(msg))
        self.bus.subscribe_to_module("ag-mem-03", lambda msg: self._handle_f2_message(msg))

        # 例：ag-mem-04 向 ag-mem-02 返回身份识别结果
        self.bus.subscribe_to_module("ag-mem-02", lambda msg: None)  # 占位

        # 例：ag-mem-07 向 ag-mem-06 请求写入令牌
        self.bus.subscribe_to_module("ag-mem-06", lambda msg: self._handle_isolation_gate(msg))

        # 更多绑定将根据实际需要扩展...
        # 当前阶段，模块内部通过 _query_xxx / _publish_xxx 回调直接注入，
        # 总线负责消息路由，回调在模块初始化时通过 set_ 方法注入

    def _handle_f1_message(self, msg):
        """处理发送给漏斗一调度单元的消息"""
        pass

    def _handle_f2_message(self, msg):
        """处理发送给漏斗二调度单元的消息"""
        pass

    def _handle_isolation_gate(self, msg):
        """处理发送给隔离管控单元的消息"""
        pass

    # ========== 主循环 ==========
    def run_cycle(self):
        """执行一个主循环周期"""
        # 1. 处理总线消息
        self.bus.process_batch(20)

        # 2. 按分区顺序调用模块的 run_xxx_cycle()
        # 分区一：顶层总控中枢
        self.f0.run_dispatch_cycle()
        self.f1_dispatcher.run_dispatcher_cycle()
        self.f2_dispatcher.run_dispatcher_cycle()

        # 分区二：漏斗一——用户画像漏斗
        self.identity_recognizer.run_recognition_cycle()
        self.slot_creator.run_creation_cycle()
        self.isolation_gate.run_isolation_cycle()
        self.behavior_recorder.run_observation_cycle()
        self.scene_marker.run_marker_cycle()
        self.preference_judge.run_judgment_cycle()
        self.preference_stats.run_statistics_cycle()
        self.suggestion_generator.run_generation_cycle()
        self.temp_cleanup.run_cleanup_cycle()
        self.inactive_reminder.run_reminder_cycle()

        # 分区三：场景分槽
        self.scene_router.run_judgment_cycle()
        self.dialogue_slot.run_slot_cycle()
        self.tool_slot.run_slot_cycle()
        self.search_slot.run_slot_cycle()
        self.creation_slot.run_slot_cycle()
        self.general_slot.run_slot_cycle()

        # 分区三：五层存储
        self.l1_storage.run_storage_cycle()
        self.l1_decay.run_assessment_cycle()
        self.l2_storage.run_storage_cycle()
        self.l2_heat.run_statistics_cycle()
        self.l3_storage.run_storage_cycle()
        self.l3_merge.run_merge_cycle()
        self.l4_storage.run_storage_cycle()
        self.l4_refiner.run_refine_cycle()
        self.l5_storage.run_storage_cycle()
        self.l5_lock.run_control_cycle()
        self.l5_access.run_access_cycle()

        # 分区三：三维重要度
        self.s_calc.run_calculation_cycle()
        self.v_calc.run_calculation_cycle()
        self.c_stats.run_statistics_cycle()
        self.i0_assign.run_assignment_cycle()
        self.weight_config.run_config_cycle()
        self.i_aggregator.run_aggregation_cycle()
        self.i_refresher.run_scheduler_cycle()

        # 分区三：晋升与遗忘
        self.promotion_judge.run_judge_cycle()
        self.layer_transfer.run_transfer_cycle()
        self.forget_judge.run_judge_cycle()
        self.min_usage_validator.run_validator_cycle()
        self.redundant_pruner.run_pruner_cycle()
        self.failure_arbitration.run_arbitration_cycle()

        # 分区四：外挂扩展区
        self.knowledge_base.run_knowledge_cycle()
        self.safety_rules.run_rule_cycle()
        self.emotion_engine.run_engine_cycle()
        self.question_cache.run_cache_cycle()

        # 分区五：系统运维
        self.quota_controller.run_controller_cycle()
        self.cold_storage.run_manager_cycle()
        self.import_export.run_cycle()
        self.change_log.run_tracer_cycle()

        # 再次处理总线消息（处理模块间通信产生的消息）
        self.bus.process_batch(20)

        self.cycle_count += 1

    def run_forever(self, interval_sec: float = 0.01):
        """持续运行主循环"""
        print("启动主循环...")
        try:
            while self.running:
                self.run_cycle()
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n收到中断信号，正在安全关闭...")
            self.shutdown()

    def shutdown(self):
        """安全关闭所有模块"""
        self.running = False
        print("Agent-mlnf-mem 已关闭")

    def demo_user_profile_flow(self):
        """演示：用户画像流程"""
        print("\n" + "=" * 60)
        print("  演示：用户画像流程")
        print("=" * 60)

        # 1. 模拟用户登录
        from ag_mem_04_driver_identity_recognition import SessionSignal, LoginCredential, DeviceFingerprint

        self.identity_recognizer.set_login_credential_query(lambda: LoginCredential(
            user_id="U001", auth_token="valid", is_valid=True
        ))
        self.identity_recognizer.set_device_fingerprint_query(lambda: DeviceFingerprint(device_id="DEV-A"))
        self.identity_recognizer.set_session_signal_query(lambda: SessionSignal(session_id="S001", access_method="web"))

        self.identity_recognizer.run_recognition_cycle()
        result = self.identity_recognizer._last_identity_result
        if result:
            print(f"  身份识别结果: user_id={result.user_id}, 置信度={result.confidence}, 推荐槽位={result.recommended_slot_type}")

        print("  画像槽创建流程已触发")

    def demo_experience_write_flow(self):
        """演示：经验写入流程"""
        print("\n" + "=" * 60)
        print("  演示：经验写入流程")
        print("=" * 60)

        from ag_mem_20_l1_temporary_storage import L1WriteRequest, ExperienceEntry

        entry = ExperienceEntry(
            experience_data={"tool": "weather_api", "result": "success"},
            i_value=0.5, s_value=0.3, v_value=0.4, c_value=0.0
        )
        self.l1_storage.set_write_request_query(lambda: L1WriteRequest(
            request_id="WR-001", source_slot_id="ag-mem-16", entry=entry
        ))
        result = self.l1_storage.run_storage_cycle()
        if result:
            print(f"  L1 写入成功, 条目ID={result.entry_id}, 使用率={result.l1_usage_pct:.2%}")

        print(f"  L1 当前条目数: {self.l1_storage.get_entry_count()}")


# ========== 演示入口 ==========
def main():
    print("=" * 70)
    print("  Agent-mlnf-mem 双漏斗记忆中枢 V1.0")
    print("  原创提出者：文波福")
    print("=" * 70)

    agent = AgentMlnfMem()

    agent.demo_user_profile_flow()
    agent.demo_experience_write_flow()

    print("\n运行 3 个主循环周期...")
    for i in range(3):
        agent.run_cycle()
        print(f"  周期 {i+1} 完成")

    print("\n✅ Agent-mlnf-mem 演示完成")
    print(f"  总周期数: {agent.cycle_count}")


if __name__ == "__main__":
    main()