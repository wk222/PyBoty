import json
import time
from pathlib import Path

from core.systems.runtime.pybot_bootstrap import build_runtime, create_llm_client
from core.systems.runtime.event_bus import event_bus, Event, EventType
from core.systems.runtime.admin_watcher import AdminWatcherDaemon
from core.systems.runtime.daemon import BackgroundDaemon
from core.systems.runtime.project_paths import ProjectPaths
from core.assets.apps.app_manager_registry import get_shared_app_manager
from core.systems.governance.approval_queue import ApprovalQueue
from agent import create_admin_agent

def main():
    print("Loading config.json...")
    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)
    llm_config = config["llm_config"]

    paths = ProjectPaths.from_root(".")
    
    print("Creating Admin Agent...")
    admin_agent = create_admin_agent(
        model=llm_config["model"],
        paths=paths,
        api_key=llm_config["api_key"],
        base_url=llm_config["api_base"],
        provider=llm_config["provider"],
    )
    
    # Start the admin loop so it can process tasks
    admin_agent.start_admin_loop()

    print(f"Initializing LLM client with model: {llm_config['model']}")
    llm = create_llm_client(
        model=llm_config["model"],
        temperature=llm_config["temperature"],
        api_key=llm_config["api_key"],
        base_url=llm_config["api_base"],
        provider=llm_config["provider"]
    )
    
    print("\n=== Testing Admin Watcher Daemon (Telemetry & Evolution) ===")
    print("Emitting mock error events to the event bus...")
    event_bus.emit(Event(type=EventType.ERROR, source="App_B", payload={"error": "Tool 'fetch_data' failed with 500 Internal Server Error"}))
    
    time.sleep(0.5)

    print("Manually triggering AdminWatcherDaemon analysis cycle...")
    dummy_daemon = BackgroundDaemon()
    watcher = AdminWatcherDaemon(llm=llm, daemon=dummy_daemon, workspace_dir=paths.workspace_dir, interval_sec=120)
    
    # Force the cycle
    watcher._run_analysis_cycle()

    # Wait for the Admin agent to pick up the event and create a task
    print("Waiting for Admin agent to create a task...")
    time.sleep(2)
    
    tasks = admin_agent.list_admin_tasks()
    print(f"Admin tasks count: {len(tasks)}")
    for t in tasks:
        print(f"Task: {t['name']} - Status: {t['status']}")
        
    print("\nWaiting for tasks to complete (up to 30s)...")
    for _ in range(15):
        tasks = admin_agent.list_admin_tasks()
        all_done = all(t['status'] in ('completed', 'failed') for t in tasks)
        if all_done and len(tasks) > 0:
            break
        time.sleep(2)
        
    print("\nFinal Task Status:")
    for t in admin_agent.list_admin_tasks():
        print(f"Task: {t['name']} - Status: {t['status']}")
        
    print("\nStopping Admin Agent...")
    admin_agent.stop_admin_loop()

if __name__ == "__main__":
    main()
