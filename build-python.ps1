# build-python.ps1
# Build gen_3dtiles_lod.py into a standalone Windows exe via PyInstaller
# Output: python_dist/gen_3dtiles_lod/gen_3dtiles_lod.exe

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host ""
Write-Host "=== Compiling Python script with PyInstaller ===" -ForegroundColor Cyan
Write-Host "  (uv will install deps automatically on first run)" -ForegroundColor Gray

uv run --no-project `
    --with geopandas `
    --with pyogrio `
    --with numpy `
    --with pygltflib `
    --with shapely `
    --with pyinstaller `
    pyinstaller `
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
Write-Host "=== Done! ===" -ForegroundColor Green
Write-Host "Output: python_dist\gen_3dtiles_lod\gen_3dtiles_lod.exe" -ForegroundColor Green
