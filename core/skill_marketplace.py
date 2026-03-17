"""
技能市场系统 — 支持技能的打包、分发、安装和发现

灵感来源：OpenClaw 的 Skill 生态系统
- 技能以目录形式存在，包含 SKILL.md + scripts/ + references/
- 支持打包为 .skill 文件（ZIP 格式）进行分发
- 支持从多个来源发现技能（本地、工作区、远程仓库）
- 支持技能验证、安装、卸载
"""

import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

SKILL_MANIFEST_FILE = "SKILL.md"
SKILL_META_FILE = "skill.json"
SKILL_CATALOG_FILE = "skill_catalog.json"


@dataclass
class SkillPackage:
    name: str
    version: str
    description: str
    author: str
    tags: list[str]
    requires: dict[str, Any]
    created_at: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tags": self.tags,
            "requires": self.requires,
            "created_at": self.created_at,
            "source": self.source,
        }


class SkillMarketplace:
    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = workspace_dir
        self.skills_dir = os.path.join(workspace_dir, "skills")
        self.catalog_path = os.path.join(workspace_dir, "data", SKILL_CATALOG_FILE)
        self.packages_dir = os.path.join(workspace_dir, "skill_packages")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.packages_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        self.catalog: dict[str, SkillPackage] = {}
        self._load_catalog()

    def _load_catalog(self):
        if os.path.exists(self.catalog_path):
            try:
                with open(self.catalog_path, encoding="utf-8") as f:
                    data = json.load(f)
                for name, info in data.get("skills", {}).items():
                    self.catalog[name] = SkillPackage(**info)
            except Exception as e:
                print(f"[SkillMarketplace] 加载技能目录失败: {e}")

    def _save_catalog(self):
        data = {
            "version": "1.0",
            "updated_at": time.time(),
            "skills": {name: pkg.to_dict() for name, pkg in self.catalog.items()},
        }
        with open(self.catalog_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _safe_skill_dir(self, skill_name: str) -> str | None:
        if not re.match(r"^[a-zA-Z0-9_\-]+$", skill_name):
            return None
        skill_dir = os.path.realpath(os.path.join(self.skills_dir, skill_name))
        skills_real = os.path.realpath(self.skills_dir)
        if os.path.commonpath([skill_dir, skills_real]) != skills_real:
            return None
        return skill_dir

    def validate_skill(self, skill_dir: str) -> dict[str, Any]:
        issues = []
        skill_md = os.path.join(skill_dir, SKILL_MANIFEST_FILE)
        if not os.path.exists(skill_md):
            issues.append({"severity": "critical", "message": f"缺少 {SKILL_MANIFEST_FILE}"})
            return {"valid": False, "issues": issues}

        with open(skill_md, encoding="utf-8") as f:
            content = f.read()

        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not frontmatter_match:
            issues.append({"severity": "critical", "message": "SKILL.md 缺少 YAML frontmatter (--- ... ---)"})
        else:
            fm = frontmatter_match.group(1)
            if "name:" not in fm:
                issues.append({"severity": "critical", "message": "frontmatter 缺少 name 字段"})
            if "description:" not in fm:
                issues.append({"severity": "warning", "message": "frontmatter 缺少 description 字段"})

        if len(content) < 100:
            issues.append({"severity": "warning", "message": "SKILL.md 内容过少，建议添加详细的使用说明"})

        scripts_dir = os.path.join(skill_dir, "scripts")
        if os.path.exists(scripts_dir):
            for f_name in os.listdir(scripts_dir):
                f_path = os.path.join(scripts_dir, f_name)
                if os.path.isfile(f_path) and f_name.endswith(".py"):
                    try:
                        with open(f_path, encoding="utf-8") as f:
                            compile(f.read(), f_path, "exec")
                    except SyntaxError as e:
                        issues.append({"severity": "critical", "message": f"scripts/{f_name} 语法错误: {e.msg}"})

        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        return {
            "valid": critical_count == 0,
            "issues": issues,
            "critical": critical_count,
            "warnings": len(issues) - critical_count,
        }

    def package_skill(self, skill_name: str) -> dict[str, Any]:
        skill_dir = self._safe_skill_dir(skill_name)
        if not skill_dir:
            return {"success": False, "error": f"无效的技能名称: '{skill_name}'"}
        if not os.path.exists(skill_dir):
            return {"success": False, "error": f"技能 '{skill_name}' 不存在"}

        validation = self.validate_skill(skill_dir)
        if not validation["valid"]:
            return {"success": False, "error": "技能验证失败", "issues": validation["issues"]}

        meta = self._extract_meta(skill_dir)
        version = meta.get("version", "1.0.0")
        package_name = f"{skill_name}-{version}.skill"
        package_path = os.path.join(self.packages_dir, package_name)

        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(skill_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arc_name = os.path.join(skill_name, os.path.relpath(file_path, skill_dir))
                    zf.write(file_path, arc_name)

            meta_content = json.dumps(meta, ensure_ascii=False, indent=2)
            zf.writestr(os.path.join(skill_name, SKILL_META_FILE), meta_content)

        pkg = SkillPackage(
            name=skill_name,
            version=version,
            description=meta.get("description", ""),
            author=meta.get("author", "agent"),
            tags=meta.get("tags", []),
            requires=meta.get("requires", {}),
            created_at=time.time(),
            source="local",
        )
        self.catalog[skill_name] = pkg
        self._save_catalog()

        return {
            "success": True,
            "package": package_name,
            "path": package_path,
            "size": os.path.getsize(package_path),
            "meta": meta,
        }

    def install_skill(self, package_path: str) -> dict[str, Any]:
        if not os.path.exists(package_path):
            return {"success": False, "error": f"包文件不存在: {package_path}"}

        if not package_path.endswith(".skill"):
            return {"success": False, "error": "文件必须是 .skill 格式"}

        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                names = zf.namelist()
                if not names:
                    return {"success": False, "error": "空的技能包"}

                skill_name = names[0].split("/")[0]

                for name in names:
                    norm = os.path.normpath(name)
                    if norm.startswith("..") or os.path.isabs(norm):
                        return {"success": False, "error": f"不安全的路径: {name}"}

                target_dir = os.path.join(self.skills_dir, skill_name)
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)

                zf.extractall(self.skills_dir)

            validation = self.validate_skill(target_dir)
            if not validation["valid"]:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": "安装后验证失败", "issues": validation["issues"]}

            meta = self._extract_meta(target_dir)
            pkg = SkillPackage(
                name=skill_name,
                version=meta.get("version", "1.0.0"),
                description=meta.get("description", ""),
                author=meta.get("author", "unknown"),
                tags=meta.get("tags", []),
                requires=meta.get("requires", {}),
                created_at=time.time(),
                source="package",
            )
            self.catalog[skill_name] = pkg
            self._save_catalog()

            return {
                "success": True,
                "skill_name": skill_name,
                "version": pkg.version,
                "path": target_dir,
            }
        except zipfile.BadZipFile:
            return {"success": False, "error": "无效的 ZIP 文件"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def uninstall_skill(self, skill_name: str) -> dict[str, Any]:
        skill_dir = self._safe_skill_dir(skill_name)
        if not skill_dir:
            return {"success": False, "error": f"无效的技能名称: '{skill_name}'"}
        if not os.path.exists(skill_dir):
            return {"success": False, "error": f"技能 '{skill_name}' 未安装"}

        shutil.rmtree(skill_dir)
        self.catalog.pop(skill_name, None)
        self._save_catalog()

        return {"success": True, "uninstalled": skill_name}

    def list_available(self) -> list[dict[str, Any]]:
        result = []
        for name, pkg in self.catalog.items():
            info = pkg.to_dict()
            info["installed"] = os.path.exists(os.path.join(self.skills_dir, name))
            result.append(info)
        return result

    def discover_skills(self) -> list[dict[str, Any]]:
        discovered = []
        if os.path.exists(self.skills_dir):
            for entry in os.scandir(self.skills_dir):
                if entry.is_dir():
                    skill_md = os.path.join(entry.path, SKILL_MANIFEST_FILE)
                    if os.path.exists(skill_md):
                        meta = self._extract_meta(entry.path)
                        discovered.append(
                            {
                                "name": entry.name,
                                "source": "workspace",
                                "meta": meta,
                                "path": entry.path,
                            }
                        )

        for pkg_file in os.listdir(self.packages_dir):
            if pkg_file.endswith(".skill"):
                discovered.append(
                    {
                        "name": pkg_file.replace(".skill", ""),
                        "source": "package",
                        "path": os.path.join(self.packages_dir, pkg_file),
                    }
                )
        return discovered

    def _extract_meta(self, skill_dir: str) -> dict[str, Any]:
        meta_path = os.path.join(skill_dir, SKILL_META_FILE)
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)

        skill_md = os.path.join(skill_dir, SKILL_MANIFEST_FILE)
        meta = {"name": os.path.basename(skill_dir), "version": "1.0.0"}
        if os.path.exists(skill_md):
            with open(skill_md, encoding="utf-8") as f:
                content = f.read()
            fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key in ("name", "description", "version", "author"):
                            meta[key] = val
                        elif key == "tags":
                            meta["tags"] = [t.strip() for t in val.split(",") if t.strip()]
        return meta


class PackageSkillInput(BaseModel):
    skill_name: str = Field(description="要打包的技能名称")


class PackageSkillTool(BaseTool):
    name: str = "package_skill"
    description: str = """将技能打包为 .skill 文件以便分发和共享。
打包前会自动验证技能的完整性（SKILL.md、脚本语法等）。
打包后的文件保存在 workspace/skill_packages/ 目录下。"""
    args_schema: type[BaseModel] = PackageSkillInput
    marketplace: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, skill_name: str) -> str:
        return json.dumps(self.marketplace.package_skill(skill_name), ensure_ascii=False, indent=2)


