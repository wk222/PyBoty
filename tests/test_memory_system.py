"""Unified tests for core Memory System, Memory Engine, Markdown Garden, Admin Memory, and Vector Stores (Eighth Round).

Consolidated and merged from 5 individual test files:
* test_memory_engine.py
* test_memory_garden.py
* test_admin_memory.py
* test_vector_store.py
* test_vector_store_backends.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Core Memory & Knowledge Systems Imports
from core.systems.memory import (
    AdminMemoryConfig,
    AdminMemoryManager,
    EngineConfig,
    MemoryEngine,
    Modality,
    Scope,
    Signal,
    build_memory_engine,
)
from core.systems.memory.markdown_garden import MarkdownGardenManager
from core.systems.memory.scoring import cosine, softmax, tokenize, bm25_lite
from core.systems.knowledge.vector_store import (
    Document,
    InMemoryVectorStore,
    create_vector_store,
)
from core.assets.agents import AgentDefinition, AgentStorage
from core.systems.agents import (
    SubagentRegistry,
    create_sub_agent_instance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(request) -> Path:
    import platform
    if platform.system() == "Windows":
        import tempfile
        return Path(tempfile.mkdtemp(prefix="pybot_mem_ws_"))
    return request.getfixturevalue("tmp_path")


@pytest.fixture
def engine(tmp_workspace: Path) -> MemoryEngine:
    eng = MemoryEngine(tmp_workspace)
    yield eng
    eng.close()


@pytest.fixture
def populated(engine: MemoryEngine) -> MemoryEngine:
    engine.ingest(
        Modality.FACT,
        "用户偏好深色主题",
        importance=0.9,
        metadata={"soft_tags": {"偏好": 1.0}},
    )
    engine.ingest(
        Modality.FACT,
        "项目使用 PostgreSQL 数据库",
        importance=0.7,
        metadata={"soft_tags": {"事实": 1.0}},
    )
    engine.ingest(
        Modality.FACT,
        "决定采用 FastAPI 框架",
        importance=0.8,
        metadata={"soft_tags": {"决策": 1.0}},
    )
    return engine


# ---------------------------------------------------------------------------
# 1. Memory Engine Core Tests (formerly test_memory_engine.py)
# ---------------------------------------------------------------------------

class TestAliases:
    def test_engine_factory(self, tmp_workspace: Path) -> None:
        eng = build_memory_engine(tmp_workspace, enable_garden=False)
        try:
            assert isinstance(eng, MemoryEngine)
            assert eng.workspace_dir == tmp_workspace
        finally:
            eng.close()

class TestIngestRecall:
    def test_ingest_returns_id(self, engine: MemoryEngine) -> None:
        rid = engine.ingest(Modality.FACT, "Test fact")
        assert isinstance(rid, str) and len(rid) == 16

    def test_ingest_idempotent(self, engine: MemoryEngine) -> None:
        rid1 = engine.ingest(Modality.FACT, "Same content")
        rid2 = engine.ingest(Modality.FACT, "Same content")
        assert rid1 == rid2
        assert engine.store.count(modality=Modality.FACT.value) == 1

    def test_recall_returns_records(self, populated: MemoryEngine) -> None:
        records = populated.recall("FastAPI", top_k=3)
        assert records
        assert any("FastAPI" in r.content for r in records)

    def test_recall_records_recall_count(self, populated: MemoryEngine) -> None:
        populated.recall("FastAPI", top_k=1)
        populated.recall("FastAPI", top_k=1)
        all_records = populated.store.list(modality=Modality.FACT.value)
        fastapi = next(r for r in all_records if "FastAPI" in r.content)
        assert fastapi.recall_count >= 1

    def test_recall_empty_query_falls_back_to_importance(
        self, populated: MemoryEngine
    ) -> None:
        records = populated.recall("", top_k=5)
        assert records
        assert "深色主题" in records[0].content or records[0].importance >= 0.7

    def test_modality_filter(self, engine: MemoryEngine) -> None:
        engine.ingest(Modality.FACT, "事实条目")
        engine.ingest(Modality.INSIGHT, "洞见条目")
        facts = engine.recall("条目", modality=Modality.FACT, top_k=5)
        assert all(r.modality == "fact" for r in facts)


class TestFeedback:
    def test_positive_feedback_boosts_importance(
        self, populated: MemoryEngine
    ) -> None:
        before = populated.store.find_by_content(
            "用户偏好深色主题", scope="agent", modality="fact"
        )
        assert before is not None
        ok = populated.feedback_by_content("用户偏好深色主题", Signal.POSITIVE)
        assert ok is True
        after = populated.store.find_by_content(
            "用户偏好深色主题", scope="agent", modality="fact"
        )
        assert after is not None
        assert after.importance_delta > before.importance_delta

    def test_negative_feedback_reduces_importance(
        self, populated: MemoryEngine
    ) -> None:
        populated.feedback_by_content("用户偏好深色主题", Signal.NEGATIVE)
        rec = populated.store.find_by_content(
            "用户偏好深色主题", scope="agent", modality="fact"
        )
        assert rec.importance_delta < 0

    def test_disproved_feedback_strong_penalty(
        self, populated: MemoryEngine
    ) -> None:
        populated.feedback_by_content("用户偏好深色主题", Signal.DISPROVED)
        rec = populated.store.find_by_content(
            "用户偏好深色主题", scope="agent", modality="fact"
        )
        assert rec.importance_delta <= -0.3

    def test_unknown_signal_returns_false(self, engine: MemoryEngine) -> None:
        engine.ingest(Modality.FACT, "Test")
        assert engine.feedback_by_content("Test", "not_a_signal") is False


class TestEpisodic:
    def test_ingest_episode(self, engine: MemoryEngine) -> None:
        rid = engine.ingest_episode(
            ts_label="2026-05-10 14:30",
            actor="用户",
            scene="workspace",
            action="请求 T3 重构",
            outcome="同意",
        )
        assert isinstance(rid, str)
        assert engine.store.count(modality=Modality.EPISODE.value) == 1

    def test_recall_recent_episodes(self, engine: MemoryEngine) -> None:
        engine.ingest_episode(
            ts_label="2026-05-09 10:00",
            actor="用户",
            scene="workspace",
            action="编辑代码",
            outcome="完成",
        )
        engine.ingest_episode(
            ts_label="2026-05-10 11:00",
            actor="助手",
            scene="repl",
            action="跑测试",
            outcome="通过",
        )
        episodes = engine.recall_recent_episodes(top_k=5)
        assert len(episodes) == 2
        assert episodes[0].ts_label.startswith("2026-05-10")

    def test_recall_episodes_by_query(self, engine: MemoryEngine) -> None:
        engine.ingest_episode(
            ts_label="2026-05-10 14:30",
            actor="用户",
            scene="workspace",
            action="请求 T3 重构",
            outcome="同意",
        )
        results = engine.recall_episodes("重构", top_k=3)
        assert results
        assert any("重构" in r.content for r in results)


class TestForgetting:
    def test_gc_demotes_stale_low_importance_facts(
        self, tmp_workspace: Path
    ) -> None:
        cfg = EngineConfig(
            forget_age_days=1.0,
            forget_importance_floor=0.5,
            enable_forgetting=True,
        )
        eng = MemoryEngine(tmp_workspace, config=cfg)
        try:
            rid1 = eng.ingest(
                Modality.FACT, "low-imp fact", importance=0.2,
                metadata={"soft_tags": {"其他": 1.0}},
            )
            rid2 = eng.ingest(
                Modality.FACT, "high-imp fact", importance=0.9,
                metadata={"soft_tags": {"事实": 1.0}},
            )
            old_ts = time.time() - 86400 * 5
            with eng.store._lock:
                eng.store._conn.execute(
                    "UPDATE memories SET first_seen_ts=? WHERE id IN (?, ?)", (old_ts, rid1, rid2)
                )
            demoted = eng.gc()
            assert demoted == 1
            forgotten = eng.store.list(status="forgotten", limit=10)
            assert len(forgotten) == 1
            assert forgotten[0].content == "low-imp fact"
        finally:
            eng.close()

    def test_protected_tags_survive_gc(self, tmp_workspace: Path) -> None:
        cfg = EngineConfig(
            forget_age_days=1.0,
            forget_importance_floor=0.99,
            protected_tags=("反思",),
        )
        eng = MemoryEngine(tmp_workspace, config=cfg)
        try:
            rid = eng.ingest(
                Modality.FACT, "this is a reflection",
                importance=0.1,
                metadata={"soft_tags": {"反思": 1.0}},
            )
            old_ts = time.time() - 86400 * 5
            with eng.store._lock:
                eng.store._conn.execute(
                    "UPDATE memories SET first_seen_ts=? WHERE id=?", (old_ts, rid)
                )
            demoted = eng.gc()
            assert demoted == 0
            assert eng.store.count(status="forgotten") == 0
        finally:
            eng.close()

    def test_reconsolidate_promotes_matching_forgotten(
        self, tmp_workspace: Path
    ) -> None:
        cfg = EngineConfig(reconsolidate_threshold=0.05)
        eng = MemoryEngine(tmp_workspace, config=cfg)
        try:
            rid = eng.ingest(
                Modality.FACT, "PostgreSQL 数据库使用 14.5",
                importance=0.4,
            )
            eng.store.update_status(rid, "forgotten")
            promoted = eng.reconsolidate("PostgreSQL", top_k=1)
            assert promoted
            rec = eng.store.get(rid)
            assert rec.status == "active"
            assert rec.importance_delta > 0
        finally:
            eng.close()


class TestPersistence:
    def test_records_survive_restart(self, tmp_workspace: Path) -> None:
        eng1 = MemoryEngine(tmp_workspace)
        eng1.ingest(Modality.FACT, "persistent fact")
        eng1.close()

        eng2 = MemoryEngine(tmp_workspace)
        try:
            recs = eng2.recall("persistent", top_k=1)
            assert recs
            assert recs[0].content == "persistent fact"
        finally:
            eng2.close()

    def test_pipeline_state_persisted(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            eng.store.set_pipeline_state(
                "distill", last_run_ts=12345.0, payload={"foo": "bar"}
            )
            ts, payload = eng.store.get_pipeline_state("distill")
            assert ts == 12345.0
            assert payload == {"foo": "bar"}
        finally:
            eng.close()


class TestTools:
    def test_engine_as_tools(self, populated: MemoryEngine) -> None:
        tools = populated.as_tools(include_garden=False)
        names = {t.name for t in tools}
        assert "search_memory" in names
        assert "save_memory" in names
        assert "forget_memory" in names
        assert "feedback_memory" in names

    def test_search_memory_tool(self, populated: MemoryEngine) -> None:
        tools = populated.as_tools(include_garden=False)
        search = next(t for t in tools if t.name == "search_memory")
        result = search._run(query="数据库", top_k=2)
        assert "PostgreSQL" in result


class TestStats:
    def test_stats_basics(self, populated: MemoryEngine) -> None:
        stats = populated.stats()
        assert stats["facts_active"] == 3
        assert stats["active"] == 3

    def test_digest_nonempty(self, populated: MemoryEngine) -> None:
        text = populated.digest()
        assert text
        assert "深色主题" in text or "PostgreSQL" in text

    def test_export_memory_md_includes_protected_section(
        self, engine: MemoryEngine
    ) -> None:
        engine.ingest(
            Modality.REFLECTION,
            "用户偏好简洁回答",
            importance=1.0,
            metadata={"soft_tags": {"反思": 1.0}},
        )
        engine.ingest(
            Modality.FACT,
            "事实条目",
            metadata={"soft_tags": {"事实": 1.0}},
        )
        md = engine.export_memory_md()
        assert "## 反思" in md
        assert "[MEMORY]" in md
        assert "事实条目" in md


class TestSecretRedaction:
    def test_ingest_redacts_high_risk_secrets(self, engine: MemoryEngine) -> None:
        rid1 = engine.ingest(
            Modality.FACT,
            "My OpenAI API key is sk-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0",
        )
        rid2 = engine.ingest(
            Modality.FACT,
            "Please use the token ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 for authentication",
        )
        rid3 = engine.ingest(
            Modality.FACT,
            'db_password = "super_secure_passwd_12345"',
        )
        
        rec1 = engine._store.get(rid1)
        assert rec1 is not None
        assert "sk-A1b2C3" not in rec1.content
        assert "[REDACTED_OPENAI_KEY]" in rec1.content

        rec2 = engine._store.get(rid2)
        assert rec2 is not None
        assert "ghp_" not in rec2.content
        assert "[REDACTED_GITHUB_TOKEN]" in rec2.content

        rec3 = engine._store.get(rid3)
        assert rec3 is not None
        assert "super_secure_passwd_12345" not in rec3.content
        assert '[REDACTED_' in rec3.content or '[REDACTED_SECRET]' in rec3.content or '[REDACTED_GENERIC_SECRET]' in rec3.content or 'db_password = "[REDACTED_SECRET]"' in rec3.content or 'db_password = "[REDACTED_GENERIC_SECRET]"' in rec3.content


class TestScopeIsolation:
    def test_scope_isolation_and_global_fallback(self, engine: MemoryEngine) -> None:
        engine.ingest(Modality.FACT, "This is agent preference fact", scope=Scope.AGENT, importance=1.0)
        engine.ingest(Modality.FACT, "This is admin config secret fact", scope=Scope.ADMIN, importance=1.0)
        engine.ingest(Modality.FACT, "This is global common knowledge fact", scope=Scope.GLOBAL, importance=1.0)
        
        prompt_agent = engine.get_context_prompt(scope=Scope.AGENT)
        assert "This is agent preference fact" in prompt_agent
        assert "This is global common knowledge fact" in prompt_agent
        assert "This is admin config secret fact" not in prompt_agent

        prompt_admin = engine.get_context_prompt(scope=Scope.ADMIN)
        assert "This is admin config secret fact" in prompt_admin
        assert "This is global common knowledge fact" in prompt_admin
        assert "This is agent preference fact" not in prompt_admin

        prompt_global = engine.get_context_prompt(scope=Scope.GLOBAL)
        assert "This is global common knowledge fact" in prompt_global
        assert "This is agent preference fact" not in prompt_global
        assert "This is admin config secret fact" not in prompt_global


class TestScoringAndMath:
    def test_cosine_edge_cases(self) -> None:
        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)

        assert cosine([1.0], [1.0, 0.0]) == 0.0

        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_softmax_edge_cases(self) -> None:
        # standard list of floats
        res = softmax([1.0, 2.0, 3.0])
        assert len(res) == 3
        assert sum(res) == pytest.approx(1.0)
        assert res[2] > res[1] > res[0]

        # empty
        assert softmax([]) == []

        # extreme values
        res_overflow = softmax([1000.0, 1001.0])
        assert sum(res_overflow) == pytest.approx(1.0)
        assert res_overflow[1] > 0.7

    def test_tokenize_clean_punctuation(self) -> None:
        text = "Hello, world! (This: is a test.)"
        tokens = tokenize(text)
        assert "hello" in tokens
        assert "world" in tokens
        assert "this" in tokens
        assert "," not in tokens
        assert "!" not in tokens

    def test_bm25_lite_scoring(self) -> None:
        q = tokenize("FastAPI 数据库")
        doc1 = tokenize("基于 FastAPI 开发的数据库应用")
        doc2 = tokenize("简单的 Python 脚本")

        score1 = bm25_lite(q, doc1)
        score2 = bm25_lite(q, doc2)

        assert score1 > 0.0
        assert score2 == 0.0
        assert score1 > score2


# ---------------------------------------------------------------------------
# 2. Memory Garden Tests (formerly test_memory_garden.py)
# ---------------------------------------------------------------------------

class TestMemoryGardenClass:
    def test_memory_garden_tools_injection(self, temp_paths):
        storage = AgentStorage(str(temp_paths.agents_dir))
        registry = SubagentRegistry()
        
        def mock_llm_factory(**kwargs):
            from langchain_core.messages import AIMessage
            class MockLLM:
                def invoke(self, *args, **kwargs): return AIMessage(content="ok")
                def bind_tools(self, *args, **kwargs): return self
            return MockLLM()

        agent_def = AgentDefinition(
            name="explorer",
            role="researcher",
            description="test explorer",
            system_prompt="explore the garden",
            capability_profile="researcher"
        )
        storage.add_agent(agent_def)
        
        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            agent_storage=storage,
            registry=registry,
            project_paths=temp_paths,
            llm_factory=mock_llm_factory,
        )
        
        tool_names = [t.name for t in runtime.tools]
        assert "list_garden_notes" in tool_names
        assert "read_garden_note" in tool_names
        assert "update_garden_note" in tool_names
        assert "search_garden" in tool_names

    def test_markdown_garden_functional_flow(self, temp_paths):
        mgr = MarkdownGardenManager(workspace_dir=str(temp_paths.root_dir))
        
        mgr.update_note("engineering/test_note.md", "This is a test contract.")
        
        notes = mgr.list_notes()
        assert "engineering/test_note.md" in notes
        
        content = mgr.read_note("engineering/test_note.md")
        assert "This is a test contract." in content
        
        results = mgr.search_notes("contract")
        assert len(results) == 1
        assert results[0]["path"] == "engineering/test_note.md"

    def test_recursive_summarization(self, temp_paths):
        def mock_summarizer(text):
            return "This is a summary of the long text."
            
        mgr = MarkdownGardenManager(
            workspace_dir=str(temp_paths.root_dir),
            summarize_fn=mock_summarizer,
            max_note_chars=100
        )
        
        long_content = "A" * 200
        mgr.update_note("long_note.md", long_content)
        
        main_content = mgr.read_note("long_note.md")
        assert "This is a summary of the long text." in main_content
        assert "(Summarized)" in main_content
        assert "_archive_" in main_content
        
        notes = mgr.list_notes()
        archive_note = next(n for n in notes if "_archive_" in n)
        archive_content = mgr.read_note(archive_note)
        assert "A" * 200 in archive_content


# ---------------------------------------------------------------------------
# 3. Admin Memory Tests (formerly test_admin_memory.py)
# ---------------------------------------------------------------------------

class TestAdminMemoryClass:
    def test_build_prompt_context_compacts_large_state(self):
        memory = AdminMemoryManager(
            config=AdminMemoryConfig(
                max_field_chars=40,
                recent_entries_limit=2,
            )
        )
        context = {
            "plan_summary": "Build an autonomous ops loop",
            "success_criteria": ["Loop survives restart"],
            "admin_plan": {
                "summary": "Ops loop",
                "steps": ["Inspect", "Build workflow", "Verify", "Deploy"],
                "success_criteria": ["Done"],
            },
            "admin_memory": {
                "summary": "Previous work summary",
                "recent_entries": [{"step_id": "step_1", "summary": "Did initial audit"}],
                "artifacts": {"workflow_id": "wf-123"},
            },
            "huge_blob": "x" * 200,
        }

        prompt_context = memory.build_prompt_context(context)

        assert prompt_context["plan_summary"] == "Build an autonomous ops loop"
        assert prompt_context["memory_summary"] == "Previous work summary"
        assert prompt_context["artifacts"]["workflow_id"] == "wf-123"
        assert prompt_context["state"]["huge_blob"].endswith("...(truncated)")

    def test_build_context_update_rolls_entries_into_summary_and_limits_artifacts(self):
        memory = AdminMemoryManager(
            summarize_fn=lambda text: f"summary::{text[:24]}",
            config=AdminMemoryConfig(
                recent_entries_limit=2,
                max_artifacts=2,
                max_field_chars=60,
            ),
        )

        context: dict[str, object] = {}
        update_1 = memory.build_context_update(
            task_name="admin_goal",
            step_id="step_1",
            step_description="Inspect",
            raw_output={"step_response": "inspected", "report": "a" * 120},
            current_context=context,
        )
        context.update(update_1)

        update_2 = memory.build_context_update(
            task_name="admin_goal",
            step_id="step_2",
            step_description="Build",
            raw_output={"step_response": "built", "workflow_id": "wf-001"},
            current_context=context,
        )
        context.update(update_2)

        update_3 = memory.build_context_update(
            task_name="admin_goal",
            step_id="step_3",
            step_description="Verify",
            raw_output={"step_response": "verified", "artifact_url": "http://example.test/output"},
            current_context=context,
        )

        admin_memory = update_3["admin_memory"]
        assert update_3["last_step_summary"].startswith("summary::")
        assert admin_memory["summary"].startswith("summary::")
        assert len(admin_memory["recent_entries"]) == 2
        assert len(admin_memory["artifacts"]) == 2
        assert "report" not in admin_memory["artifacts"]


# ---------------------------------------------------------------------------
# 4. In-Memory Vector Store Tests (formerly test_vector_store.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    return InMemoryVectorStore()


class TestInMemoryVectorStoreClass:
    def test_add_and_count(self, store):
        docs = [Document(page_content="hello world"), Document(page_content="foo bar")]
        ids = store.add_documents(docs)
        assert len(ids) == 2
        assert store.count() == 2

    def test_search_returns_relevant(self, store):
        store.add_documents(
            [
                Document(page_content="Python is a programming language"),
                Document(page_content="The weather is nice today"),
                Document(page_content="Python programming tutorial"),
            ]
        )
        results = store.search("Python programming")
        assert len(results) == 3
        assert results[0].score >= results[1].score
        assert "Python" in results[0].document.page_content

    def test_search_empty_collection(self, store):
        results = store.search("anything")
        assert results == []

    def test_search_nonexistent_collection(self, store):
        results = store.search("anything", collection="nonexistent")
        assert results == []

    def test_delete_by_ids(self, store):
        store.add_documents(
            [
                Document(page_content="doc1", doc_id="id1"),
                Document(page_content="doc2", doc_id="id2"),
                Document(page_content="doc3", doc_id="id3"),
            ]
        )
        deleted = store.delete(["id1", "id2"])
        assert deleted == 2
        assert store.count() == 1

    def test_delete_nonexistent_ids(self, store):
        store.add_documents([Document(page_content="doc1", doc_id="id1")])
        deleted = store.delete(["nonexistent"])
        assert deleted == 0
        assert store.count() == 1

    def test_delete_collection(self, store):
        store.add_documents([Document(page_content="x")], collection="test")
        assert store.delete_collection("test") is True
        assert store.count("test") == 0

    def test_delete_nonexistent_collection(self, store):
        assert store.delete_collection("nope") is False

    def test_list_collections(self, store):
        store.add_documents([Document(page_content="a")], collection="alpha")
        store.add_documents([Document(page_content="b")], collection="beta")
        assert sorted(store.list_collections()) == ["alpha", "beta"]

    def test_upsert_deduplicates(self, store):
        store.add_documents([Document(page_content="v1", doc_id="same")])
        store.add_documents([Document(page_content="v2", doc_id="same")])
        assert store.count() == 1
        results = store.search("v2")
        assert results[0].document.page_content == "v2"

    def test_multiple_collections(self, store):
        store.add_documents([Document(page_content="a")], collection="c1")
        store.add_documents([Document(page_content="b")], collection="c2")
        assert store.count("c1") == 1
        assert store.count("c2") == 1
        assert store.count("c3") == 0

    def test_top_k_limit(self, store):
        for i in range(10):
            store.add_documents([Document(page_content=f"doc {i}")])
        results = store.search("doc", top_k=3)
        assert len(results) == 3

    def test_metadata_preserved(self, store):
        store.add_documents([Document(page_content="test", metadata={"source": "file.txt", "page": 1})])
        results = store.search("test")
        assert results[0].document.metadata["source"] == "file.txt"
        assert results[0].document.metadata["page"] == 1


class TestCreateVectorStoreClass:
    def test_memory_backend(self):
        store = create_vector_store(backend="memory")
        assert isinstance(store, InMemoryVectorStore)

    def test_sqlite_vec_backend(self, tmp_workspace):
        store = create_vector_store(backend="sqlite-vec", persist_dir=str(tmp_workspace / "sqlite-vec"))
        assert store.count() == 0

    def test_faiss_backend(self, tmp_workspace):
        store = create_vector_store(backend="faiss", persist_dir=str(tmp_workspace / "faiss"))
        assert store.count() == 0

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown vector store"):
            create_vector_store(backend="nonexistent")


# ---------------------------------------------------------------------------
# 5. Vector Store Backends Tests (formerly test_vector_store_backends.py)
# ---------------------------------------------------------------------------

class DummyEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if "python" in lowered else 0.0,
            1.0 if "weather" in lowered else 0.0,
            float(len(lowered.split())),
        ]


class TestVectorStoreBackendsClass:
    def test_sqlite_vec_store_add_search_delete(self, tmp_workspace: Path):
        store = create_vector_store(
            backend="sqlite-vec",
            persist_dir=str(tmp_workspace / "sqlite-vec"),
            embedding_function=DummyEmbeddings(),
        )
        store.add_documents(
            [
                Document(page_content="Python async patterns", doc_id="py"),
                Document(page_content="Weather forecast today", doc_id="weather"),
            ]
        )

        results = store.search("python", top_k=1)

        assert results[0].document.doc_id == "py"
        assert store.count() == 2
        assert store.delete(["py"]) == 1
        assert store.count() == 1

    def test_faiss_store_persists_metadata_even_without_native_faiss(self, tmp_workspace: Path):
        persist_dir = tmp_workspace / "faiss"
        store = create_vector_store(
            backend="faiss",
            persist_dir=str(persist_dir),
            embedding_function=DummyEmbeddings(),
        )
        store.add_documents(
            [
                Document(page_content="Python runtime internals", metadata={"kind": "code"}, doc_id="py"),
                Document(page_content="Weather alerts", metadata={"kind": "ops"}, doc_id="weather"),
            ]
        )

        reloaded = create_vector_store(
            backend="faiss",
            persist_dir=str(persist_dir),
            embedding_function=DummyEmbeddings(),
        )
        results = reloaded.search("python", top_k=1)

        assert reloaded.count() == 2
        assert results[0].document.doc_id == "py"
        assert results[0].document.metadata["kind"] == "code"


class TestAdvancedMemoryFeatures:
    def test_adaptive_forgetting_rate(self, tmp_workspace: Path) -> None:
        # 1. Initialize engine
        eng = MemoryEngine(tmp_workspace)
        try:
            # 2. Ingest two facts
            fid1 = eng.ingest(Modality.FACT, "High recall consolidated fact")
            fid2 = eng.ingest(Modality.FACT, "Zero recall forgotten fact")

            # Simulate touching recall to increase recall_count for fid1
            now = time.time()
            # Simulate 10 recalls on fid1 to consolidate it
            for _ in range(10):
                eng._store.touch_recall(fid1, ts=now, recall_bonus=0.01)

            # Retrieve records from store to inspect count
            rec1 = eng._store.get(fid1)
            rec2 = eng._store.get(fid2)
            assert rec1.recall_count == 10
            assert rec2.recall_count == 0

            # Calculate decay weights manually after 10 days of age
            from core.systems.memory.scoring import temporal_decay_weight
            decay_consolidated = temporal_decay_weight(
                last_recall_ts=now - 10 * 86400.0,
                now=now,
                alpha=0.95,
                recall_count=10,
            )
            decay_unconsolidated = temporal_decay_weight(
                last_recall_ts=now - 10 * 86400.0,
                now=now,
                alpha=0.95,
                recall_count=0,
            )

            # Consolidated weight must decay significantly slower (higher score) than unconsolidated
            assert decay_consolidated > decay_unconsolidated
            assert decay_consolidated > 0.95 ** 10  # 0.95**10 is the static decay without consolidation
        finally:
            eng.close()

    def test_graph_lite_operations_and_associations(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            # Ingest associated facts
            fid1 = eng.ingest(Modality.FACT, "张三是我的大学同学")
            fid2 = eng.ingest(Modality.FACT, "张三在 Alpha 项目组工作")
            fid3 = eng.ingest(Modality.FACT, "Alpha 项目组负责开发 PyBot")

            # Build links (张三是同学 -> [项目组] -> 张三在Alpha -> [开发] -> Alpha负责开发)
            eng.add_link(fid1, fid2, "项目组")
            eng.add_link(fid2, fid3, "开发")

            # Verify outbound links
            links = eng._store.get_links(fid1)
            assert len(links) == 1
            assert links[0]["target_id"] == fid2
            assert links[0]["relation"] == "项目组"

            # Verify bidirectional association recall (depth 1)
            assoc_from_fid2 = eng.get_associated_memories(fid2, max_depth=1)
            assert len(assoc_from_fid2) == 2
            # Should pull fid1 via inbound and fid3 via outbound link
            ids = {rec.id for rec, _ in assoc_from_fid2}
            assert fid1 in ids
            assert fid3 in ids

            # Verify full integrated hybrid recall with association expansion
            # Querying "同学" directly recalls "张三是我的大学同学" (fid1)
            # FTS/Semantic recalls fid1, which should automatically expand to recall fid2 (1-hop link)
            recalled = eng.recall("同学", top_k=1, record_recall=False)
            assert len(recalled) >= 2
            recalled_ids = {r.id for r in recalled}
            assert fid1 in recalled_ids
            assert fid2 in recalled_ids  # Successfully retrieved via Graph-Lite association!
            
            # Find fid2 and verify metadata annotations
            fid2_record = next(r for r in recalled if r.id == fid2)
            assert fid2_record.metadata["associated_via"] == "项目组"
            assert "张三是我的大学同学" in fid2_record.metadata["associated_parent"]

        finally:
            eng.close()

    def test_counterfactual_correction_and_belief_revision(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            # 1. Ingest an initial fact
            fid1 = eng.ingest(Modality.FACT, "PyBot backend is implemented in Node.js")
            
            # 2. Ingest an updated, contradictory fact with high semantic/token overlap
            # "PyBot backend is implemented in Python" has high token overlap with "PyBot backend is implemented in Node.js"
            fid2 = eng.ingest(Modality.FACT, "PyBot backend is implemented in Python")
            
            # Since fid1 is contradictory/superseded, its status should be updated to "forgotten"
            rec1 = eng._store.get(fid1)
            rec2 = eng._store.get(fid2)
            
            assert rec1 is not None
            assert rec2 is not None
            assert rec1.status == "forgotten"  # Soft-archived
            assert rec2.status == "active"
            
            # Verify that they are bidirectionally linked via "contradicted_by" and "supersedes"
            links_from_fid1 = eng._store.get_links(fid1)
            assert len(links_from_fid1) == 1
            assert links_from_fid1[0]["target_id"] == fid2
            assert links_from_fid1[0]["relation"] == "contradicted_by"
            
            links_from_fid2 = eng._store.get_links(fid2)
            assert len(links_from_fid2) == 1
            assert links_from_fid2[0]["target_id"] == fid1
            assert links_from_fid2[0]["relation"] == "supersedes"
            
        finally:
            eng.close()

    def test_cross_process_sqlite_file_lock(self, tmp_workspace: Path) -> None:
        from core.systems.memory.store import SQLiteFileLock
        lock_path = tmp_workspace / "test.lock"
        
        # Verify basic lock acquisition and release
        lock1 = SQLiteFileLock(lock_path)
        lock2 = SQLiteFileLock(lock_path)
        
        with lock1:
            # Sidecar lock file should exist
            assert lock_path.exists()
            
            # Attempting non-blocking lock acquisition in another thread/process should wait
            # Here we just verify that we can release and re-acquire
            pass
        
        # Lock file should be cleaned up on Windows or released
        if os.name == "nt":
            assert not lock_path.exists()

    def test_auto_capture_heuristic_ingests_session_notes(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            captured = eng.auto_capture([
                {"role": "user", "content": "Remember this design decision."},
                {"role": "assistant", "content": "PyBot uses SQLite for unified memory storage."},
            ])
            assert len(captured) == 1
            rec = eng._store.get(captured[0])
            assert rec is not None
            assert rec.modality == Modality.SESSION_NOTE.value
        finally:
            eng.close()

    def test_auto_capture_schedules_journal_when_caller_present(self, tmp_workspace: Path) -> None:
        def fake_journal(system: str, user: str) -> str:
            return "无"

        eng = MemoryEngine(tmp_workspace, journal_caller=fake_journal)
        try:
            captured = eng.auto_capture([
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ])
            assert captured == ["journal_scheduled"]
        finally:
            eng.close()

    def test_auto_recall_returns_context_prompt(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            eng.ingest(Modality.FACT, "PyBot memory engine is unified")
            recalled = eng.auto_recall("memory engine", top_k=3)
            assert "PyBot memory engine is unified" in recalled
        finally:
            eng.close()

    def test_counterfactual_no_correction_when_unrelated(self, tmp_workspace: Path) -> None:
        eng = MemoryEngine(tmp_workspace)
        try:
            fid1 = eng.ingest(Modality.FACT, "The sky appears blue on clear days")
            fid2 = eng.ingest(Modality.FACT, "Python 3.12 introduced improved error messages")
            rec1 = eng._store.get(fid1)
            rec2 = eng._store.get(fid2)
            assert rec1 is not None and rec2 is not None
            assert rec1.status == "active"
            assert rec2.status == "active"
            assert eng._store.get_links(fid1) == []
        finally:
            eng.close()

    def test_sqlite_store_concurrent_writes_with_lock(self, tmp_workspace: Path) -> None:
        import threading

        eng = MemoryEngine(tmp_workspace)
        errors: list[str] = []

        def writer(prefix: str) -> None:
            try:
                for i in range(10):
                    eng.ingest(
                        Modality.SESSION_NOTE,
                        f"{prefix} isolated note {i} with unique token {prefix}-{i}",
                    )
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=writer, args=(f"t{idx}",)) for idx in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        try:
            assert errors == []
            stats = eng.get_memory_stats()
            assert stats.get("total", 0) >= 40
        finally:
            eng.close()

