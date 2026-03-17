"""
示例2：跨会话复用工具

展示工具的持久化和跨会话复用能力
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import create_tool_creator_agent


def session_1():
    """第一个会话：创建工具"""
    print("=" * 60)
    print("  第一个会话：创建工具")
    print("=" * 60 + "\n")

    agent = create_tool_creator_agent(
        model="gpt-4",
        thread_id="reuse-demo",  # 注意这个 thread_id
    )

    print("👤 用户: 创建一个计算员工工资的工具")
    print("       输入基本工资、绩效系数(0-2)、加班小时数")
    print("       公式：基本工资 * 绩效系数 + 加班小时数 * 50")
    print()

    response = agent.chat(
        "创建一个计算员工工资的工具，"
        "输入base_salary(基本工资)、performance(绩效系数0-2)、overtime_hours(加班小时)，"
        "返回总工资，公式是 base_salary * performance + overtime_hours * 50"
    )

    print(f"🤖 助手:\n{response}\n")

    print("✅ 工具已创建并持久化到 checkpoint")
    print("💾 会话结束...\n")


def session_2():
    """第二个会话：使用之前创建的工具"""
    print("=" * 60)
    print("  第二个会话：使用之前的工具（新实例）")
    print("=" * 60 + "\n")

    # 创建新的智能体实例，但使用相同的 thread_id
    agent = create_tool_creator_agent(
        model="gpt-4",
        thread_id="reuse-demo",  # 相同的 thread_id
    )

    print("📥 工具从 checkpoint 自动恢复...\n")

    print("👤 用户: 计算员工工资")
    print("       基本工资5000，绩效系数1.5，加班10小时")
    print()

    response = agent.chat("计算一个员工的工资：基本工资5000，绩效系数1.5，加班10小时")

    print(f"🤖 助手: {response}\n")

    print("✅ 工具在新会话中自动恢复并使用！")
    print("🎯 核心价值：无需重新创建，直接复用！")


def main():
    print("\n" + "🚀" * 30)
    print("    示例2：跨会话复用工具")
    print("🚀" * 30 + "\n")

    # 第一个会话
    session_1()

    # 模拟会话结束，等待用户确认
    input("按回车键继续到第二个会话...")
    print()

    # 第二个会话
    session_2()

    print("\n" + "🎉" * 30)
    print("    示例完成！")
    print("🎉" * 30 + "\n")


if __name__ == "__main__":
    main()
