$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$src = "core/assets/tools/tool_arg_repair.py"
$dst = "core/systems/middleware/tool_arg_repair.py"

if (-not (Test-Path $src)) { Write-Host "SKIP missing: $src"; exit 0 }
if (Test-Path $dst) { Write-Host "SKIP target exists: $dst"; exit 0 }

$bytes = [System.IO.File]::ReadAllBytes($src)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
} else {
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
}
[System.IO.File]::WriteAllText($dst, $text, $utf8NoBom)
Remove-Item $src -Force
Write-Host "moved: $src -> $dst"
