import sqlite3
import json

conn = sqlite3.connect('workspace/data/events.db')
cursor = conn.execute("SELECT payload FROM events WHERE type IN ('llm_end', 'tool_call', 'tool_result') ORDER BY id DESC LIMIT 5")
for row in cursor.fetchall():
    try:
        data = json.loads(row[0])
        print(f"Keys: {list(data.keys())}")
        if 'thread_id' in data: print(f"Thread: {data['thread_id']}")
    except:
        pass
