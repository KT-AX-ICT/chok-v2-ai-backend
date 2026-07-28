"""프롬프트 평가 뷰어 로컬 서빙 (설계 E 보조).

`eval/` 를 정적 서버로 띄워 `viewer.html`이 `index.csv`·`runs/*/result.json`을 fetch할 수
있게 한다. 브라우저 `file://` 은 로컬 fetch가 막혀 상세/비교가 안 되므로 이 서버가 필요하다.
FastAPI 앱(:8000)과 무관한 **로컬 dev 도구** — 배포 환경용이 아니다.

    python -m eval.serve [--port 8899] [--no-open]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import webbrowser
from pathlib import Path

EVAL_DIR = Path(__file__).parent


def serve(port: int = 8899, open_browser: bool = True) -> None:
    """eval/ 를 127.0.0.1:port 로 서빙(블로킹). Ctrl+C 로 종료."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(EVAL_DIR))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/viewer.html"
    print(f"[serve] {url}   (Ctrl+C 로 종료)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 브라우저 자동열기 실패는 무시(URL은 출력됨)
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 종료")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프롬프트 평가 뷰어 로컬 서빙")
    parser.add_argument("--port", type=int, default=8899, help="수신 포트 (기본 8899)")
    parser.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args(argv)
    serve(args.port, not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
