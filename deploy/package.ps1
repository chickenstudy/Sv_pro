# SV-PRO Packaging Script for Windows
# Build React dashboard + copy source to deploy/
# Then manually copy deploy/ folder to Linux server.

$ErrorActionPreference = "Stop"

$DeployDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $DeployDir

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SV-PRO Packaging (Windows)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Project root : $ProjectRoot" -ForegroundColor Gray
Write-Host "  Deploy dir   : $DeployDir" -ForegroundColor Gray
Write-Host ""

# ============================================================
# Step 1: Build frontend
# ============================================================
Write-Host "[1/5] Build frontend React..." -ForegroundColor Yellow

$DistDir = Join-Path $ProjectRoot "dashboard\dist"

if (-not (Test-Path $DistDir)) {
    Write-Host "  No dist found - skipping build." -ForegroundColor Gray
    Write-Host "  Build first: cd dashboard; npm install; npm run build" -ForegroundColor DarkGray
} else {
    Write-Host "  Frontend already built." -ForegroundColor Green
}

# ============================================================
# Step 2: Copy backend source
# ============================================================
Write-Host "[2/5] Copy backend source..." -ForegroundColor Yellow

$BackendDest = Join-Path $DeployDir "backend"
if (Test-Path $BackendDest) { Remove-Item $BackendDest -Recurse -Force }
New-Item -ItemType Directory -Path $BackendDest -Force | Out-Null

Copy-Item -Path (Join-Path $ProjectRoot "backend\*") -Destination $BackendDest -Recurse -Force

$FrontendDest = Join-Path $BackendDest "frontend"
New-Item -ItemType Directory -Path $FrontendDest -Force | Out-Null
if (Test-Path $DistDir) {
    Copy-Item -Path "$DistDir\*" -Destination $FrontendDest -Recurse -Force
}

Write-Host "  Backend + frontend copied." -ForegroundColor Green

# ============================================================
# Step 3: Copy AI pipeline (src)
# ============================================================
Write-Host "[3/5] Copy AI pipeline modules..." -ForegroundColor Yellow

$SrcDest = Join-Path $DeployDir "src"
if (Test-Path $SrcDest) { Remove-Item $SrcDest -Recurse -Force }
Copy-Item -Path (Join-Path $ProjectRoot "src") -Destination $SrcDest -Recurse -Force

Write-Host "  src/ copied." -ForegroundColor Green

# ============================================================
# Step 4: Copy models
# ============================================================
Write-Host "[4/5] Copy AI models..." -ForegroundColor Yellow

$ModelsSrc = Join-Path $ProjectRoot "models"
$ModelsDest = Join-Path $DeployDir "models"

if (Test-Path $ModelsDest) { Remove-Item $ModelsDest -Recurse -Force }
New-Item -ItemType Directory -Path $ModelsDest -Force | Out-Null

if (Test-Path $ModelsSrc) {
    Get-ChildItem $ModelsSrc -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $ModelsDest -Recurse -Force
    }
    $modelCount = (Get-ChildItem $ModelsDest -Directory).Count
    Write-Host "  $modelCount model folders copied." -ForegroundColor Green
} else {
    Write-Host "  Warning: models/ folder not found." -ForegroundColor DarkYellow
}

# ============================================================
# Step 5: Copy configs & Dockerfiles
# ============================================================
Write-Host "[5/5] Copy configs & Dockerfiles..." -ForegroundColor Yellow

$copyPairs = @(
    @{ Src = "docker-compose.yml";          Dest = $DeployDir },
    @{ Src = "Dockerfile.backend";           Dest = $DeployDir },
    @{ Src = "Dockerfile.savant-ai-core";    Dest = $DeployDir },
    @{ Src = "Dockerfile.ingress-manager";   Dest = $DeployDir },
    @{ Src = "requirements.txt";             Dest = $DeployDir },
    @{ Src = "module\module.yml";            Dest = $DeployDir },
    @{ Src = "tracker\config_tracker_NvSORT.yml"; Dest = Join-Path $DeployDir "tracker" },
    @{ Src = "install.sh";                   Dest = $DeployDir },
    @{ Src = "scripts\sql";                 Dest = Join-Path $DeployDir "scripts" }
)

foreach ($pair in $copyPairs) {
    $src = Join-Path $ProjectRoot $pair.Src
    if (Test-Path $src) {
        New-Item -ItemType Directory -Path $pair.Dest -Force | Out-Null
        if ((Test-Path $src -PathType Leaf)) {
            Copy-Item $src $pair.Dest -Force
        } else {
            Copy-Item $src $pair.Dest -Recurse -Force
        }
    }
}

$MonSrc = Join-Path $ProjectRoot "monitoring"
$MonDest = Join-Path $DeployDir "monitoring"
if (Test-Path $MonSrc) {
    if (Test-Path $MonDest) { Remove-Item $MonDest -Recurse -Force }
    Copy-Item $MonSrc $MonDest -Recurse -Force
}

Write-Host "  Configs copied." -ForegroundColor Green

# ============================================================
# Check critical models
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Model Check:" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

function CheckModel($Path) {
    if (Test-Path $Path) {
        $sizeMB = [math]::Round((Get-Item $Path).Length / 1MB, 1)
        Write-Host "  [OK] $Path ($sizeMB MB)" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $Path" -ForegroundColor Red
    }
}

CheckModel "$ModelsDest\yolov8n_plate\yolov8n_plate.onnx"
CheckModel "$ModelsDest\yolov8\yolov8s.onnx"
CheckModel "$ModelsDest\scrfd_10g_bnkps.onnx"
CheckModel "$ModelsDest\glintr100.onnx"

# ============================================================
# Summary
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Done! deploy/ is ready." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next step - copy to Linux server:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Use rsync (install: choco install rsync):" -ForegroundColor Gray
Write-Host '     rsync -avP deploy/ user@server:/tmp/svpro/' -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Or use WinSCP/MobaXterm to drag-and-drop." -ForegroundColor Gray
Write-Host ""
Write-Host "  3. On server:" -ForegroundColor Gray
Write-Host "     ssh user@server" -ForegroundColor Gray
Write-Host "     sudo mkdir -p /opt/svpro" -ForegroundColor Gray
Write-Host "     sudo cp -r /tmp/svpro/* /opt/svpro/" -ForegroundColor Gray
Write-Host "     cd /opt/svpro" -ForegroundColor Gray
Write-Host ""
Write-Host "  4. Edit .env (security):" -ForegroundColor Gray
Write-Host '     sudo nano .env' -ForegroundColor Gray
Write-Host "     Change: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  5. Install:" -ForegroundColor Gray
Write-Host "     chmod +x install.sh scripts/*.sh" -ForegroundColor Gray
Write-Host "     sudo ./install.sh" -ForegroundColor Gray
Write-Host ""
Write-Host "  6. Verify:" -ForegroundColor Gray
Write-Host "     bash scripts/quickstart.sh" -ForegroundColor Gray
Write-Host ""