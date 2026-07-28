"""프롬프트 평가 — 채점 (설계 C단계).

`ground_truth.yaml`의 시나리오별 기대 type/service와 예측 RcaResult를 비교한다.
  - type    : 정형화 후 exact-enum, 정형화 전엔 type_keywords로 관대 매칭.
  - service : 데이터 앵커명 별칭을 정규화해 매칭. service_optional이면 채점 제외.

`run.py`가 선택적으로 import한다 — 이 모듈(또는 pyyaml)이 없으면 채점 없이 결과만 기록된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_GT_PATH = Path(__file__).parent / "ground_truth.yaml"
_GROUND_TRUTH: dict = (
    yaml.safe_load(_GT_PATH.read_text(encoding="utf-8")) or {}
    if _GT_PATH.exists()
    else {}
)


def _norm_service(s: str) -> str:
    """서비스명 정규화 — 소문자·구분자 제거·접미 'service' 제거. media-service→media."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\s_-]+", "", s)
    s = re.sub(r"service$", "", s)
    return s


def _type_ok(pred_type: str, expected: dict) -> bool:
    pred = (pred_type or "").strip()
    if pred.upper() == str(expected.get("type", "")).upper():
        return True  # 정형화 후 exact-enum
    low = pred.lower()
    return any(str(k).lower() in low for k in expected.get("type_keywords", []))


def _service_ok(pred_service: str, expected: dict) -> bool:
    if expected.get("service_optional"):
        return True
    pred = _norm_service(pred_service)
    if not pred:
        return False
    names = [expected.get("service", ""), *expected.get("service_aliases", [])]
    return any(pred == _norm_service(n) for n in names)


def score_result(scenario: str, result) -> tuple[bool | None, str]:
    """(정답여부, 판정 문구) 반환. ground_truth에 없는 시나리오면 (None, 사유)."""
    gt = _GROUND_TRUTH.get(scenario)
    if not gt:
        return None, f"(ground_truth 없음: {scenario})"

    type_ok = _type_ok(result.type, gt)
    service_ok = _service_ok(result.service, gt)
    correct = type_ok and service_ok

    parts = [f"type={'O' if type_ok else 'X'}"]
    if not gt.get("service_optional"):
        parts.append(f"service={'O' if service_ok else 'X'}")
    return correct, " ".join(parts)
