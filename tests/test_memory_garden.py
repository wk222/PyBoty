from __future__ import annotations

import json
import pytest
from pathlib import Path
from core.assets.agents import (
    AgentStorage,
    AgentDefinition,
    SubagentRegistry,
    create_sub_agent_instance,
)

def test_memory_garden_tools_injection(temp_paths):
    storage = AgentStorage(str(temp_paths.agents_dir))
    registry = SubagentRegistry()
    
    # Mock LLM
    def mock_llm_factory(**kwargs):
        from langchain_core.messages import AIMessage
        class MockLLM:
            def invoke(self, *args, **kwargs): return AIMessage(content="ok")
            def bind_tools(self, *args, **kwargs): return self
        return MockLLM()

    # 1. Create a researcher agent (should have garden tools by default now)
    agent_def = AgentDefinition(
        name="explorer",
        role="researcher",
        description="test explorer",
        system_prompt="explore the garden",
        capability_profile="researcher"
    )
    storage.add_agent(agent_def)
    
    # 2. Create instance
    runtime = create_sub_agent_instance(
        agent_def=agent_def,
        agent_storage=storage,
        registry=registry,
        project_paths=temp_paths,
        llm_factory=mock_llm_factory,
    )
    
    # 3. Check tools
    tool_names = [t.name for t in runtime.tools]
    assert "list_garden_notes" in tool_names
    assert "read_garden_note" in tool_names
    assert "update_garden_note" in tool_names
    assert "search_garden" in tool_names

def test_markdown_garden_functional_flow(temp_paths):
    from core.systems.memory.markdown_garden import MarkdownGardenManager
    
    mgr = MarkdownGardenManager(workspace_dir=str(temp_paths.root_dir))
    
    # 1. Update/Create note
    mgr.update_note("engineering/test_note.md", "This is a test contract.")
    
    # 2. List notes
    notes = mgr.list_notes()
    assert "engineering/test_note.md" in notes
    
    # 3. Read note
    content = mgr.read_note("engineering/test_note.md")
    assert "This is a test contract." in content
    
    # 4. Search
    results = mgr.search_notes("contract")
    assert len(results) == 1
    assert results[0]["path"] == "engineering/test_note.md"

def test_recursive_summarization(temp_paths):
    from core.systems.memory.markdown_garden import MarkdownGardenManager
    
    # Mock summarizer
    def mock_summarizer(text):
        return "This is a summary of the long text."
        
    mgr = MarkdownGardenManager(
        workspace_dir=str(temp_paths.root_dir),
        summarize_fn=mock_summarizer,
        max_note_chars=100 # Low limit for testing
    )
    
    # 1. Create a large note
    long_content = "A" * 200
    mgr.update_note("long_note.md", long_content)
    
    # 2. Check main note
    main_content = mgr.read_note("long_note.md")
    assert "This is a summary of the long text." in main_content
    assert "(Summarized)" in main_content
    assert "_archive_" in main_content
    
    # 3. Check archive
    notes = mgr.list_notes()
    archive_note = next(n for n in notes if "_archive_" in n)
    archive_content = mgr.read_note(archive_note)
    assert "A" * 200 in archive_content

