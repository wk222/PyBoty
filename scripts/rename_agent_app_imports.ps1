$ErrorActionPreference = "Stop"

$replacements = @(
    @{ Old = "core.systems.agents.agent_capability_profile"; New = "core.assets.agents.capability_profile" },
    @{ Old = "core.systems.agents.agent_middleware_profile"; New = "core.assets.agents.middleware_profile" },
    @{ Old = "core.systems.agents.agent_storage"; New = "core.assets.agents.storage" },
    @{ Old = "core.systems.agents.delegation_payload"; New = "core.assets.agents.delegation_payload" },
    @{ Old = "core.systems.agents.agent_role_policy"; New = "core.assets.agents.role_policy" },
    @{ Old = "core.systems.agents.agent_tool_inventory"; New = "core.assets.agents.tool_inventory" },
    @{ Old = "core.systems.apps.app_templates"; New = "core.assets.apps.templates" },
    @{ Old = "core.systems.apps.app_packager"; New = "core.assets.apps.packager" },
    @{ Old = "from .agent_capability_profile import"; New = "from core.assets.agents.capability_profile import" },
    @{ Old = "from .agent_middleware_profile import"; New = "from core.assets.agents.middleware_profile import" },
    @{ Old = "from .agent_storage import"; New = "from core.assets.agents.storage import" },
    @{ Old = "from .delegation_payload import"; New = "from core.assets.agents.delegation_payload import" },
    @{ Old = "from .agent_role_policy import"; New = "from core.assets.agents.role_policy import" },
    @{ Old = "from .agent_tool_inventory import"; New = "from core.assets.agents.tool_inventory import" },
    @{ Old = "from .app_templates import"; New = "from core.assets.apps.templates import" },
    @{ Old = "from .app_packager import"; New = "from core.assets.apps.packager import" }
)

$targets = Get-ChildItem -Recurse -Include *.py -Path core, tests, web, scripts, agent.py, api_server.py -ErrorAction SilentlyContinue
$updated = 0
foreach ($f in $targets) {
    $content = Get-Content $f.FullName -Raw -Encoding UTF8
    $orig = $content
    foreach ($r in $replacements) {
        $content = $content.Replace($r.Old, $r.New)
    }
    if ($content -ne $orig) {
        Set-Content -Path $f.FullName -Value $content -Encoding UTF8 -NoNewline
        $updated++
        Write-Host "updated: $($f.FullName.Substring($PWD.Path.Length + 1))"
    }
}
Write-Host "TOTAL FILES UPDATED: $updated"
