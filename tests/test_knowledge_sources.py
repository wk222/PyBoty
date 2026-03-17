"""Tests for core.knowledge_sources."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from core.knowledge_sources import (
    DirectorySource,
    FileSource,
    GitRepoSource,
    KnowledgeManager,
    TextSource,
    URLSource,
)
from core.vector_store import InMemoryVectorStore


class TestFileSource:
    def test_load_text_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello world, this is a test document.")
            f.flush()
            docs = FileSource(f.name).load()
        os.unlink(f.name)
        assert len(docs) >= 1
        assert "Hello world" in docs[0].page_content
        assert docs[0].metadata["format"] == "text"

    def test_load_nonexistent(self):
        docs = FileSource("/nonexistent/file.txt").load()
        assert docs == []

    def test_load_with_metadata(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\nSome content")
            f.flush()
            docs = FileSource(f.name, metadata={"project": "test"}).load()
        os.unlink(f.name)
        assert docs[0].metadata["project"] == "test"


class TestDirectorySource:
    def test_load_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("File A content", encoding="utf-8")
            (Path(tmpdir) / "b.md").write_text("File B content", encoding="utf-8")
            (Path(tmpdir) / "c.exe").write_text("Binary", encoding="utf-8")
            docs = DirectorySource(tmpdir).load()
            sources = {d.metadata.get("filename") for d in docs}
            assert "a.txt" in sources
            assert "b.md" in sources
            assert "c.exe" not in sources

    def test_nonexistent_directory(self):
        docs = DirectorySource("/nonexistent/dir").load()
        assert docs == []

    def test_custom_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("text", encoding="utf-8")
            (Path(tmpdir) / "b.rs").write_text("fn main() {}", encoding="utf-8")
            docs = DirectorySource(tmpdir, extensions=[".rs"]).load()
            assert all(d.metadata["filename"].endswith(".rs") for d in docs)


class TestTextSource:
    def test_load_text(self):
        docs = TextSource("Some important knowledge to remember").load()
        assert len(docs) >= 1
        assert "important knowledge" in docs[0].page_content

    def test_empty_text(self):
        assert TextSource("").load() == []
        assert TextSource("   ").load() == []

    def test_source_name(self):
        docs = TextSource("content", source_name="user_notes").load()
        assert docs[0].metadata["source"] == "user_notes"


class TestURLSource:
    def test_source_type(self):
        s = URLSource("https://example.com")
        assert s.source_type == "url"

    def test_bad_url(self):
        docs = URLSource("http://this-domain-does-not-exist-12345.com", timeout=2).load()
        assert docs == []


class TestGitRepoSource:
    def test_load_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("print('hello')", encoding="utf-8")
            sub = Path(tmpdir) / "src"
            sub.mkdir()
            (sub / "utils.py").write_text("def helper(): pass", encoding="utf-8")
            (Path(tmpdir) / ".git").mkdir()
            (Path(tmpdir) / ".git" / "config").write_text("git config", encoding="utf-8")

            docs = GitRepoSource(tmpdir).load()
            filenames = {d.metadata.get("filename") for d in docs}
            assert "main.py" in filenames
            assert "utils.py" in filenames
            assert "config" not in filenames  # .git excluded

    def test_nonexistent(self):
        assert GitRepoSource("/nonexistent").load() == []

    def test_exclude_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nm = Path(tmpdir) / "node_modules"
            nm.mkdir()
            (nm / "pkg.js").write_text("module.exports = {}", encoding="utf-8")
            (Path(tmpdir) / "app.js").write_text("console.log('hi')", encoding="utf-8")

            docs = GitRepoSource(tmpdir, extensions=[".js"]).load()
            filenames = {d.metadata.get("filename") for d in docs}
            assert "app.js" in filenames
            assert "pkg.js" not in filenames


class TestKnowledgeManager:
    def setup_method(self):
        self.store = InMemoryVectorStore()
        self.mgr = KnowledgeManager(self.store)

    def test_add_source(self):
        count = self.mgr.add_source(TextSource("Knowledge about Python"))
        assert count >= 1
        assert self.store.count("default") >= 1

    def test_add_multiple_sources(self):
        self.mgr.add_source(TextSource("First piece"), collection="docs")
        self.mgr.add_source(TextSource("Second piece"), collection="docs")
        sources = self.mgr.list_sources()
        assert len(sources) == 2

    def test_refresh(self):
        self.mgr.add_source(TextSource("Version 1"), collection="wiki")
        count1 = self.store.count("wiki")
        total = self.mgr.refresh("wiki")
        assert total == count1

    def test_stats(self):
        self.mgr.add_source(TextSource("A"), collection="c1")
        self.mgr.add_source(TextSource("B"), collection="c1")
        self.mgr.add_source(TextSource("C"), collection="c2")
        stats = self.mgr.stats()
        assert stats["total_sources"] == 3
        assert "c1" in stats["collections"]
        assert stats["collections"]["c1"]["source_count"] == 2

    def test_list_sources(self):
        self.mgr.add_source(TextSource("X"), collection="a")
        sources = self.mgr.list_sources()
        assert len(sources) == 1
        assert sources[0]["source_type"] == "text"
        assert sources[0]["collection"] == "a"

    def test_add_file_source(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("File content for testing knowledge manager")
            f.flush()
            count = self.mgr.add_source(FileSource(f.name))
        os.unlink(f.name)
        assert count >= 1
