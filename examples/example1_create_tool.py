"""
示例1：创建自定义工具

展示如何让智能体创建一个自定义工具
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import create_tool_creator_agent


def main():
    print("=" * 60)
    print("  示例1：创建自定义工具")
    print("=" * 60 + "\n")

    # 创建智能体
    agent = create_tool_creator_agent(model="gpt-4", thread_id="example-1")

    # 场景：创建一个计算折扣价的工具
    print("📝 场景：电商平台需要一个计算折扣价的工具\n")

    print("👤 用户: 创建一个计算折扣价的工具")
    print("       输入原价和折扣率，返回折后价")
    print()

    response = agent.chat(
        "创建一个计算折扣价的工具，"
        "输入原价(price)和折扣率(discount，0-1之间)，"
        "返回折后价，公式是 price * (1 - discount)"
    )

    print(f"🤖 助手:\n{response}\n")

    # 使用刚创建的工具
    print("👤 用户: 原价100元，打8折是多少？")
    response = agent.chat("原价100元，打8折是多少？")
    print(f"🤖 助手: {response}\n")

    print("✅ 工具创建成功并可以使用！")

    # 查看工具列表
    print("\n📋 当前已创建的工具:")
    tools = agent.list_tools()
    for name, desc in tools.items():
        print(f"  - {name}: {desc}")


if __name__ == "__main__":
    main()
