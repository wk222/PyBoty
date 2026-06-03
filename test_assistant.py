import asyncio
from core.systems.runtime.project_paths import ProjectPaths
from agent import create_tool_creator_agent

async def main():
    print("Initializing agent...")
    agent = create_tool_creator_agent(
        model="gpt-4o-mini", # Use a fast model for testing
        thread_id="test-assistant-mode",
        enable_agent_creation=False
    )
    
    print("\nTesting chat (simple greeting)...")
    reply = agent.chat("Hello, who are you?")
    print(f"Reply: {reply}")
    
    print("\nTesting tool call (create_app)...")
    reply = agent.chat("创建一个名字叫 test_app_123 的聊天应用，描述是测试应用")
    print(f"Reply: {reply}")

if __name__ == "__main__":
    asyncio.run(main())
