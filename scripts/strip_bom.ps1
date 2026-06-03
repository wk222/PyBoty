$ErrorActionPreference = "Stop"

$files = @(
    "core\assets\tools\tool_creation_support.py",
    "core\assets\tools\tool_creator.py",
    "core\assets\workflows\workflow_collaboration_runtime.py",
    "core\modes\capability_synthesis.py",
    "core\systems\agents\agent_creator.py",
    "core\systems\agents\agent_services.py",
    "core\systems\agents\agent_tool_sync.py",
    "core\systems\agents\subagent_governance.py",
    "core\systems\agents\subagent_runtime.py",
    "core\systems\agents\team_orchestrator.py",
    "core\systems\agents\__init__.py",
    "core\systems\apps\app_manager.py",
    "core\systems\apps\iterative_app_builder.py",
    "core\systems\apps\__init__.py",
    "core\systems\governance\subagent_sandbox.py",
    "core\systems\runtime\pybot_bootstrap.py"
)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach ($f in $files) {
    $bytes = [System.IO.File]::ReadAllBytes($f)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
        [System.IO.File]::WriteAllText($f, $text, $utf8NoBom)
        Write-Host "stripped BOM: $f"
    } else {
        Write-Host "no BOM: $f"
    }
}
