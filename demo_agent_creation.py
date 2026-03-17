"""
智能体创建能力演示

展示主智能体如何自主创建和管理子智能体
这是"元元编程"能力的演示
"""

from agent import PyBot


def demo_agent_creation():
    """演示智能体创建能力"""

    print("=" * 70)
    print("🤖 智能体创建能力演示 - 元元编程")
    print("=" * 70)
    print()
    print("本演示展示主智能体如何：")
    print("1. 自主创建专门化的子智能体")
    print("2. 将任务委派给子智能体执行")
    print("3. 管理智能体团队")
    print()

    # 创建主智能体
    agent = PyBot(model="gpt-4", thread_id="agent-creation-demo", enable_agent_creation=True)

    # ========== 场景1：创建数据分析师 ==========
    print("\n" + "=" * 50)
    print("📊 场景1：创建数据分析师智能体")
    print("=" * 50)

    response = agent.chat("""
创建一个数据分析师智能体，具有以下特点：
- 名称：data_analyst
- 角色：高级数据分析师
- 专长：数据清洗、统计分析、趋势识别、报告撰写
- 风格：专业但易懂
""")
    print(f"\n回复：{response}")

    # ========== 场景2：创建代码审查员 ==========
    print("\n" + "=" * 50)
    print("🔍 场景2：创建代码审查员智能体")
    print("=" * 50)

    response = agent.chat("""
创建一个代码审查员智能体：
- 名称：code_reviewer
- 角色：资深代码审查员
- 专长：代码质量、最佳实践、安全漏洞、性能优化
- 风格：严谨细致，给出具体建议
""")
    print(f"\n回复：{response}")

    # ========== 场景3：委派数据分析任务 ==========
    print("\n" + "=" * 50)
    print("📈 场景3：委派数据分析任务")
    print("=" * 50)

    response = agent.chat("""
让数据分析师分析以下销售数据：

月份: [1月, 2月, 3月, 4月, 5月, 6月]
销售额: [100, 120, 115, 140, 160, 180]
成本: [60, 70, 68, 80, 90, 95]

请分析：
1. 销售趋势
2. 利润变化
3. 增长率
""")
    print(f"\n回复：{response}")

    # ========== 场景4：委派代码审查任务 ==========
    print("\n" + "=" * 50)
    print("🔧 场景4：委派代码审查任务")
    print("=" * 50)

    response = agent.chat("""
让代码审查员审查以下Python代码：

```python
def calculate_discount(price, discount):
    if discount > 100:
        discount = 100
    final_price = price - price * discount / 100
    return final_price

def process_orders(orders):
    total = 0
    for order in orders:
        total = total + calculate_discount(order['price'], order['discount'])
    return total
```

请检查代码质量和潜在问题。
""")
    print(f"\n回复：{response}")

    # ========== 场景5：查看智能体团队 ==========
    print("\n" + "=" * 50)
    print("👥 场景5：查看智能体团队")
    print("=" * 50)

    response = agent.chat("列出所有已创建的智能体")
    print(f"\n回复：{response}")

    # 直接查看
    print("\n直接查看：")
    print(f"工具列表：{agent.list_tools()}")
    print(f"智能体列表：{agent.list_agents()}")

    print("\n" + "=" * 70)
    print("✅ 演示完成！")
    print("=" * 70)
    print()
    print("总结：")
    print("- 主智能体成功创建了专门化的子智能体")
    print("- 子智能体能够独立完成委派的任务")
    print("- 这展示了'智能体创造智能体'的元元编程能力")
    print()


def demo_tool_and_agent_collaboration():
    """演示工具和智能体的协作"""

    print("\n" + "=" * 70)
    print("🔄 工具与智能体协作演示")
    print("=" * 70)

    agent = PyBot(model="gpt-4", thread_id="collaboration-demo")

    # 先创建一个计算工具
    print("\n步骤1：创建计算工具")
    response = agent.chat("创建一个计算复利的工具，参数：本金principal、年利率rate、年数years")
    print(f"回复：{response}")

    # 再创建一个财务顾问智能体
    print("\n步骤2：创建财务顾问智能体")
    response = agent.chat("""
创建一个财务顾问智能体：
- 名称：financial_advisor
- 角色：理财顾问
- 专长：投资建议、风险评估、财务规划
""")
    print(f"回复：{response}")

    # 让财务顾问使用工具进行分析
    print("\n步骤3：综合分析")
    response = agent.chat("""
我有10万元想要投资，请：
1. 用复利计算工具计算：年利率5%，投资10年后的金额
2. 让财务顾问给出投资建议
""")
    print(f"回复：{response}")

    print("\n✅ 协作演示完成！")


if __name__ == "__main__":
    print("选择演示模式：")
    print("1. 智能体创建演示")
    print("2. 工具与智能体协作演示")
    print("3. 全部演示")

    choice = input("\n请输入选择 (1/2/3): ").strip()

    if choice == "1":
        demo_agent_creation()
    elif choice == "2":
        demo_tool_and_agent_collaboration()
    else:
        demo_agent_creation()
        demo_tool_and_agent_collaboration()
