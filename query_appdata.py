import sqlite3
import json

def main():
    db_path = r'C:\Users\wgk\AppData\Local\PyBot\workspace\data\events.db'
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT id, type, payload FROM events WHERE payload LIKE '%create_custom_tool%' ORDER BY id DESC LIMIT 10")
        for row in cursor.fetchall():
            try:
                payload = json.loads(row[2])
                if payload.get('tool_name') == 'create_custom_tool':
                    print(f"ID: {row[0]} | Type: {row[1]}")
                    if 'args' in payload:
                        print("Args:", payload['args'])
                    if 'response' in payload:
                        print("Response:", payload['response'])
                    print("-" * 50)
            except Exception:
                pass
    except Exception as e:
        print(f"Error accessing DB: {e}")

if __name__ == '__main__':
    main()