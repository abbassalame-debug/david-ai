# install.ps1 — Install David Premiere CEP Panel
# Run once from the premiere_cep folder of the repository.
# The queue file lives in DAVID_DATA (default: <repo>\data) and is shared with design_premiere.py.
# No admin rights needed (installs to per-user CEP folder).

$src  = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = if ($env:DAVID_DATA) { $env:DAVID_DATA } else { Join-Path (Split-Path -Parent $src) "data" }
$dest = "$env:APPDATA\Adobe\CEP\extensions\david-premiere-panel"

Write-Host "Installing David Premiere CEP panel..."
Write-Host "  Source : $src"
Write-Host "  Dest   : $dest"

# Copy panel files
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Copy-Item $src -Destination $dest -Recurse -Exclude "install.ps1"
Write-Host "  Files copied."

# Tell the panel where the queue is (config.js is read by index.html)
$qjs = ($dataDir -replace "\\", "/") + "/premiere_queue.json"
"var DAVID_QUEUE_PATH = '$qjs';" | Out-File -FilePath (Join-Path $dest "config.js") -Encoding ascii

# Enable CEP debug mode (bypasses extension signing requirement)
# CSXS 12 is what CSXS\manifest.xml requires (RequiredRuntime CSXS 12.0, Premiere 2024 and later, including 2026); 11 is kept for an
# older Premiere. (This used to set only 11 and 10.) test_adobe_install.py checks the versions here include the manifest's runtime.
foreach ($ver in @("12", "11")) {
    $key = "HKCU:\Software\Adobe\CSXS.$ver"
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    Set-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -Type String
    Write-Host "  CSXS.$ver debug mode enabled."
}

# Create empty queue file if missing
$queue = Join-Path $dataDir "premiere_queue.json"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
if (-not (Test-Path $queue)) {
    '{"commands":[]}' | Out-File -FilePath $queue -Encoding utf8
    Write-Host "  Queue file created: $queue"
}

Write-Host ""
Write-Host "Done. Restart Premiere Pro, then open the panel:" -ForegroundColor Green
Write-Host "  Window -> Extensions -> David AI" -ForegroundColor Cyan
