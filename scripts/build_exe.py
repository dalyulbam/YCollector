"""Build standalone YCollector executables.

Modes
-----
nuitka       Native compile (recommended). Faster startup, fewer AV
             false-positives, ~80-120 MB .exe (standalone with PySide6).
pyinstaller  Universal fallback. Simpler, ~150 MB onedir, slower start.

Usage
-----
    # Install build extras into the venv first
    uv sync --extra build

    # Build the GUI .exe (default — Nuitka)
    uv run python scripts/build_exe.py

    # Use PyInstaller instead
    uv run python scripts/build_exe.py --mode pyinstaller

    # Also produce a console-mode CLI .exe
    uv run python scripts/build_exe.py --target both

The output lands in ``dist/``. Both ``dist/`` and ``build/`` are gitignored.

Notes
-----
- Nuitka requires a C compiler. On Windows it auto-downloads MinGW64
  on first run if MSVC isn't present.
- For Whisper (Phase 3), the Whisper model files are *not* bundled —
  they're opt-in downloads at runtime (~250 MB ~ 1.5 GB).
- Plan §11.1 covers the broader packaging strategy.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "ycollector"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"
ICON = ASSETS / "icon.ico"


def _ensure_dirs() -> None:
    DIST.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)


def _check_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


# ── Nuitka ──────────────────────────────────────────────────────────────────
def build_nuitka(target: str) -> Path:
    if not _check_module("nuitka"):
        raise SystemExit(
            "nuitka not installed.  Run:  uv sync --extra build"
        )

    is_gui = target == "gui"
    output_name = "YCollector" if is_gui else "ycollector-cli"
    entry = SRC / ("gui.py" if is_gui else "cli.py")

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        f"--output-dir={DIST}",
        f"--output-filename={output_name}",
        "--include-package=ycollector",
        "--remove-output",
        "--no-deployment-flag=self-execution",
    ]
    if sys.platform == "win32":
        if is_gui:
            cmd.append("--windows-console-mode=disable")
        if ICON.exists():
            cmd.append(f"--windows-icon-from-ico={ICON}")
    elif sys.platform == "darwin":
        cmd.extend([
            "--macos-create-app-bundle",
            f"--macos-app-name={output_name}",
        ])
        if ICON.exists():
            cmd.append(f"--macos-app-icon={ICON}")

    cmd.append(str(entry))

    print(">>>", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    out_dir = DIST / f"{entry.stem}.dist"
    suffix = ".exe" if sys.platform == "win32" else ""
    binary = out_dir / f"{output_name}{suffix}"
    print(f"\nBuilt: {binary}")
    return binary


# ── PyInstaller ─────────────────────────────────────────────────────────────
def build_pyinstaller(target: str) -> Path:
    if not _check_module("PyInstaller"):
        raise SystemExit(
            "pyinstaller not installed.  Run:  uv sync --extra build"
        )

    is_gui = target == "gui"
    output_name = "YCollector" if is_gui else "ycollector-cli"
    entry = SRC / ("gui.py" if is_gui else "cli.py")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", output_name,
        "--noconfirm",
        "--onedir",
        "--paths", str(ROOT / "src"),
        "--collect-all", "PySide6" if is_gui else "yt_dlp",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    if is_gui:
        cmd.append("--windowed")
    if sys.platform == "win32" and ICON.exists():
        cmd.extend(["--icon", str(ICON)])

    cmd.append(str(entry))

    print(">>>", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    suffix = ".exe" if sys.platform == "win32" else ""
    binary = DIST / output_name / f"{output_name}{suffix}"
    print(f"\nBuilt: {binary}")
    return binary


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build YCollector standalone executables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See plan §11.1 for the full packaging strategy.",
    )
    parser.add_argument("--mode", choices=["nuitka", "pyinstaller"], default="nuitka",
                        help="Build backend (default: nuitka).")
    parser.add_argument("--target", choices=["gui", "cli", "both"], default="gui",
                        help="Which entry point(s) to build (default: gui).")
    parser.add_argument("--clean", action="store_true",
                        help="Remove dist/ and build/ before building.")
    args = parser.parse_args()

    if args.clean:
        for d in (DIST, BUILD):
            if d.exists():
                print(f"  cleaning {d}")
                shutil.rmtree(d)

    _ensure_dirs()
    build_fn = build_nuitka if args.mode == "nuitka" else build_pyinstaller
    targets = ["gui", "cli"] if args.target == "both" else [args.target]
    for t in targets:
        build_fn(t)

    print("\nDone. Output in dist/.")
    if sys.platform == "win32":
        print("Tip: Right-click YCollector.exe → 바탕화면에 바로가기 만들기 → 시작 메뉴에 고정.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
