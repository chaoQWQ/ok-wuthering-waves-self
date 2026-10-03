param(
    [ValidateSet('MultiAccountDailyTask')]
    [string]$Task
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$pythonw = Join-Path $workspaceRoot '.venv\Scripts\pythonw.exe'
$entryPoint = Join-Path $projectRoot 'main.py'

if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "找不到 OK-WW 的 Python 运行时：$pythonw"
}

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "找不到 OK-WW 启动文件：$entryPoint"
}

$launchArguments = @('"' + $entryPoint + '"')
if ($Task) {
    $launchArguments += @('-t', $Task)
}

Start-Process `
    -FilePath $pythonw `
    -ArgumentList $launchArguments `
    -WorkingDirectory $projectRoot `
    -Verb RunAs `
    -WindowStyle Hidden
