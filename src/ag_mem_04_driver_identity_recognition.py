#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-04
模块名称: 用户身份识别单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 融合设备指纹、用户登录凭证、声纹特征等多模态信号，在每次用户会话开始时
          确认用户身份。将识别结果（已知用户ID + 置信度 + 是否新用户标记）通过
          InternalBus 上报至 ag-mem-02，由调度单元决定匹配已有画像槽或创建新槽。
          支持长期用户、临时用户和访客三种身份模式的识别与区分。
          不参与任何认知决策，仅提供身份识别结果。

依赖模块:
    设备指纹采集模块(外部), 外部认证服务, 声纹识别引擎(可选)
被依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-07(用户行为观测记录单元)

安全约束:
  S-01: 声纹特征向量仅用于本地匹配，原始音频数据不存储、不上传
  S-02: 设备指纹数据仅用于用户关联，不共享给漏斗二或任何外部模块
  S-03: 访客模式的用户ID不关联任何持久化数据，会话结束后自动失效
  S-04: 身份识别结果仅向 ag-mem-02 输出，不得被其他模块直接访问
  S-05: 识别置信度低于0.5时，必须明确标记为低置信度，下游模块应限制敏感操作

版本: V1.0 (总线集成版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class IdentityState(Enum):
    WAITING_SESSION = "waiting_session"
    IDENTIFYING = "identifying"
    IDENTIFIED = "identified"
    INSUFFICIENT_SIGNAL = "insufficient_signal"
    SYSTEM_PAUSED = "system_paused"


class RecognitionMethod(Enum):
    LOGIN_CREDENTIAL = "登录凭证"
    DEVICE_FINGERPRINT = "设备指纹"
    VOICE_PRINT = "声纹特征"
    NO_SIGNAL = "无可用信号"


class UserIdentityRecognizer:
    module_id = "ag-mem-04"
    module_name = "用户身份识别单元"
    version = "V1.0"

    LOGIN_CONFIDENCE = 0.98
    DEVICE_MATCH_CONFIDENCE = 0.85
    VOICE_MATCH_CONFIDENCE = 0.75
    DEVICE_NEW_CONFIDENCE = 0.50
    GUEST_CONFIDENCE = 0.0
    SESSION_TIMEOUT_SEC = 1800

    def __init__(self):
        self.bus: Optional[InternalBus] = None          # 由主入口注入

        self.state = IdentityState.WAITING_SESSION
        self._device_user_map: Dict[str, str] = {}
        self._voice_template_db: Dict[str, List[float]] = {}
        self._last_identity_result: Optional[Dict[str, Any]] = None
        self._last_identify_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 外部数据源（由主入口通过总线消息注入，此处仅作缓存）
        self._latest_device_fingerprint: Optional[Dict[str, Any]] = None
        self._latest_login_credential: Optional[Dict[str, Any]] = None
        self._latest_voice_feature: Optional[Dict[str, Any]] = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def user_identity_main_loop(self):
        """主循环，由主入口周期性调用"""
        if self.state == IdentityState.SYSTEM_PAUSED:
            return

        # 处理总线消息，接收来自 ag-mem-01 / ag-mem-02 的请求，以及外部数据注入
        if self.bus:
            self.bus.process_batch(10)

        # 周期性状态上报（可选）
        self._report_status()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        """接收 InternalBus 发往本模块的消息"""
        if not isinstance(msg.data, dict):
            return

        # 处理身份识别请求（来自 ag-mem-01 或 ag-mem-02）
        if msg.topic == "ag-mem-04.identity_query":
            self._handle_identity_query(msg)
            return

        # 注入设备指纹数据
        if msg.topic == "ag-mem-04.device_fingerprint":
            self._latest_device_fingerprint = msg.data
            return

        # 注入登录凭证
        if msg.topic == "ag-mem-04.login_credential":
            self._latest_login_credential = msg.data
            return

        # 注入声纹特征
        if msg.topic == "ag-mem-04.voice_feature":
            self._latest_voice_feature = msg.data
            return

        # 响应冷启动检查
        if msg.topic == "ag-mem-04.init_check":
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-02",
                    event_type="internal_status",
                    source_module=self.module_id,
                    data={"available": True}
                )

    def _handle_identity_query(self, msg: Message):
        """接收身份识别请求，执行多模态融合，返回结果"""
        if self.state == IdentityState.SYSTEM_PAUSED:
            return

        # 判断是否可以使用缓存的识别结果
        now = time.time()
        if self._last_identity_result and (now - self._last_identify_time) < self.SESSION_TIMEOUT_SEC:
            # 会话未过期，直接返回缓存结果
            self._reply_identity(msg, self._last_identity_result)
            return

        # 执行多模态识别
        result = self._perform_identification(msg.data)
        self._last_identity_result = result
        self._last_identify_time = now

        # 发布识别结果给 ag-mem-02
        self._reply_identity(msg, result)

        # 记录日志
        self._log_event("IDENTITY_RESULT", result)

    def _perform_identification(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行多模态身份识别，返回标准结果字典"""
        # 融合外源数据：请求中可能包含设备、登录凭证等，也可以使用总线注入的全局数据
        device = request_data.get("device_fingerprint") or self._latest_device_fingerprint
        login = request_data.get("login_credential") or self._latest_login_credential
        voice = request_data.get("voice_feature") or self._latest_voice_feature

        # 优先级1：登录凭证
        if login and isinstance(login, dict) and login.get("auth_token") and login.get("user_id"):
            user_id = login["user_id"]
            # 关联设备
            if device and isinstance(device, dict) and device.get("device_id"):
                self._device_user_map[device["device_id"]] = user_id
            return {
                "user_id": user_id,
                "confidence": self.LOGIN_CONFIDENCE,
                "recognition_method": RecognitionMethod.LOGIN_CREDENTIAL.value,
                "is_new_user": False,
                "recommended_slot_type": "长期槽"
            }

        # 优先级2：设备指纹匹配
        if device and isinstance(device, dict) and device.get("device_id"):
            device_id = device["device_id"]
            if device_id in self._device_user_map:
                return {
                    "user_id": self._device_user_map[device_id],
                    "confidence": self.DEVICE_MATCH_CONFIDENCE,
                    "recognition_method": RecognitionMethod.DEVICE_FINGERPRINT.value,
                    "is_new_user": False,
                    "recommended_slot_type": "长期槽"
                }
            else:
                # 新设备，暂未关联用户
                temp_user_id = f"TEMP_{uuid.uuid4().hex[:8]}"
                self._device_user_map[device_id] = temp_user_id
                return {
                    "user_id": temp_user_id,
                    "confidence": self.DEVICE_NEW_CONFIDENCE,
                    "recognition_method": RecognitionMethod.DEVICE_FINGERPRINT.value,
                    "is_new_user": True,
                    "recommended_slot_type": "临时槽"
                }

        # 优先级3：声纹特征匹配
        if voice and isinstance(voice, dict) and voice.get("feature_vector"):
            feature = voice["feature_vector"]
            quality = voice.get("quality", 0.0)
            if quality > 0.6:
                matched_user = self._match_voice(feature)
                if matched_user:
                    return {
                        "user_id": matched_user,
                        "confidence": self.VOICE_MATCH_CONFIDENCE,
                        "recognition_method": RecognitionMethod.VOICE_PRINT.value,
                        "is_new_user": False,
                        "recommended_slot_type": "长期槽"
                    }
                else:
                    temp_user_id = f"TEMP_{uuid.uuid4().hex[:8]}"
                    return {
                        "user_id": temp_user_id,
                        "confidence": 0.4,
                        "recognition_method": RecognitionMethod.VOICE_PRINT.value,
                        "is_new_user": True,
                        "recommended_slot_type": "临时槽"
                    }

        # 无任何信号
        self.state = IdentityState.INSUFFICIENT_SIGNAL
        guest_id = f"GUEST_{uuid.uuid4().hex[:8]}"
        return {
            "user_id": guest_id,
            "confidence": self.GUEST_CONFIDENCE,
            "recognition_method": RecognitionMethod.NO_SIGNAL.value,
            "is_new_user": True,
            "recommended_slot_type": "访客槽"
        }

    def _reply_identity(self, original_msg: Message, result: Dict[str, Any]):
        """向 ag-mem-02 发送身份识别结果"""
        if not self.bus:
            return
        self.bus.publish(
            topic="ag-mem-02.identity_result",
            source_module=self.module_id,
            data=result,
            target_module="ag-mem-02",
            correlation_id=original_msg.correlation_id
        )

    def _match_voice(self, feature_vector: List[float]) -> Optional[str]:
        if not feature_vector:
            return None
        best_match = None
        best_similarity = 0.0
        for user_id, template in self._voice_template_db.items():
            similarity = self._cosine_similarity(feature_vector, template)
            if similarity > best_similarity and similarity > 0.7:
                best_similarity = similarity
                best_match = user_id
        return best_match

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ====================== 管理接口 ======================
    def register_user_device(self, user_id: str, device_id: str):
        """手动注册设备与用户的关联"""
        self._device_user_map[device_id] = user_id

    def register_voice_template(self, user_id: str, feature_vector: List[float]):
        """注册用户的声纹模板"""
        self._voice_template_db[user_id] = feature_vector

    def emergency_shutdown(self):
        self.state = IdentityState.SYSTEM_PAUSED
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _report_status(self):
        """周期向 ag-mem-01 上报自身状态（可选）"""
        if not self.bus:
            return
        self.bus.publish_to_module(
            target_module="ag-mem-01",
            event_type="internal_status",
            source_module=self.module_id,
            data={"available": True}
        )

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

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs