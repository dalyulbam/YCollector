"""가상 Chromium(Playwright) → YouTube 로그인 → Netscape cookies.txt.

배경
----
yt-dlp 의 ``--cookies-from-browser chrome`` 은 시스템 Chrome 의 쿠키 DB 를
직접 읽지만, Chrome 이 실행 중이면 SQLite 파일에 write lock 이 걸려
``Could not copy Chrome cookie database`` 에러가 난다 (yt-dlp 이슈 #7271).

본 모듈은 **Playwright 가 관리하는 별 Chromium** 을 띄워:

1. 사용자가 자기 YouTube 계정으로 로그인하고,
2. 로그인이 끝나면 컨텍스트의 쿠키를 Netscape HTTP Cookie File 포맷으로 저장.

이후 ``yt-dlp --cookies <file>`` 로 패스 → 메인 Chrome 은 그대로 켜둬도 됨.

사용
----
::

    uv sync --extra cookies-headed
    uv run playwright install chromium    # 1회 (~150MB)
    uv run ycollector-login               # 별 창에서 로그인 → 자동 저장

저장 위치
---------
- Windows:  ``%APPDATA%\\YCollector\\cookies.txt``
- macOS:    ``~/Library/Application Support/YCollector/cookies.txt``
- Linux:    ``${XDG_CONFIG_HOME:-~/.config}/ycollector/cookies.txt``

Playwright persistent context 의 user_data_dir 은 같은 폴더의
``pw-profile/`` 에 둔다. 한 번 로그인하면 이후엔 그 프로필을 재사용하므로
재로그인 절차를 건너뛸 수 있다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Playwright 는 optional extra. 안 깔린 상태에서 import 만 해도 동작이 안 멈추도록
# main() 안에서 lazy import.

YOUTUBE_HOME = "https://www.youtube.com/"
_LOGIN_POLL_INTERVAL = 0.8  # sec
_LOGIN_CONFIRM_DELAY = 8.0  # sec — 로그인 감지 후 추가 대기(쿠키 안정화)


# ── 경로 헬퍼 ─────────────────────────────────────────────────────────────
def user_config_dir() -> Path:
    """settings.ini 와 동일한 사용자 설정 디렉토리."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "YCollector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "YCollector"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ycollector"


def default_cookies_path() -> Path:
    return user_config_dir() / "cookies.txt"


def default_profile_dir() -> Path:
    return user_config_dir() / "pw-profile"


def is_cookies_present(path: Path | None = None) -> bool:
    """파일 존재 + non-empty 만 체크. 만료까지 보지는 않음(yt-dlp 가 잡음)."""
    p = path or default_cookies_path()
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


# ── Netscape cookies.txt 직렬화 ───────────────────────────────────────────
_NETSCAPE_HEADER = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.se/docs/http-cookies.html\n"
    "# 이 파일은 ycollector-login 이 생성합니다. 수동 편집 비권장.\n"
    "# Generated: {ts}\n"
    "\n"
)


def to_netscape(cookies: list[dict[str, Any]]) -> str:
    """Playwright cookie dict 리스트 → Netscape cookies.txt 본문.

    Playwright cookie 키:
        name, value, domain, path, expires, httpOnly, secure, sameSite
    Netscape 7 컬럼(탭 구분):
        domain  include_subdomains  path  secure  expires  name  value
    """
    lines: list[str] = [_NETSCAPE_HEADER.format(ts=datetime.now().isoformat(timespec="seconds"))]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain:
            continue
        # Netscape 규칙: 첫 글자가 '.' 이면 "include subdomains".
        # Playwright 가 host-only cookie 면 도메인이 'youtube.com' 식으로 옴.
        # 흔히 yt-dlp 호환을 위해 모두 leading-dot 형태로 통일하는 게 안전.
        if c.get("httpOnly"):
            # curl 의 관례: HTTP-only 쿠키는 도메인 앞에 '#HttpOnly_' 를 붙임.
            # yt-dlp 는 이 prefix 를 지원한다.
            domain_field = f"#HttpOnly_{_normalize_domain(domain)}"
        else:
            domain_field = _normalize_domain(domain)
        include_sub = "TRUE" if domain_field.lstrip("#HttpOnly_").startswith(".") else "FALSE"
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires_raw = c.get("expires")
        if expires_raw is None or expires_raw < 0:
            expires = "0"   # session cookie
        else:
            expires = str(int(expires_raw))
        name = c.get("name") or ""
        value = c.get("value") or ""
        # 값에 탭/개행이 들어가면 Netscape 포맷이 깨짐 → strip.
        value = value.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        lines.append(
            "\t".join([domain_field, include_sub, path, secure, expires, name, value]) + "\n"
        )
    return "".join(lines)


def _normalize_domain(domain: str) -> str:
    """leading-dot 통일. 'www.youtube.com' → '.youtube.com' 로 강제하진 않고,
    이미 dot 가 없는 host-only 만 그대로 둔다(yt-dlp 가 둘 다 받음)."""
    if domain.startswith("."):
        return domain
    # 흔히 google account 쿠키들은 'accounts.google.com' 같은 host-only.
    # 그대로 두면 include_subdomains=FALSE 로 나가 정확히 같은 host 에만 매칭.
    return domain


# ── 로그인 흐름 ───────────────────────────────────────────────────────────
def _is_logged_in(page: Any) -> bool:
    """YouTube 상단 우측의 사용자 아바타 버튼 존재 여부로 판단.

    여러 셀렉터 후보 — YouTube 가 자주 바뀌므로 OR.
    """
    for selector in (
        "button#avatar-btn",
        "ytd-topbar-menu-button-renderer #avatar-btn",
        "button[aria-label*='Google 계정']",
        "button[aria-label*='Google Account']",
        "img#img.style-scope.yt-img-shadow",  # 아바타 이미지 자체
    ):
        try:
            if page.query_selector(selector):
                return True
        except Exception:
            # 페이지 navigation 중 또는 컨텍스트 닫힘 — 호출자가 처리.
            return False
    return False


