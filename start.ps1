# 本地一键启动（Windows PowerShell）
# 用法： .\start.ps1            -> 后端 8000 + 前端 5173（开发模式）
#        .\start.ps1 -Prod      -> 构建前端后仅启动后端 8000（单端口）
# 若提示禁止运行脚本，改用：
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
#
# 本文件必须保存为 UTF-8 with BOM：
# PowerShell 5.1 读取无 BOM 脚本时按系统 ANSI(GBK) 解码，中文提示里的字节
# 可能被误判成字符串结束符，直接抛 ParserError（Unexpected token）。

param(
    [switch]$Prod,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

# 统一控制台输出编码，避免中文提示显示成乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Test-PythonOk {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    try {
        $version = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $version) { return $false }
        $parts = $version.Trim().Split('.')
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        return (($major -gt 3) -or (($major -eq 3) -and ($minor -ge 11)))
    }
    catch {
        return $false
    }
}

# ---------- 环境检查 ----------
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    # 已有虚拟环境时以它为准，不拿系统 python 的判断去干扰
    if (-not (Test-PythonOk $VenvPython)) {
        throw "现有 .venv 的解释器版本低于 3.11。请删除 .venv 目录后用 Python 3.11+ 重建。"
    }
    Write-Host "Python: $VenvPython (已有虚拟环境)" -ForegroundColor Gray
}
else {
    # 必须校验版本：机器上可能存在 Python 2.7，直接拿它建 venv 会失败
    $found = $null
    foreach ($name in @("python", "python3", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and (Test-PythonOk $cmd.Source)) { $found = $cmd.Source; break }
    }
    if (-not $found) {
        throw "未找到 Python 3.11 或更高版本。请安装后在安装向导中勾选 Add python.exe to PATH。"
    }
    Write-Host "Python: $found" -ForegroundColor Gray
    Write-Host "[1/5] 创建 Python 虚拟环境 ..." -ForegroundColor Cyan
    & $found -m venv .venv
    if (-not (Test-Path $VenvPython)) { throw "虚拟环境创建失败" }
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "未找到 Node.js。请安装 Node.js 18 或更高版本：https://nodejs.org"
}
Write-Host "Node:   $((Get-Command node).Source)" -ForegroundColor Gray

# ---------- 依赖 ----------
if (-not $SkipInstall) {
    Write-Host "[2/5] 安装后端依赖（首次约 1-3 分钟）..." -ForegroundColor Cyan
    & $VenvPython -m pip install -q --disable-pip-version-check `
        -i https://pypi.org/simple -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "依赖安装失败。可尝试换镜像后重试：" -ForegroundColor Yellow
        Write-Host "  .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor Yellow
        throw "后端依赖安装失败"
    }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env（未填写任何密钥，AI 分析会显示未配置）" -ForegroundColor Yellow
}

# ---------- 启动 ----------
if ($Prod) {
    Write-Host "[3/5] 安装并构建前端 ..." -ForegroundColor Cyan
    Push-Location frontend
    if (-not (Test-Path "node_modules")) {
        & cmd.exe /c "npm install --registry=https://registry.npmmirror.com"
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "前端依赖安装失败" }
    }
    & cmd.exe /c "npm run build"
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "前端构建失败" }
    Pop-Location

    Write-Host "[4/5] 启动服务：http://127.0.0.1:8000" -ForegroundColor Green
    & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
}
else {
    Write-Host "[3/5] 启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Green
    $backend = Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--app-dir", "backend", "--reload" `
        -WorkingDirectory $Root -PassThru -WindowStyle Normal

    Write-Host "[4/5] 安装并启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Green
    Push-Location frontend
    if (-not (Test-Path "node_modules")) {
        & cmd.exe /c "npm install --registry=https://registry.npmmirror.com"
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "前端依赖安装失败" }
    }
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Normal
    Pop-Location

    Write-Host "[5/5] 已启动。关闭这两个窗口即停止服务，后台定时任务也随之停止。" -ForegroundColor Yellow
    Write-Host "后端 PID: $($backend.Id)  接口文档: http://127.0.0.1:8000/docs" -ForegroundColor Gray
}
