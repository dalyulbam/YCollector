# YCollector — Tauri 인스톨러 빌드 스크립트 (Windows).
#
# 절차:
#   1) Nuitka 로 ycollector CLI 의 single-folder dist 를 만든다.
#   2) 산출물 폴더에서 ycollector.exe 를 추출해
#      src-tauri/binaries/ycollector-x86_64-pc-windows-msvc.exe 로 복사한다.
#      (Tauri sidecar 네이밍 규칙: <name>-<rust target triple>(.exe))
#   3) frontend/ 에서 pnpm install + Vite 빌드.
#   4) src-tauri 에서 tauri build → .msi / NSIS .exe 인스톨러.
#
# 사전 요구:
#   - Rust toolchain (`rustup default stable`)
#   - pnpm (`npm i -g pnpm`) — npm 으로 대체 가능, 본 스크립트는 pnpm 우선.
#   - WebView2 Runtime (Windows 11 기본 포함)
#
# 빠른 dev 실행은 별도:  cargo tauri dev  (이 스크립트는 release 인스톨러 생성용)

param(
    [string]$Triple = "x86_64-pc-windows-msvc",
    [switch]$SkipSidecar  # sidecar 가 이미 binaries/ 에 있을 때
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Need($cmd) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "필수 명령을 찾지 못함: $cmd"
    }
}

# 의존성 점검 (sidecar 빌드는 SkipSidecar 시 uv 도 skip).
if (-not $SkipSidecar) { Need uv }
Need cargo
$PkgMgr = if (Get-Command pnpm -ErrorAction SilentlyContinue) { "pnpm" } else { Need npm; "npm" }
Write-Host "package manager: $PkgMgr"

# 1) sidecar 빌드 ───────────────────────────────────────────────────────────
if (-not $SkipSidecar) {
    Step "Nuitka 로 ycollector CLI 빌드"
    uv sync --extra build
    uv run python scripts/build_exe.py --target cli

    # build_exe.py 의 산출물 후보를 자동 탐색.
    $candidates = @(
        Join-Path $Root "dist/ycollector-cli.exe",
        Join-Path $Root "dist/cli.dist/ycollector-cli.exe",
        Join-Path $Root "dist/cli.dist/ycollector.exe"
    )
    $sidecarSrc = $null
    foreach ($p in $candidates) {
        if (Test-Path $p) { $sidecarSrc = $p; break }
    }
    if (-not $sidecarSrc) {
        # dist 안에서 ycollector*.exe 를 fallback 탐색.
        $found = Get-ChildItem -Recurse -Path (Join-Path $Root "dist") -Filter "ycollector*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $sidecarSrc = $found.FullName }
    }
    if (-not $sidecarSrc) {
        throw "Nuitka 산출물에서 ycollector*.exe 를 찾지 못함. dist/ 폴더를 확인하세요."
    }
    Step "sidecar 후보: $sidecarSrc"

    $binDir = Join-Path $Root "src-tauri/binaries"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $sidecarDest = Join-Path $binDir "ycollector-$Triple.exe"
    Copy-Item -Force $sidecarSrc $sidecarDest
    Write-Host "  → $sidecarDest"
} else {
    Step "sidecar 빌드 스킵 (--SkipSidecar)"
}

# 2) frontend 빌드 ─────────────────────────────────────────────────────────
Step "frontend 의존성 설치 + Vite 빌드"
Push-Location (Join-Path $Root "frontend")
try {
    if ($PkgMgr -eq "pnpm") {
        pnpm install
        pnpm build
    } else {
        npm install
        npm run build
    }
} finally {
    Pop-Location
}

# 3) Tauri 인스톨러 빌드 ───────────────────────────────────────────────────
Step "tauri build (.msi + NSIS .exe)"
Push-Location (Join-Path $Root "src-tauri")
try {
    # tauri-cli 가 frontend 에 devDependency 로 들어있다면 그쪽을 통해서 실행.
    & cargo tauri build
} finally {
    Pop-Location
}

Step "완료. 산출물은 src-tauri/target/release/bundle/ 아래를 확인하세요."