def login_and_save(
    out_path: Path | None = None,
    *,
    profile_dir: Path | None = None,
    headless: bool = False,
    timeout: int = 600,
) -> Path:
    """별 Chromium 을 띄워 로그인 → cookies.txt 저장 → 경로 반환.

    Args:
        out_path: 저장 경로. None 이면 default_cookies_path().
        profile_dir: Playwright user_data_dir. None 이면 default_profile_dir().
            재호출 시 같은 dir 을 쓰면 이전 로그인 유지(재로그인 불필요).
        headless: True 면 헤드리스 모드(첫 로그인엔 부적합 — 디버그용).
        timeout: 사용자가 로그인 완료할 때까지 대기할 최대 초.
    """
    try:
        from playwright.sync_api import (
            Error as PWError,  # noqa: N814
            sync_playwright,
        )
    except ImportError as exc:
        raise SystemExit(
            "playwright 가 설치돼있지 않습니다.\n"
            "  uv sync --extra cookies-headed\n"
            "  uv run playwright install chromium\n"
            f"원인: {exc}"
        ) from exc

    out_path = out_path or default_cookies_path()
    profile_dir = profile_dir or default_profile_dir()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"[i] Chromium 프로필: {profile_dir}", file=sys.stderr)
    print(f"[i] 저장 위치: {out_path}", file=sys.stderr)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1280, "height": 800},
        )

        # 컨텍스트가 자체 페이지를 들고 시작할 수도, 없을 수도 — 안전하게 한 장.
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(YOUTUBE_HOME, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            print(f"  [!] YouTube 로드 실패(네트워크?): {exc}", file=sys.stderr)

        # 컨텍스트가 닫혔는지(사용자 창 닫음) 추적.
        closed = {"v": False}
        ctx.on("close", lambda _=None: closed.update(v=True))

        print(
            "\n────────────────────────────────────────────────────────────────\n"
            "  ▶ 띄워진 Chromium 창에서 YouTube 에 로그인하세요.\n"
            "  ▶ 로그인 완료가 감지되면 자동으로 쿠키를 저장하고 창이 닫힙니다.\n"
            "  ▶ 수동 종료하려면 창을 그냥 닫으세요 — 현재까지 쿠키를 저장합니다.\n"
            f"  ▶ 타임아웃: {timeout}s\n"
            "────────────────────────────────────────────────────────────────\n",
            file=sys.stderr,
        )

        t0 = time.monotonic()
        confirmed_at: float | None = None

        while True:
            elapsed = time.monotonic() - t0
            if elapsed > timeout:
                print("  [!] 타임아웃 — 현재 상태로 쿠키 저장.", file=sys.stderr)
                break
            if closed["v"]:
                print("  [i] 사용자가 창을 닫음 — 마지막 쿠키 저장 시도.", file=sys.stderr)
                break
            try:
                logged_in = _is_logged_in(page)
            except PWError:
                # 페이지가 detach 됐을 수 있음 — 다음 폴링에서 ctx 가 닫혔는지 확인됨.
                logged_in = False

            if logged_in:
                if confirmed_at is None:
                    confirmed_at = time.monotonic()
                    print(
                        f"  ✓ 로그인 감지. {_LOGIN_CONFIRM_DELAY:.0f}초 후 자동 저장…",
                        file=sys.stderr,
                    )
                elif time.monotonic() - confirmed_at >= _LOGIN_CONFIRM_DELAY:
                    break
            else:
                confirmed_at = None  # 로그아웃이 다시 보이면 카운터 리셋
            time.sleep(_LOGIN_POLL_INTERVAL)

        # 쿠키 수집. context 가 이미 닫혔다면 try 가 PWError.
        cookies: list[dict[str, Any]] = []
        try:
            cookies = ctx.cookies()
        except Exception as exc:
            print(f"  [!] 쿠키 수집 실패: {exc}", file=sys.stderr)
        # 다 받았으면 자동 종료.
        try:
            ctx.close()
        except Exception:
            pass

    if not cookies:
        print(
            "\n  ✗ 쿠키를 한 개도 수집하지 못했습니다.\n"
            "    - 로그인을 정말 완료했는지 확인\n"
            "    - 또는 같은 명령을 다시 시도(profile dir 가 보존됨)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    text = to_netscape(cookies)
    out_path.write_text(text, encoding="utf-8")

    # 요약 출력
    yt_count = sum(1 for c in cookies if "youtube" in (c.get("domain") or ""))
    g_count = sum(1 for c in cookies if "google" in (c.get("domain") or ""))
    print(
        f"\n  ✓ 저장 완료: {out_path}  (총 {len(cookies)}개 · youtube {yt_count} · google {g_count})\n"
        f"    이제 그냥:  uv run ycollector --yes-playlist \"<URL>\"\n"
        f"    또는 명시:  uv run ycollector --cookies \"{out_path}\" \"<URL>\"\n",
        file=sys.stderr,
    )
    return out_path


# ── Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    p = argparse.ArgumentParser(
        prog="ycollector-login",
        description=(
            "가상 Chromium(Playwright)으로 YouTube 에 로그인하고 쿠키를 cookies.txt 로 "
            "저장합니다. 메인 Chrome 은 켜둔 채로도 충돌 없이 동작합니다."
        ),
    )
    p.add_argument("--out", type=Path, default=None,
                   help=f"저장 경로 (기본: {default_cookies_path()})")
    p.add_argument("--profile-dir", type=Path, default=None,
                   help=f"Playwright user_data_dir (기본: {default_profile_dir()})")
    p.add_argument("--timeout", type=int, default=600,
                   help="사용자 로그인 대기 최대 초 (기본 600)")
    p.add_argument("--headless", action="store_true",
                   help="헤드리스 모드. 첫 로그인엔 비권장 — 이미 프로필 dir 에 "
                        "세션이 있을 때 자동 갱신용.")
    p.add_argument("--print-path", action="store_true",
                   help="기본 저장 경로만 출력하고 종료.")
    args = p.parse_args(argv)

    if args.print_path:
        print(args.out or default_cookies_path())
        return 0

    try:
        login_and_save(
            out_path=args.out,
            profile_dir=args.profile_dir,
            headless=args.headless,
            timeout=args.timeout,
        )
    except KeyboardInterrupt:
        print("\n  중단됨 (Ctrl+C).", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
