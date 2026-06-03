#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-04
模块名称: 用户身份识别单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 融合设备指纹、用户登录凭证、声纹特征等多模态信号，在每次用户会话开始时
          确认用户身份。将识别结果（已知用户ID + 置信度 + 是否新用户标记）上报至
          ag-mem-02，由调度单元决定匹配已有画像槽或创建新槽。支持长期用户、临时用户
          和访客三种身份模式的识别与区分。不参与任何认知决策，仅提供身份识别结果。

依赖模块:
    设备指纹采集模块, 外部认证服务, 声纹识别引擎(可选)
被依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-07(用户行为观测记录单元)

安全约束:
  S-01: 声纹特征向量仅用于本地匹配，原始音频数据不存储、不上传
  S-02: 设备指纹数据仅用于用户关联，不共享给漏斗二或任何外部模块
  S-03: 访客模式的用户ID不关联任何持久化数据，会话结束后自动失效
  S-04: 身份识别结果仅向 ag-mem-02 输出，不得被其他模块直接访问
  S-05: 识别置信度低于0.5时，必须明确标记为低置信度，下游模块应限制敏感操作
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


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


@dataclass
class DeviceFingerprint:
    device_id: str = ""
    browser_fingerprint: str = ""
    os_type: str = ""
    screen_resolution: str = ""
    timezone: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class LoginCredential:
    user_id: str = ""
    auth_token: str = ""
    is_valid: bool = False
    expires_at: float = field(default_factory=time.time)


@dataclass
class VoiceFeature:
    feature_vector: List[float] = field(default_factory=list)
    quality: float = 0.0
    noise_level: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class SessionSignal:
    session_id: str = ""
    access_method: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class IdentityResult:
    user_id: str = ""
    recognition_method: RecognitionMethod = RecognitionMethod.NO_SIGNAL
    confidence: float = 0.0
    is_new_user: bool = True
    recommended_slot_type: str = "访客槽"
    timestamp: float = field(default_factory=time.time)


