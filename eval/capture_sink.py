"""프롬프트 평가 하네스 — 픽스처 캡처 싱크 (설계 A단계).

SDK(main)의 e2e 실행이 트리거 발화 시 POST하는 IngestBundle을 받아
`eval/fixtures/<scenario>.json`에 저장한다. SDK `scripts/mock_ingest_server.py`
(요약만 찍고 버림)에 "본문 저장"을 더한 형태다.

사용:
    python eval/capture_sink.py <scenario>            # :8000/ingest 대기
    # 다른 터미널(SDK 루트, main):  scripts/run_local_demo.sh <scenario>

첫 트리거 번들을 `fixtures/<scenario>.json`으로 저장하고, 이후 번들은
`fixtures/<scenario>.dup<n>.json`으로 남겨 유실 없이 사용자가 고르게 한다.
받은 바이트를 그대로 저장하므로(재직렬화 없음) SDK가 보낸 원본과 동일하다.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCENARIOS = ("cpu", "kill_media", "code_media")
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _summary(bundle: dict) -> str:
    """수신 로그 한 줄 — window·trigger·모달리티별 건수."""
    window = bundle.get("window", {})
    trigger = bundle.get("triggerInfo", {})
    triggered_by = ",".join(trigger.get("triggeredBy", [])) or "(없음)"
    return (
        f"window={window.get('start')}~{window.get('end')} "
        f"trigger={trigger.get('triggerTime')}({triggered_by}) "
        f"logs={len(bundle.get('logs', []))} "
        f"metrics={len(bundle.get('metrics', []))} "
        f"traces={len(bundle.get('traces', []))}"
    )


def _make_handler(scenario: str, state: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/ingest":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)  # 받은 바이트 그대로 보존

            state["count"] += 1
            n = state["count"]
            FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            path = FIXTURES_DIR / (f"{scenario}.json" if n == 1 else f"{scenario}.dup{n}.json")
            path.write_bytes(body)

            try:
                summary = _summary(json.loads(body))
            except (ValueError, TypeError):
                summary = f"(JSON 파싱 실패, {len(body)} bytes 그대로 저장)"
            print(f"[캡처 #{n}] {summary}")
            print(f"          → {path}")
            if n == 1:
                print("  첫 트리거 번들 저장 완료. 추가 POST는 dup 로 남깁니다. (Ctrl+C 로 종료)")

            # rca-collect 의 transport 가 기대하는 성공 응답(accepted=True) 반환.
            resp = json.dumps({"accepted": True, "job_id": "capture"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *args: object) -> None:
            pass  # 기본 접속 로그는 끈다 — 위 요약 한 줄이면 충분

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프롬프트 평가 픽스처 캡처 싱크")
    parser.add_argument("scenario", choices=SCENARIOS, help="캡처할 시나리오")
    parser.add_argument("--port", type=int, default=8000, help="수신 포트 (기본 8000)")
    args = parser.parse_args(argv)

    # Windows 콘솔 기본 cp949 는 '→' 등을 못 찍고 죽는다 — SDK 스크립트와 같은 처리.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    state = {"count": 0}
    server = HTTPServer(("127.0.0.1", args.port), _make_handler(args.scenario, state))
    print(f"[capture_sink] http://127.0.0.1:{args.port}/ingest 대기 — 시나리오 '{args.scenario}'")
    print(f"[capture_sink] SDK 루트(main)에서:  scripts/run_local_demo.sh {args.scenario}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[capture_sink] 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
