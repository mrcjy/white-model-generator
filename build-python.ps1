# build-python.ps1
# 用 uv 创建隔离环境，将 gen_3dtiles_lod.py 编译为独立 Windows exe
# 输出: python_dist/gen_3dtiles_lod/gen_3dtiles_lod.exe

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host ""
Write-Host "=== Step 1: 创建编译用虚拟环境 ===" -ForegroundColor Cyan
uv venv "$Root\.venv-build" --python 3.11
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "=== Step 2: 安装依赖 + PyInstaller ===" -ForegroundColor Cyan
uv pip install --python "$Root\.venv-build" `
    geopandas pyogrio numpy pygltflib shapely pyinstaller
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "=== Step 3: 编译 Python 脚本 ===" -ForegroundColor Cyan
& "$Root\.venv-build\Scripts\pyinstaller.exe" `
    --onedir `
    --name gen_3dtiles_lod `
    --collect-all geopandas `
    --collect-all pyogrio `
    --collect-all shapely `
    --collect-all fiona `
    --distpath "$Root\python_dist" `
    --workpath "$Root\.pyinstaller-build" `
    --specpath "$Root\.pyinstaller-build" `
    --noconfirm `
    "$Root\gen_3dtiles_lod.py"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "=== 完成! ===" -ForegroundColor Green
Write-Host "编译产物: python_dist\gen_3dtiles_lod\gen_3dtiles_lod.exe" -ForegroundColor Green
