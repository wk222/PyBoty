"""PyBot Core package.

Canonical product model:
  Tools -> Skills -> Agents -> Workflows -> Apps

This module uses a finer-grained internal export map for contributor
navigation. Those groups are implementation domains, not extra
user-facing product layers.

See ``docs/ARCHITECTURE.md`` for the canonical project model.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .version import get_pybot_version

_EXPORTS: dict[str, tuple[str, str]] = {
    # ── Domain 1: Infrastructure ─────────────────────────────────────
    "get_config": (".config", "get_config"),
    "get_agent_control_config": (".config", "get_agent_control_config"),
    "get_llm_config": (".config", "get_llm_config"),
    "reload_config": (".config", "reload_config"),
    "save_config": (".config", "save_config"),
    "ToolError": (".errors", "ToolError"),
    "ToolInputError": (".errors", "ToolInputError"),
    "ToolAuthorizationError": (".errors", "ToolAuthorizationError"),
    "ToolNotFoundError": (".errors", "ToolNotFoundError"),
    "ToolTimeoutError": (".errors", "ToolTimeoutError"),
    "ToolRateLimitError": (".errors", "ToolRateLimitError"),
    "extract_error_code": (".errors", "extract_error_code"),
    "redact_sensitive_text": (".errors", "redact_sensitive_text"),
    "format_error": (".errors", "format_error"),
    "RetryPolicy": (".retry_policy", "RetryPolicy"),
    "RetryConfig": (".retry_policy", "RetryConfig"),
    "create_default_retry_policy": (".retry_policy", "create_default_retry_policy"),
    "ProjectPaths": (".project_paths", "ProjectPaths"),
    "safe_resolve": (".path_utils", "safe_resolve"),
    "safe_join": (".path_utils", "safe_join"),
    "SessionEventQueue": (".session_events", "SessionEventQueue"),
    "EventBus": (".event_bus", "EventBus"),
    "event_bus": (".event_bus", "event_bus"),
    "Event": (".event_bus", "Event"),
    "EventType": (".event_bus", "EventType"),
    # ── Domain 2: LLM & Model ───────────────────────────────────────
    "build_system_prompt": (".prompts", "build_system_prompt"),
    # ── Domain 3: Knowledge & Memory ────────────────────────────────
    "Document": (".vector_store", "Document"),
    "SearchResult": (".vector_store", "SearchResult"),
    "VectorStoreBackend": (".vector_store", "VectorStoreBackend"),
    "ChromaVectorStore": (".vector_store", "ChromaVectorStore"),
    "InMemoryVectorStore": (".vector_store", "InMemoryVectorStore"),
    "create_vector_store": (".vector_store", "create_vector_store"),
    "DocumentPipeline": (".document_pipeline", "DocumentPipeline"),
    "ChunkConfig": (".document_pipeline", "ChunkConfig"),
    "KnowledgeSource": (".knowledge_sources", "KnowledgeSource"),
    "FileSource": (".knowledge_sources", "FileSource"),
    "DirectorySource": (".knowledge_sources", "DirectorySource"),
    "TextSource": (".knowledge_sources", "TextSource"),
    "URLSource": (".knowledge_sources", "URLSource"),
    "GitRepoSource": (".knowledge_sources", "GitRepoSource"),
    "KnowledgeManager": (".knowledge_sources", "KnowledgeManager"),
    "RetrievalConfig": (".knowledge_retrieval", "RetrievalConfig"),
    "retrieve_and_format": (".knowledge_retrieval", "retrieve_and_format"),
    "get_knowledge_tools": (".knowledge_tools", "get_knowledge_tools"),
    "MemoryManager": (".memory_manager", "MemoryManager"),
    "extract_key_facts": (".memory_manager", "extract_key_facts"),
    "SemanticMemoryManager": (".semantic_memory", "SemanticMemoryManager"),
    "encode_memory": (".memory_scoring", "encode_memory"),
    "composite_score": (".memory_scoring", "composite_score"),
    # ── Domain 4: Tool System ───────────────────────────────────────
    "ToolStorage": (".tool_storage", "ToolStorage"),
    "ToolContext": (".tool_storage", "ToolContext"),
    "ToolCreatorTool": (".tool_creator", "ToolCreatorTool"),
    "TemplateToolCreator": (".tool_creator", "TemplateToolCreator"),
    "ListTemplatesTool": (".tool_creator", "ListTemplatesTool"),
    "create_dynamic_tool": (".tool_creator", "create_dynamic_tool"),
    "get_tool_creator_tools": (".tool_creator", "get_tool_creator_tools"),
    "get_dynamic_tools": (".tool_creator", "get_dynamic_tools"),
    "TOOL_TEMPLATES": (".tool_templates", "TOOL_TEMPLATES"),
    "get_template": (".tool_templates", "get_template"),
    "list_templates": (".tool_templates", "list_templates"),
    "get_templates_by_category": (".tool_templates", "get_templates_by_category"),
    "get_template_prompt_section": (".tool_templates", "get_template_prompt_section"),
    "DynamicToolMiddleware": (".tool_middleware", "DynamicToolMiddleware"),
    "ToolChainExecutor": (".tool_chain", "ToolChainExecutor"),
    "get_tool_chain_tools": (".tool_chain", "get_tool_chain_tools"),
    "ToolCache": (".tool_cache", "ToolCache"),
    "cached_tool_call": (".tool_cache", "cached_tool_call"),
    "get_clarification_tools": (".clarification_tool", "get_clarification_tools"),
    "AskClarificationTool": (".clarification_tool", "AskClarificationTool"),
    "AnalyzeRequirementTool": (".clarification_tool", "AnalyzeRequirementTool"),
    # ── Domain 5: Agents ────────────────────────────────────────────
    "AgentControlPolicy": (".agent_control", "AgentControlPolicy"),
    "ToolRiskLevel": (".agent_control", "ToolRiskLevel"),
    "AgentCapabilityProfile": (".agent_capability_profile", "AgentCapabilityProfile"),
    "AgentMiddlewareProfile": (".agent_middleware_profile", "AgentMiddlewareProfile"),
    "AgentStorage": (".agent_storage", "AgentStorage"),
    "AgentDefinition": (".agent_storage", "AgentDefinition"),
    "AgentContext": (".agent_storage", "AgentContext"),
    "invoke_persisted_agent": (".agent_services", "invoke_persisted_agent"),
    "AgentCreatorTool": (".agent_creator", "AgentCreatorTool"),
    "DelegateToAgentTool": (".agent_creator", "DelegateToAgentTool"),
    "AskAgentTool": (".agent_creator", "AskAgentTool"),
    "ListAgentsTool": (".agent_creator", "ListAgentsTool"),
    "RemoveAgentTool": (".agent_creator", "RemoveAgentTool"),
    "get_agent_creator_tools": (".agent_creator", "get_agent_creator_tools"),
    "create_sub_agent_instance": (".subagent_runtime", "create_sub_agent_instance"),
    "ApprovalQueue": (".approval_queue", "ApprovalQueue"),
    "AgentTool": (".agent_as_tool", "AgentTool"),
    "TeamTool": (".agent_as_tool", "TeamTool"),
    "create_agent_tool": (".agent_as_tool", "create_agent_tool"),
    "create_team_tool": (".agent_as_tool", "create_team_tool"),
    "FeedbackStore": (".training", "FeedbackStore"),
    "FeedbackRecord": (".training", "FeedbackRecord"),
    "SocietyOfMind": (".society_of_mind", "SocietyOfMind"),
    "MindAgent": (".society_of_mind", "MindAgent"),
    "SocietyConfig": (".society_of_mind", "SocietyConfig"),
    # ── Domain 6: Orchestration & Workflow ──────────────────────────
    "PyFlowEngine": (".pyflow_engine", "PyFlowEngine"),
    "get_pyflow_tools": (".workflow_tools", "get_pyflow_tools"),
    "get_execution_loop_tools": (".execution_loop", "get_execution_loop_tools"),
    "TaskScheduler": (".task_scheduler", "TaskScheduler"),
    "ScheduledTask": (".task_scheduler", "ScheduledTask"),
    "SpeakerSelector": (".speaker_selection", "SpeakerSelector"),
    "RoundRobinSelector": (".speaker_selection", "RoundRobinSelector"),
    "LLMSelector": (".speaker_selection", "LLMSelector"),
    "RuleBasedSelector": (".speaker_selection", "RuleBasedSelector"),
    "RandomSelector": (".speaker_selection", "RandomSelector"),
    "TerminationCondition": (".termination", "TerminationCondition"),
    "MaxMessages": (".termination", "MaxMessages"),
    "MaxTokens": (".termination", "MaxTokens"),
    "Timeout": (".termination", "Timeout"),
    "TextMatch": (".termination", "TextMatch"),
    "AnyCondition": (".termination", "AnyCondition"),
    "AllConditions": (".termination", "AllConditions"),
    "ExternalSignal": (".termination", "ExternalSignal"),
    "PauseManager": (".pause_resume", "PauseManager"),
    "SimplePausableAgent": (".pause_resume", "SimplePausableAgent"),
    "PauseState": (".pause_resume", "PauseState"),
    # ── Domain 7: Safety & Governance ───────────────────────────────
    "Guardrail": (".guardrails", "Guardrail"),
    "run_with_guardrails": (".guardrails", "run_with_guardrails"),
    "LengthGuardrail": (".guardrails", "LengthGuardrail"),
    "JsonGuardrail": (".guardrails", "JsonGuardrail"),
    "RegexGuardrail": (".guardrails", "RegexGuardrail"),
    "InterventionHandler": (".intervention", "InterventionHandler"),
    "InterventionChain": (".intervention", "InterventionChain"),
    "ContentFilterHandler": (".intervention", "ContentFilterHandler"),
    "RateLimitHandler": (".intervention", "RateLimitHandler"),
    "LoggingHandler": (".intervention", "LoggingHandler"),
    # ── Domain 8: Middleware & Context ──────────────────────────────
    "MiddlewareStack": (".middleware_stack", "MiddlewareStack"),
    "ContextMiddleware": (".middleware_stack", "ContextMiddleware"),
    "MemoryMiddleware": (".middleware_stack", "MemoryMiddleware"),
    "ToolEvictionMiddleware": (".middleware_stack", "ToolEvictionMiddleware"),
    "BusMiddleware": (".middleware_stack", "BusMiddleware"),
    "PromptCachingMiddleware": (".middleware_stack", "PromptCachingMiddleware"),
    "PatchToolCallsMiddleware": (".patch_tool_calls", "PatchToolCallsMiddleware"),
    "TodoListMiddleware": (".todo_middleware", "TodoListMiddleware"),
    "SummarizationMiddleware": (".summarization_middleware", "SummarizationMiddleware"),
    "SummarizationConfig": (".summarization_middleware", "SummarizationConfig"),
    "LCToolEvictionMiddleware": (".tool_eviction_middleware", "LCToolEvictionMiddleware"),
    "LCMemoryMiddleware": (".lc_memory_middleware", "LCMemoryMiddleware"),
    "LCBusMiddleware": (".lc_bus_middleware", "LCBusMiddleware"),
    "BufferedChatContext": (".context_strategies", "BufferedChatContext"),
    "TokenLimitedChatContext": (".context_strategies", "TokenLimitedChatContext"),
    "HeadAndTailChatContext": (".context_strategies", "HeadAndTailChatContext"),
    "CompositeContextStrategy": (".context_strategies", "CompositeContextStrategy"),
    # ── Domain 9: Backend & IO ──────────────────────────────────────
    "BackendProtocol": (".backend_protocol", "BackendProtocol"),
    "SandboxBackendProtocol": (".backend_protocol", "SandboxBackendProtocol"),
    "LocalFilesystemBackend": (".backend_protocol", "LocalFilesystemBackend"),
    "LocalSandboxBackend": (".backend_protocol", "LocalSandboxBackend"),
    "CompositeBackend": (".backend_protocol", "CompositeBackend"),
    "FileInfo": (".backend_protocol", "FileInfo"),
    "ExecResult": (".backend_protocol", "ExecResult"),
    "FileOperationError": (".backend_protocol", "FileOperationError"),
    "WriteResult": (".backend_protocol", "WriteResult"),
    "EditResult": (".backend_protocol", "EditResult"),
    "BackendFactory": (".backend_protocol", "BackendFactory"),
    "resolve_backend": (".backend_protocol", "resolve_backend"),
    "DockerSandboxBackend": (".docker_sandbox", "DockerSandboxBackend"),
    "SkillRegistry": (".skill_registry", "SkillRegistry"),
    "SkillDefinition": (".skill_models", "SkillDefinition"),
    "SkillMarketplace": (".skill_marketplace", "SkillMarketplace"),
    "get_marketplace_tools": (".skill_marketplace", "get_marketplace_tools"),
    "WorkspaceManager": (".workspace_manager", "WorkspaceManager"),
    "UvEnvManager": (".uv_env_manager", "UvEnvManager"),
    "UvEnvDefinition": (".uv_env_manager", "UvEnvDefinition"),
    "PyHubClient": (".pyhub_client", "PyHubClient"),
    # ── Domain 10: Application & Integration ────────────────────────
    "AppManager": (".app_manager", "AppManager"),
    "AppDefinition": (".app_manager", "AppDefinition"),
    "get_app_creator_tools": (".app_creator", "get_app_creator_tools"),
    "set_app_manager": (".app_creator", "set_app_manager"),
    "get_app_verifier_tools": (".app_verifier", "get_app_verifier_tools"),
    "VerifyAppTool": (".app_verifier", "VerifyAppTool"),
    "ReadAppFileTool": (".app_verifier", "ReadAppFileTool"),
    "set_verifier_app_manager": (".app_verifier", "set_verifier_app_manager"),
    "CapabilityBus": (".capability_bus", "CapabilityBus"),
    "CapabilityLayer": (".capability_bus", "CapabilityLayer"),
    "get_capability_bus_tools": (".capability_bus", "get_capability_bus_tools"),
    "ContextWindowManager": (".context_manager", "ContextWindowManager"),
    "ContextConfig": (".context_manager", "ContextConfig"),
    "count_tokens_approx": (".context_manager", "count_tokens_approx"),
    "EvalFramework": (".eval_framework", "EvalFramework"),
    "get_eval_tools": (".eval_framework", "get_eval_tools"),
    "get_private_keys": (".private_state", "get_private_keys"),
    "register_private_keys": (".private_state", "register_private_keys"),
    "ApprovalDashboard": (".approval_dashboard", "ApprovalDashboard"),
    "DashboardFilter": (".approval_dashboard", "DashboardFilter"),
    "build_delegation_chain": (".subagent_governance", "build_delegation_chain"),
    "format_delegation_tree": (".subagent_governance", "format_delegation_tree"),
    "load_agents_yaml": (".yaml_config", "load_agents_yaml"),
    "load_tasks_yaml": (".yaml_config", "load_tasks_yaml"),
    "auto_discover_yaml": (".yaml_config", "auto_discover_yaml"),
    "AgentSpec": (".component_serialization", "AgentSpec"),
    "ToolSpec": (".component_serialization", "ToolSpec"),
    "TeamSpec": (".component_serialization", "TeamSpec"),
    "WorkflowSpec": (".component_serialization", "WorkflowSpec"),
    "serialize_component": (".component_serialization", "serialize_component"),
    "deserialize_component": (".component_serialization", "deserialize_component"),
    "to_json": (".component_serialization", "to_json"),
    "from_json": (".component_serialization", "from_json"),
}

__all__ = list(_EXPORTS)

__version__ = get_pybot_version()
__author__ = "Patent Applicant"
__patent__ = "一种具有自主工具创建和智能体创建能力的智能体系统"


def __getattr__(name: str) -> Any:
    """Resolve legacy exports lazily on first access."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports in interactive environments."""
    return sorted(set(globals()) | set(__all__))
