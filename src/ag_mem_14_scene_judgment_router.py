#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-14
模块名称: 任务场景判定与分槽路由单元
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 接收 ag-mem-03 下发的场景判定请求，基于任务描述、用户历史行为特征与当前上下文，
          判定当前任务所属的场景类别，并输出目标分槽编号。支持多场景匹配与置信度排序。
          不参与认知决策，仅执行场景判定与分槽推荐。

依赖模块: ag-mem-03, ag-mem-10
被依赖模块: ag-mem-03, ag-mem-15~19

安全约束:
  S-01: 仅基于任务描述元数据特征，不解析用户敏感内容
  S-02: 用户偏好数据仅用于置信度修正，不作为唯一依据
  S-03: 置信度低于阈值时返回通用任务
  S-04: 不持久化用户数据

版本: V1.0 (最终修复版 · 全日志 · 熔断安全 · 规范对齐)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class RouterState(Enum):
    IDLE = "idle"
    FEATURE_EXTRACT = "feature_extract"
    RULE_MATCH = "rule_match"
    RESULT_OUTPUT = "result_output"
    SYSTEM_PAUSED = "system_paused"


class SceneCategory(Enum):
    DIALOGUE = "对话交互"
    TOOL_CALL = "工具调用"
    SEARCH = "信息检索"
    CREATION = "创作生成"
    GENERAL = "通用任务"


SCENE_TO_SLOT_MAP = {
    SceneCategory.DIALOGUE: "ag-mem-15",
    SceneCategory.TOOL_CALL: "ag-mem-16",
    SceneCategory.SEARCH: "ag-mem-17",
    SceneCategory.CREATION: "ag-mem-18",
    SceneCategory.GENERAL: "ag-mem-19",
}


