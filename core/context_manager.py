"""
上下文窗口管理器 — 智能对话历史裁剪

灵感来源：
- LangChain: ConversationSummaryBufferMemory 混合策略
- DeepAgents: SummarizationMiddleware 自动压缩

核心能力：
1. Token 近似计算（不依赖外部 tokenizer）
2. 滑动窗口 — 保留最近 k 轮对话
3. 摘要压缩 — 超出 token 限制时自动摘要旧消息
4. 重要信息保护 — 系统提示和关键工具结果不被裁剪
5. 工具输出压缩 — 长工具输出自动截断并保留关键信息
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ContextConfig:
    max_tokens: int = 12000
    max_turns: int = 30
    summary_threshold: float = 0.75
    tool_output_max_chars: int = 2000
    preserve_system: bool = True
    preserve_recent_turns: int = 6
    summarize_callback: Callable | None = None
    offload_dir: str | None = None  # 类似 DeepAgents，将裁剪掉的历史落盘
    thread_id: str = "default"


def count_tokens_approx(text: str) -> int:
    if not text:
        return 0
    cn_chars = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    remaining = len(text) - cn_chars
    return cn_chars * 2 + remaining // 4 + 1


def count_message_tokens(message: dict[str, Any]) -> int:
    tokens = 4
    content = message.get("content", "")
    if isinstance(content, str):
        tokens += count_tokens_approx(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                tokens += count_tokens_approx(str(part.get("text", "")))
            else:
                tokens += count_tokens_approx(str(part))
    tokens += count_tokens_approx(message.get("role", ""))
    if "tool_calls" in message:
        for tc in message.get("tool_calls", []):
            tokens += count_tokens_approx(str(tc))
    return tokens


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(count_message_tokens(m) for m in messages) + 3


class ContextWindowManager:
    def __init__(self, config: ContextConfig | None = None):
        self.config = config or ContextConfig()
        self._summary_cache: dict[str, str] = {}

    def trim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return messages

        total_tokens = count_messages_tokens(messages)
        if total_tokens <= self.config.max_tokens and len(messages) <= self.config.max_turns * 2:
            return self._compress_tool_outputs(messages)

        system_msgs = []
        conversation_msgs = []

        for msg in messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                conversation_msgs.append(msg)

        preserve_count = self.config.preserve_recent_turns * 2
        if len(conversation_msgs) <= preserve_count:
            result = system_msgs + conversation_msgs
            return self._compress_tool_outputs(result)

        recent = conversation_msgs[-preserve_count:]
        older = conversation_msgs[:-preserve_count]

        recent_tokens = count_messages_tokens(system_msgs + recent)
        remaining_budget = self.config.max_tokens - recent_tokens

        if remaining_budget <= 200 or not older:
            if older:
                self._offload_messages(older)
            if self.config.summarize_callback and older:
                summary = self._summarize_messages(older)
                if summary:
                    summary_msg = {"role": "system", "content": f"[之前对话摘要]\n{summary}"}
                    return self._compress_tool_outputs(system_msgs + [summary_msg] + recent)
            return self._compress_tool_outputs(system_msgs + recent)

        kept_older = []
        used_tokens = 0
        for msg in reversed(older):
            msg_tokens = count_message_tokens(msg)
            if used_tokens + msg_tokens > remaining_budget:
                break
            kept_older.insert(0, msg)
            used_tokens += msg_tokens

        trimmed_older = older[: len(older) - len(kept_older)]
        if trimmed_older:
            self._offload_messages(trimmed_older)

        if self.config.summarize_callback and trimmed_older:
            summary = self._summarize_messages(trimmed_older)
            if summary:
                summary_msg = {"role": "system", "content": f"[之前对话摘要]\n{summary}"}
                return self._compress_tool_outputs(system_msgs + [summary_msg] + kept_older + recent)

        return self._compress_tool_outputs(system_msgs + kept_older + recent)

    def _offload_messages(self, messages: list[dict[str, Any]]):
        """将裁剪掉的消息追加到文件，防止上下文永久丢失（参考 DeepAgents）"""
        if not self.config.offload_dir or not messages:
            return
        try:
            import os
            from datetime import datetime

            os.makedirs(self.config.offload_dir, exist_ok=True)
            filepath = os.path.join(self.config.offload_dir, f"{self.config.thread_id}.md")
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n\n## Offloaded at {datetime.now().isoformat()}\n\n")
                for msg in messages:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = json.dumps(content, ensure_ascii=False)
                    f.write(f"### {role.upper()}\n{content}\n\n")
        except Exception as e:
            print(f"[ContextManager] Offload failed: {e}")

    def _compress_tool_outputs(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        max_chars = self.config.tool_output_max_chars
        result = []
        for msg in messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                content = msg["content"]
                if len(content) > max_chars:
                    compressed = self._smart_truncate(content, max_chars)
                    result.append({**msg, "content": compressed})
                    continue
            result.append(msg)
        return result

    def _smart_truncate(self, content: str, max_chars: int) -> str:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                summary_parts = []
                if "success" in data:
                    summary_parts.append(f"success: {data['success']}")
                if "error" in data:
                    summary_parts.append(f"error: {str(data['error'])[:300]}")
                if "stdout" in data:
                    summary_parts.append(f"stdout: {str(data['stdout'])[:500]}")
                if "result" in data:
                    summary_parts.append(f"result: {str(data['result'])[:500]}")
                for k, v in data.items():
                    if k not in ("success", "error", "stdout", "result", "stderr", "traceback"):
                        s = str(v)
                        if len(s) < 200:
                            summary_parts.append(f"{k}: {s}")
                result = "\n".join(summary_parts)
                if len(result) <= max_chars:
                    return result
                return result[:max_chars] + "\n...[截断]"
            elif isinstance(data, list):
                return json.dumps(data[:10], ensure_ascii=False, indent=1) + f"\n...[共 {len(data)} 项，显示前 10]"
        except (json.JSONDecodeError, TypeError):
            pass

        head = content[: max_chars // 2]
        tail = content[-(max_chars // 4) :]
        return f"{head}\n\n...[中间内容已省略，共 {len(content)} 字符]...\n\n{tail}"

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str | None:
        if not self.config.summarize_callback:
            return self._simple_summary(messages)

        conversation_text = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                short = content[:500] if len(content) > 500 else content
                conversation_text.append(f"[{role}]: {short}")

        if not conversation_text:
            return None

        text = "\n".join(conversation_text[-20:])

        try:
            summary = self.config.summarize_callback(
                f"请用 3-5 句话总结以下对话的要点（保留关键事实、决策和待办事项）：\n\n{text}"
            )
            return summary
        except Exception:
            return self._simple_summary(messages)

    def _simple_summary(self, messages: list[dict[str, Any]]) -> str | None:
        topics = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and msg.get("role") == "user" and len(content) > 5:
                topics.append(content[:100])
        if not topics:
            return None
        return "用户之前讨论了: " + "; ".join(topics[-5:])

    def get_stats(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        total_tokens = count_messages_tokens(messages)
        return {
            "message_count": len(messages),
            "total_tokens_approx": total_tokens,
            "max_tokens": self.config.max_tokens,
            "usage_pct": round(total_tokens / self.config.max_tokens * 100, 1),
            "needs_trim": total_tokens > self.config.max_tokens,
        }