class UserIdentityRecognizer:
    LOGIN_CONFIDENCE = 0.98
    DEVICE_MATCH_CONFIDENCE = 0.85
    VOICE_MATCH_CONFIDENCE = 0.75
    DEVICE_NEW_CONFIDENCE = 0.50
    GUEST_CONFIDENCE = 0.0
    SESSION_TIMEOUT_SEC = 1800  # 30分钟会话超时

    def __init__(self):
        self.module_id = "ag-mem-04"
        self.module_name = "用户身份识别单元"
        self.version = "V1.0"

        self.state = IdentityState.WAITING_SESSION
        self._device_user_map: Dict[str, str] = {}  # device_id -> user_id
        self._voice_template_db: Dict[str, List[float]] = {}  # user_id -> voice template
        self._last_identity_result: Optional[IdentityResult] = None
        self._last_identify_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_session_signal = None
        self._query_device_fingerprint = None
        self._query_login_credential = None
        self._query_voice_feature = None
        self._query_identity_request = None

        self._publish_identity_result = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_session_signal_query(self, callback: Callable[[], Optional[SessionSignal]]):
        self._query_session_signal = callback

    def set_device_fingerprint_query(self, callback: Callable[[], Optional[DeviceFingerprint]]):
        self._query_device_fingerprint = callback

    def set_login_credential_query(self, callback: Callable[[], Optional[LoginCredential]]):
        self._query_login_credential = callback

    def set_voice_feature_query(self, callback: Callable[[], Optional[VoiceFeature]]):
        self._query_voice_feature = callback

    def set_identity_request_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_identity_request = callback

    def set_identity_result_publisher(self, callback: Callable[[IdentityResult], None]):
        self._publish_identity_result = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_recognition_cycle(self) -> Optional[IdentityResult]:
        now = time.time()

        if self.state == IdentityState.SYSTEM_PAUSED:
            return None

        # 响应外部身份查询
        request = self._query_identity_request() if self._query_identity_request else None
        if request:
            if self._last_identity_result and (now - self._last_identify_time) < self.SESSION_TIMEOUT_SEC:
                # 返回缓存的识别结果
                if self._publish_identity_result:
                    self._publish_identity_result(self._last_identity_result)
                return self._last_identity_result
            else:
                # 身份已过期，触发重新识别
                return None

        # 检测新会话
        session = self._query_session_signal() if self._query_session_signal else None
        if session is None:
            return None

        self.state = IdentityState.IDENTIFYING
        result = self._perform_identification(session)
        self.state = IdentityState.IDENTIFIED

        self._last_identity_result = result
        self._last_identify_time = now

        if self._publish_identity_result:
            self._publish_identity_result(result)

        self._log_event("IDENTITY_RESULT", {
            "user_id": result.user_id,
            "method": result.recognition_method.value,
            "confidence": result.confidence
        })

        self.state = IdentityState.WAITING_SESSION
        return result

    def _perform_identification(self, session: SessionSignal) -> IdentityResult:
        # 优先级1：登录凭证
        login = self._query_login_credential() if self._query_login_credential else None
        if login and login.is_valid:
            # 将设备与用户关联
            device = self._query_device_fingerprint() if self._query_device_fingerprint else None
            if device and device.device_id:
                self._device_user_map[device.device_id] = login.user_id

            return IdentityResult(
                user_id=login.user_id,
                recognition_method=RecognitionMethod.LOGIN_CREDENTIAL,
                confidence=self.LOGIN_CONFIDENCE,
                is_new_user=False,
                recommended_slot_type="长期槽"
            )

        # 优先级2：设备指纹匹配
        device = self._query_device_fingerprint() if self._query_device_fingerprint else None
        if device and device.device_id and device.device_id in self._device_user_map:
            matched_user = self._device_user_map[device.device_id]
            return IdentityResult(
                user_id=matched_user,
                recognition_method=RecognitionMethod.DEVICE_FINGERPRINT,
                confidence=self.DEVICE_MATCH_CONFIDENCE,
                is_new_user=False,
                recommended_slot_type="长期槽"
            )

        if device and device.device_id:
            # 设备存在但未关联用户
            temp_user_id = f"TEMP_{uuid.uuid4().hex[:8]}"
            self._device_user_map[device.device_id] = temp_user_id
            return IdentityResult(
                user_id=temp_user_id,
                recognition_method=RecognitionMethod.DEVICE_FINGERPRINT,
                confidence=self.DEVICE_NEW_CONFIDENCE,
                is_new_user=True,
                recommended_slot_type="临时槽"
            )

        # 优先级3：声纹特征匹配
        voice = self._query_voice_feature() if self._query_voice_feature else None
        if voice and voice.quality > 0.6:
            matched_user = self._match_voice(voice.feature_vector)
            if matched_user:
                return IdentityResult(
                    user_id=matched_user,
                    recognition_method=RecognitionMethod.VOICE_PRINT,
                    confidence=self.VOICE_MATCH_CONFIDENCE,
                    is_new_user=False,
                    recommended_slot_type="长期槽"
                )
            else:
                # 声纹不匹配，创建临时用户
                temp_user_id = f"TEMP_{uuid.uuid4().hex[:8]}"
                return IdentityResult(
                    user_id=temp_user_id,
                    recognition_method=RecognitionMethod.VOICE_PRINT,
                    confidence=0.4,
                    is_new_user=True,
                    recommended_slot_type="临时槽"
                )

        # 无可用信号
        self.state = IdentityState.INSUFFICIENT_SIGNAL
        guest_id = f"GUEST_{session.session_id}"
        return IdentityResult(
            user_id=guest_id,
            recognition_method=RecognitionMethod.NO_SIGNAL,
            confidence=self.GUEST_CONFIDENCE,
            is_new_user=True,
            recommended_slot_type="访客槽"
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

    def register_user_device(self, user_id: str, device_id: str):
        self._device_user_map[device_id] = user_id

    def register_voice_template(self, user_id: str, feature_vector: List[float]):
        self._voice_template_db[user_id] = feature_vector

    def get_state(self) -> IdentityState:
        return self.state

    def emergency_shutdown(self):
        self.state = IdentityState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 用户身份识别单元 (ag-mem-04) 演示")
    print("=" * 70)

    recognizer = UserIdentityRecognizer()
    recognizer.set_session_signal_query(lambda: SessionSignal(session_id="S001", access_method="web"))

    print_separator("STEP 1: 登录凭证识别")
    recognizer.set_login_credential_query(lambda: LoginCredential(
        user_id="U001", auth_token="valid_token", is_valid=True
    ))
    recognizer.set_device_fingerprint_query(lambda: DeviceFingerprint(device_id="DEV-A"))
    result = recognizer.run_recognition_cycle()
    if result:
        print(f"  用户ID: {result.user_id}")
        print(f"  识别方式: {result.recognition_method.value}")
        print(f"  置信度: {result.confidence}")
        print(f"  推荐槽位: {result.recommended_slot_type}")

    print_separator("STEP 2: 设备指纹匹配（已知设备）")
    recognizer.set_login_credential_query(lambda: None)
    recognizer.set_device_fingerprint_query(lambda: DeviceFingerprint(device_id="DEV-A"))
    recognizer.set_session_signal_query(lambda: SessionSignal(session_id="S002"))
    result = recognizer.run_recognition_cycle()
    if result:
        print(f"  用户ID: {result.user_id}")
        print(f"  识别方式: {result.recognition_method.value}")
        print(f"  置信度: {result.confidence}")

    print_separator("STEP 3: 全新访客（无任何信号）")
    recognizer.set_device_fingerprint_query(lambda: None)
    recognizer.set_voice_feature_query(lambda: VoiceFeature(quality=0.2))
    recognizer.set_session_signal_query(lambda: SessionSignal(session_id="S003"))
    result = recognizer.run_recognition_cycle()
    if result:
        print(f"  用户ID: {result.user_id}")
        print(f"  识别方式: {result.recognition_method.value}")
        print(f"  置信度: {result.confidence}")
        print(f"  推荐槽位: {result.recommended_slot_type}")

    print("\n✅ 用户身份识别单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-04 用户身份识别单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_recognizer():
            r = UserIdentityRecognizer()
            r.set_session_signal_query(lambda: SessionSignal(session_id="S_TEST"))
            return r

        # TC-M04-01: 登录凭证识别
        print("\n[TC-M04-01] 登录凭证识别")
        try:
            r = setup_recognizer()
            r.set_login_credential_query(lambda: LoginCredential(user_id="U001", is_valid=True))
            result = r.run_recognition_cycle()
            assert result is not None
            assert result.confidence >= 0.95
            assert result.recognition_method == RecognitionMethod.LOGIN_CREDENTIAL
            assert not result.is_new_user
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M04-02: 设备指纹匹配已知用户
        print("\n[TC-M04-02] 设备指纹匹配已知用户")
        try:
            r = setup_recognizer()
            r.register_user_device("U002", "DEV-X")
            r.set_device_fingerprint_query(lambda: DeviceFingerprint(device_id="DEV-X"))
            r.set_login_credential_query(lambda: None)
            result = r.run_recognition_cycle()
            assert result is not None
            assert result.user_id == "U002"
            assert result.confidence >= 0.8
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M04-03: 全新设备（无关联用户）
        print("\n[TC-M04-03] 全新设备（无关联用户）")
        try:
            r = setup_recognizer()
            r.set_device_fingerprint_query(lambda: DeviceFingerprint(device_id="NEW-DEV"))
            r.set_login_credential_query(lambda: None)
            result = r.run_recognition_cycle()
            assert result is not None
            assert result.is_new_user
            assert result.confidence == 0.5
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M04-04: 无任何信号（访客模式）
        print("\n[TC-M04-04] 无任何信号（访客模式）")
        try:
            r = setup_recognizer()
            r.set_device_fingerprint_query(lambda: None)
            r.set_voice_feature_query(lambda: None)
            r.set_login_credential_query(lambda: None)
            result = r.run_recognition_cycle()
            assert result is not None
            assert result.confidence == 0.0
            assert "GUEST" in result.user_id
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M04-05: 声纹匹配成功
        print("\n[TC-M04-05] 声纹匹配成功")
        try:
            r = setup_recognizer()
            template = [1.0, 0.5, 0.8]
            r.register_voice_template("U003", template)
            r.set_voice_feature_query(lambda: VoiceFeature(feature_vector=[1.0, 0.5, 0.8], quality=0.8))
            r.set_device_fingerprint_query(lambda: None)
            r.set_login_credential_query(lambda: None)
            result = r.run_recognition_cycle()
            assert result is not None
            assert result.user_id == "U003"
            assert result.confidence >= 0.7
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M04-06: 紧急熔断
        print("\n[TC-M04-06] 紧急熔断")
        try:
            r = setup_recognizer()
            r.emergency_shutdown()
            assert r.state == IdentityState.SYSTEM_PAUSED
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