class InstallSkillInput(BaseModel):
    package_path: str = Field(description="技能包文件路径 (.skill)")


class InstallSkillTool(BaseTool):
    name: str = "install_skill"
    description: str = """从 .skill 包文件安装技能到工作区。
安装后技能会自动被发现并可用。"""
    args_schema: type[BaseModel] = InstallSkillInput
    marketplace: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, package_path: str) -> str:
        return json.dumps(self.marketplace.install_skill(package_path), ensure_ascii=False, indent=2)


class UninstallSkillInput(BaseModel):
    skill_name: str = Field(description="要卸载的技能名称")


class UninstallSkillTool(BaseTool):
    name: str = "uninstall_skill"
    description: str = "卸载已安装的技能，删除其所有文件。"
    args_schema: type[BaseModel] = UninstallSkillInput
    marketplace: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, skill_name: str) -> str:
        return json.dumps(self.marketplace.uninstall_skill(skill_name), ensure_ascii=False, indent=2)


class SearchSkillsInput(BaseModel):
    query: str = Field(description="搜索关键词（按名称、描述、标签匹配）", default="")


class SearchSkillsTool(BaseTool):
    name: str = "search_skills"
    description: str = """搜索技能市场中可用的技能。
可按名称、描述、标签搜索。不填关键词则列出所有可用技能。"""
    args_schema: type[BaseModel] = SearchSkillsInput
    marketplace: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, query: str = "") -> str:
        all_skills = self.marketplace.list_available()
        discovered = self.marketplace.discover_skills()

        seen = {s["name"] for s in all_skills}
        for d in discovered:
            if d["name"] not in seen:
                all_skills.append(
                    {
                        "name": d["name"],
                        "source": d["source"],
                        "installed": d["source"] == "workspace",
                        **d.get("meta", {}),
                    }
                )
                seen.add(d["name"])

        if query:
            q = query.lower()
            all_skills = [
                s
                for s in all_skills
                if q in s.get("name", "").lower()
                or q in s.get("description", "").lower()
                or any(q in t.lower() for t in s.get("tags", []))
            ]

        return json.dumps(
            {
                "success": True,
                "count": len(all_skills),
                "skills": all_skills,
            },
            ensure_ascii=False,
            indent=2,
        )


