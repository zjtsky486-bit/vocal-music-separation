[CmdletBinding()]
param(
    [ValidateSet('cuda', 'dml')][string]$Backend = 'cuda',
    [string]$PythonPath,
    [string]$ModelPath,
    [string]$RuntimeDir,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$engineDir = Join-Path $repoRoot 'plugins/music'
$cacheDir = Join-Path $repoRoot '.cache'
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $engineDir 'python' }
$RuntimeDir = [IO.Path]::GetFullPath($RuntimeDir)
if (-not $RuntimeDir.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'RuntimeDir must be inside this repository; dependencies stay project-local.'
}
$runtimePython = Join-Path $RuntimeDir 'Scripts/python.exe'
$backendFile = Join-Path $RuntimeDir '.runtime-backend.json'

function Get-CFreeGiB { return (Get-PSDrive -Name C -ErrorAction Stop).Free / 1GB }
function Assert-Space([double]$Minimum = 40) {
    if ((Get-CFreeGiB) -le $Minimum) { throw "C: has at most $Minimum GiB free; installation stopped." }
}
function Quote-Argument([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
function Stop-OwnedProcessTree([Diagnostics.Process]$Owner) {
    $Owner.Refresh()
    if ($Owner.HasExited) { return }
    $started = $Owner.StartTime.ToUniversalTime()
    $snapshot = @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue)
    $ids = [Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($Owner.Id)
    do {
        $added = $false
        foreach ($item in $snapshot) {
            if ($ids.Contains([int]$item.ParentProcessId) -and $item.CreationDate -and $item.CreationDate.ToUniversalTime() -ge $started) {
                if ($ids.Add([int]$item.ProcessId)) { $added = $true }
            }
        }
    } while ($added)
    foreach ($item in ($snapshot | Where-Object { $ids.Contains([int]$_.ProcessId) -and $_.ProcessId -ne $Owner.Id } | Sort-Object CreationDate -Descending)) {
        $child = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        # Recheck creation time to avoid stopping a different process after PID reuse.
        if ($child -and [Math]::Abs(($child.StartTime.ToUniversalTime() - $item.CreationDate.ToUniversalTime()).TotalMilliseconds) -lt 10) {
            Stop-Process -InputObject $child -Force -ErrorAction SilentlyContinue
        }
    }
    $Owner.Refresh()
    if (-not $Owner.HasExited) { Stop-Process -InputObject $Owner -Force -ErrorAction SilentlyContinue }
}
function Invoke-Guarded([string]$Executable, [string[]]$Arguments) {
    Assert-Space 41
    $tag = [guid]::NewGuid().ToString('N')
    $stdout = Join-Path $cacheDir "install-$tag.stdout.log"
    $stderr = Join-Path $cacheDir "install-$tag.stderr.log"
    $line = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $child = Start-Process -FilePath $Executable -ArgumentList $line -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    try {
        while (-not $child.HasExited) {
            Assert-Space
            Start-Sleep -Milliseconds 250
            $child.Refresh()
        }
        $child.WaitForExit()
        if ($child.ExitCode -ne 0) { throw "Command exited $($child.ExitCode). See $stderr" }
        return @{ stdout = (Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue); stderr = $stderr }
    } finally {
        Stop-OwnedProcessTree $child
        $child.Dispose()
    }
}

$previousEnv = @{}
try {
    if (-not $PythonPath) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $launcher) { throw 'Provide -PythonPath to an installed CPython 3.12 x64 executable, or install Python 3.12 yourself.' }
        $PythonPath = (& $launcher.Source -3.12 -B -c 'import sys;print(sys.executable)').Trim()
        if ($LASTEXITCODE -ne 0) { throw 'py -3.12 is unavailable; provide -PythonPath.' }
    }
    $PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
    $version = & $PythonPath -B -c 'import json,sys,struct;print(json.dumps([*sys.version_info[:2],struct.calcsize("P")*8]))'
    if ($LASTEXITCODE -ne 0 -or (($version | ConvertFrom-Json) -join ',') -ne '3,12,64') { throw 'The runtime requires CPython 3.12 x64.' }
    if (Test-Path -LiteralPath $backendFile) {
        $existing = Get-Content -LiteralPath $backendFile -Raw | ConvertFrom-Json
        if ($existing.backend -ne $Backend) { throw 'This venv belongs to a different ORT backend. Choose another project-local -RuntimeDir; do not mix CUDA and DirectML packages.' }
    }
    if (Test-Path -LiteralPath $runtimePython) {
        $conflict = & $runtimePython -B -c 'import importlib.metadata as m,json;names={d.metadata["Name"].lower() for d in m.distributions()};print(json.dumps(sorted(names & {"onnxruntime","onnxruntime-gpu","onnxruntime-directml"})))'
        $installed = @($conflict | ConvertFrom-Json)
        $wanted = if ($Backend -eq 'cuda') { 'onnxruntime-gpu' } else { 'onnxruntime-directml' }
        if ($LASTEXITCODE -ne 0 -or @($installed | Where-Object { $_ -ne $wanted }).Count) { throw 'The target venv contains another ORT build. Use a new RuntimeDir instead of overwriting it.' }
    }
    $requirements = Join-Path $repoRoot "requirements/$Backend.txt"
    if ($ModelPath) { $ModelPath = (Resolve-Path -LiteralPath $ModelPath).Path }
    $plan = @{ ok = $true; dryRun = [bool]$DryRun; backend = $Backend; python = $PythonPath; runtimePython = $runtimePython; requirements = $requirements; modelPath = $ModelPath; cFreeGiB = [Math]::Round((Get-CFreeGiB), 2); driverInstallation = $false }
    Assert-Space 41
    if ($DryRun) { $plan | ConvertTo-Json -Depth 5 -Compress; exit 0 }
    New-Item -ItemType Directory -Force -Path $cacheDir, (Join-Path $cacheDir 'tmp') | Out-Null
    foreach ($name in @('PIP_CACHE_DIR', 'TEMP', 'TMP', 'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE')) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $env:PIP_CACHE_DIR = Join-Path $cacheDir 'pip'
    $env:TEMP = Join-Path $cacheDir 'tmp'
    $env:TMP = $env:TEMP
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONNOUSERSITE = '1'
    if (-not (Test-Path -LiteralPath $runtimePython)) { $null = Invoke-Guarded $PythonPath @('-m', 'venv', $RuntimeDir) }
    $null = Invoke-Guarded $runtimePython @('-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '--cache-dir', (Join-Path $cacheDir 'pip'), '--disable-pip-version-check', '--only-binary=:all:', '--requirement', $requirements)
    $null = Invoke-Guarded $runtimePython @('-m', 'pip', 'check')
    Assert-Space
    @{ backend = $Backend; python = '3.12'; requirements = "requirements/$Backend.txt" } | ConvertTo-Json | Set-Content -LiteralPath $backendFile -Encoding UTF8
    if ($ModelPath) {
        $import = Invoke-Guarded $runtimePython @((Join-Path $repoRoot 'scripts/import-model.py'), '--model', $ModelPath, '--engine-dir', $engineDir)
        $plan.model = $import.stdout | ConvertFrom-Json
    } else {
        $plan.modelMissing = -not (Test-Path -LiteralPath (Join-Path $engineDir 'roformer-port/artifacts-balanced/mel_band_roformer_kim_T241.onnx'))
        if ($plan.modelMissing) { $plan.next = 'Import your validated T241 model or explicitly run scripts/export-model.py; no unverified T241 URL is used.' }
    }
    $plan.dryRun = $false
    $plan | ConvertTo-Json -Depth 5 -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
} finally {
    foreach ($name in $previousEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process') }
}
