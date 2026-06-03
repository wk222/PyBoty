$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$moves = @(
    @{ From = "core/assets/tools/tool_middleware_observability.py"; To = "core/systems/middleware/tool_middleware_observability.py" },
    @{ From = "core/assets/tools/tool_arg_repair_middleware.py";  To = "core/systems/middleware/tool_arg_repair_middleware.py"  },
    @{ From = "core/assets/tools/tool_eviction_middleware.py";    To = "core/systems/middleware/tool_eviction_middleware.py"    },
    @{ From = "core/assets/tools/todo_write.py";                  To = "core/systems/execution/todo_write.py"                  }
)

foreach ($m in $moves) {
    if (-not (Test-Path $m.From)) {
        Write-Host "SKIP missing: $($m.From)"
        continue
    }
    if (Test-Path $m.To) {
        Write-Host "SKIP target exists: $($m.To)"
        continue
    }
    $bytes = [System.IO.File]::ReadAllBytes($m.From)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    } else {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    }
    [System.IO.File]::WriteAllText($m.To, $text, $utf8NoBom)
    Remove-Item $m.From -Force
    Write-Host "moved: $($m.From) -> $($m.To)"
}