class SceneJudgmentRouter:
    module_id = "ag-mem-14"
    module_name = "任务场景判定与分槽路由单元"
    version = "V1.0"

    # 关键词库（扩展后）
    CREATION_KEYWORDS = ["写", "生成", "创作", "画", "翻译", "总结", "编写", "制作", "撰写", "设计"]
    SEARCH_KEYWORDS = ["搜索", "查找", "什么是", "如何", "最新", "查询", "找", "检索", "科普"]
    TOOL_KEYWORDS = ["执行", "调用", "运行", "API", "操作文件", "工具", "插件", "脚本"]
    DIALOGUE_KEYWORDS = ["聊天", "问候", "闲聊", "你好", "谢谢", "再见", "今天", "天气", "讲个笑话", "推荐"]

    DEFAULT_CONFIDENCE = {
        SceneCategory.DIALOGUE: 0.75,
        SceneCategory.TOOL_CALL: 0.85,
        SceneCategory.SEARCH: 0.80,
        SceneCategory.CREATION: 0.80,
        SceneCategory.GENERAL: 0.50,
    }

    SECONDARY_SCENE_THRESHOLD = 0.1
    MIN_CONFIDENCE_THRESHOLD = 0.3
    USER_PREFERENCE_BOOST = 0.1
    USER_PREFERENCE_PENALTY = 0.05
    HIGH_FREQ_SCENE_RATIO = 0.5

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = RouterState.IDLE
        self._judgment_count: int = 0
        self._scene_hit_count: Dict[SceneCategory, int] = {s: 0 for s in SceneCategory}
        self._pending_judgment: Optional[Dict[str, Any]] = None
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.scene_judgment_main_loop()

    def scene_judgment_main_loop(self):
        if self.state == RouterState.SYSTEM_PAUSED:
            return
        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-14.scene_judgment_request":
            self._handle_scene_request(msg)
            return

        # 异步返回的用户偏好摘要
        if msg.topic == "ag-mem-14.preference_summary":
            self._handle_preference_response(msg)
            return

    def _handle_scene_request(self, msg: Message):
        data = msg.data
        task_desc = data.get("task_description", "")
        need_pref = data.get("need_user_preference_assist", False)
        request_id = data.get("request_id", "")

        self.state = RouterState.FEATURE_EXTRACT
        self._log_event("JUDGMENT_START", {"request_id": request_id, "task_length": len(task_desc)})

        # 直接使用字符串包含判断替代无效的正则分词
        keywords = self._extract_matched_keywords(task_desc)
        has_tool = self._has_tool_indicator(task_desc)

        self.state = RouterState.RULE_MATCH
        candidates = self._match_scenes(keywords, has_tool)

        if need_pref and self.bus:
            self._pending_judgment = {
                "msg": msg,
                "candidates": candidates
            }
            self.bus.publish_to_module(
                target_module="ag-mem-10",
                event_type="summary_query",
                source_module=self.module_id,
                data={"_correlation_id": msg.correlation_id}
            )
            self._log_event("PREFERENCE_REQUESTED", {"request_id": request_id})
            return

        result = self._finalize_judgment(request_id, candidates)
        self._send_result(msg, result)
        self.state = RouterState.IDLE

    def _handle_preference_response(self, msg: Message):
        if not self._pending_judgment:
            self._log_event("PREFERENCE_IGNORED", {"reason": "无待处理请求"})
            return

        pending = self._pending_judgment
        self._pending_judgment = None
        request_id = pending["msg"].data.get("request_id", "")

        # 从偏好摘要中提取场景分布
        scene_dist = msg.data.get("by_scene_label", {})
        self._apply_preference_boost(pending["candidates"], scene_dist)

        result = self._finalize_judgment(request_id, pending["candidates"])
        self._send_result(pending["msg"], result)
        self._log_event("PREFERENCE_APPLIED", {"request_id": request_id})
        self.state = RouterState.IDLE

    def _extract_matched_keywords(self, text: str) -> List[str]:
        """返回所有在文本中出现的关键词（直接字符串包含）"""
        if not text:
            return []
        matched = []
        for kw in (self.CREATION_KEYWORDS + self.SEARCH_KEYWORDS +
                   self.TOOL_KEYWORDS + self.DIALOGUE_KEYWORDS):
            if kw in text:
                matched.append(kw)
        return matched

    def _has_tool_indicator(self, text: str) -> bool:
        return any(kw in text for kw in self.TOOL_KEYWORDS)

    def _match_scenes(self, matched_keywords: List[str], has_tool: bool) -> List[Dict[str, Any]]:
        candidates = []
        for scene in [SceneCategory.DIALOGUE, SceneCategory.TOOL_CALL,
                      SceneCategory.SEARCH, SceneCategory.CREATION]:
            scene_kws = self._get_scene_keywords(scene)
            hit = [kw for kw in matched_keywords if kw in scene_kws]
            score = len(hit)
            basis = []
            if hit:
                basis.append(f"关键词: {hit}")
            # 工具名称特殊加分
            if scene == SceneCategory.TOOL_CALL and has_tool:
                score += 3
                basis.append("检测到工具指令")
            if score > 0:
                conf = min(self.DEFAULT_CONFIDENCE[scene] + score * 0.05, 1.0)
                candidates.append({
                    "scene": scene,
                    "slot_id": SCENE_TO_SLOT_MAP[scene],
                    "confidence": conf,
                    "basis": ", ".join(basis)
                })
        return candidates

    def _get_scene_keywords(self, scene: SceneCategory) -> List[str]:
        if scene == SceneCategory.CREATION:
            return self.CREATION_KEYWORDS
        elif scene == SceneCategory.SEARCH:
            return self.SEARCH_KEYWORDS
        elif scene == SceneCategory.TOOL_CALL:
            return self.TOOL_KEYWORDS
        elif scene == SceneCategory.DIALOGUE:
            return self.DIALOGUE_KEYWORDS
        return []

    def _apply_preference_boost(self, candidates: List[Dict[str, Any]], scene_dist: Dict[str, Any]):
        total = sum(
            s.get("total_count", 0) if isinstance(s, dict) else 0
            for s in scene_dist.values()
        )
        if total <= 0:
            return

        for candidate in candidates:
            scene_name = candidate["scene"].value
            stats = scene_dist.get(scene_name, {})
            cnt = stats.get("total_count", 0) if isinstance(stats, dict) else 0
            ratio = cnt / total
            if ratio > self.HIGH_FREQ_SCENE_RATIO:
                candidate["confidence"] += self.USER_PREFERENCE_BOOST
                candidate["basis"] += ", 用户高频场景+0.1"
            elif ratio == 0.0:
                candidate["confidence"] -= self.USER_PREFERENCE_PENALTY
                candidate["basis"] += ", 用户历史无此场景-0.05"

    def _finalize_judgment(self, request_id: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        candidates.sort(key=lambda x: x["confidence"], reverse=True)
        if not candidates or candidates[0]["confidence"] < self.MIN_CONFIDENCE_THRESHOLD:
            self._log_event("JUDGMENT_FALLBACK_GENERAL", {"request_id": request_id})
            return {
                "request_id": request_id,
                "primary_scene": SceneCategory.GENERAL.value,
                "confidence": 0.3,
                "target_slot_id": SCENE_TO_SLOT_MAP[SceneCategory.GENERAL],
                "secondary_scenes": [],
                "judgment_basis": "无法匹配任何场景规则"
            }
        primary = candidates[0]
        secondary = []
        for c in candidates[1:]:
            if primary["confidence"] - c["confidence"] < self.SECONDARY_SCENE_THRESHOLD:
                secondary.append({
                    "scene": c["scene"].value,
                    "confidence": c["confidence"],
                    "slot_id": c["slot_id"]
                })
            else:
                break

        self._judgment_count += 1
        self._scene_hit_count[primary["scene"]] += 1

        self._log_event("JUDGMENT_COMPLETE", {
            "request_id": request_id,
            "scene": primary["scene"].value,
            "confidence": round(primary["confidence"], 2)
        })

        return {
            "request_id": request_id,
            "primary_scene": primary["scene"].value,
            "confidence": primary["confidence"],
            "target_slot_id": primary["slot_id"],
            "secondary_scenes": secondary,
            "judgment_basis": primary.get("basis", "规则匹配")
        }

    def _send_result(self, original_msg: Message, result: Dict[str, Any]):
        if self.bus:
            self.bus.publish(
                topic=f"{original_msg.source_module}.scene_judgment_result",
                source_module=self.module_id,
                data=result,
                target_module=original_msg.source_module,
                correlation_id=original_msg.correlation_id
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = RouterState.SYSTEM_PAUSED
        self._pending_judgment = None
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp