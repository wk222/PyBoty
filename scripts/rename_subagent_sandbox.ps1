$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$old = "core.systems.governance.subagent_sandbox"
$new = "core.systems.agents.subagent_sandbox"

$paths = @(
    "core/systems/agents/subagent_runtime.py",
    "core/systems/agents/subagent_governance.py",
    "core/systems/agents/__init__.py",
    "core/systems/apps/iterative_app_builder.py",
    "core/modes/factories.py",
    "tests/test_subagent_sandbox.py",
    "tests/test_subagent_isolation.py",
    "tests/test_subagent_runtime_registry.py",
    "tests/test_delegation_chain.py",
    "tests/test_agent_prompt_middleware.py"
)

$count = 0
foreach ($p in $paths) {
    if (-not (Test-Path $p)) { Write-Host "SKIP missing: $p"; continue }
    $bytes = [System.IO.File]::ReadAllBytes($p)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
        $hadBom = $true
    } else {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $hadBom = $false
    }
    if ($text -match [regex]::Escape($old)) {
        $newText = $text.Replace($old, $new)
        [System.IO.File]::WriteAllText($p, $newText, $utf8NoBom)
        Write-Host "updated: $p"
        $count += 1
    } else {
        if ($hadBom) {
            [System.IO.File]::WriteAllText($p, $text, $utf8NoBom)
            Write-Host "stripped BOM only: $p"
        } else {
            Write-Host "no change: $p"
        }
    }
}
Write-Host ""
Write-Host "Total updated: $count"