class CreateSkillInput(BaseModel):
    skill_name: str = Field(description="技能名称 (英文+连字符)")
    description: str = Field(description="技能功能描述")
    instructions: str = Field(description="技能使用说明（Markdown 格式，作为 SKILL.md 的主体内容）")
    tags: str = Field(default="", description="逗号分隔的标签")
    author: str = Field(default="agent", description="作者名称")


class CreateSkillTool(BaseTool):
    name: str = "create_skill"
    description: str = """创建新技能并注册到技能市场。

技能是一组可复用的指令和工具集合，包含:
- SKILL.md: 技能说明书（frontmatter + 使用说明）
- scripts/: 可执行脚本
- references/: 参考文档
- assets/: 模板和资源

创建后可通过 package_skill 打包分发。"""
    args_schema: type[BaseModel] = CreateSkillInput
    marketplace: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, skill_name: str, description: str, instructions: str, tags: str = "", author: str = "agent") -> str:
        if not skill_name.replace("-", "").replace("_", "").isalnum():
            return json.dumps(
                {"success": False, "error": "技能名称只能包含字母、数字、连字符和下划线"},
                ensure_ascii=False,
            )

        skill_dir = os.path.join(self.marketplace.skills_dir, skill_name)
        if os.path.exists(skill_dir):
            return json.dumps({"success": False, "error": f"技能 '{skill_name}' 已存在"}, ensure_ascii=False)

        os.makedirs(skill_dir, exist_ok=True)
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        tag_str = ", ".join(tag_list) if tag_list else ""

        skill_md = f"""---
name: {skill_name}
description: {description}
version: 1.0.0
author: {author}
tags: {tag_str}
---

# {skill_name}

{description}

## 使用说明

{instructions}
"""
        with open(os.path.join(skill_dir, SKILL_MANIFEST_FILE), "w", encoding="utf-8") as f:
            f.write(skill_md)

        meta = {
            "name": skill_name,
            "description": description,
            "version": "1.0.0",
            "author": author,
            "tags": tag_list,
            "requires": {},
            "created_at": time.time(),
        }
        with open(os.path.join(skill_dir, SKILL_META_FILE), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        pkg = SkillPackage(
            name=skill_name,
            version="1.0.0",
            description=description,
            author=author,
            tags=tag_list,
            requires={},
            created_at=time.time(),
            source="created",
        )
        self.marketplace.catalog[skill_name] = pkg
        self.marketplace._save_catalog()

        return json.dumps(
            {
                "success": True,
                "skill_name": skill_name,
                "path": skill_dir,
                "message": (
                    f"技能 '{skill_name}' 创建成功。可以用 update_app_file 添加脚本，或用 package_skill 打包分发。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )


def get_marketplace_tools(marketplace: SkillMarketplace) -> list[BaseTool]:
    return [
        CreateSkillTool(marketplace=marketplace),
        PackageSkillTool(marketplace=marketplace),
        InstallSkillTool(marketplace=marketplace),
        UninstallSkillTool(marketplace=marketplace),
        SearchSkillsTool(marketplace=marketplace),
    ]
