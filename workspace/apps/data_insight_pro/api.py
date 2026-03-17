import sqlite3
import pandas as pd
import json
import os

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

if action == 'list_tables':
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row['name'] for row in cursor.fetchall()]
    conn.close()
    result = {"tables": tables}

elif action == 'get_table_preview':
    table_name = payload.get('table_name')
    conn = get_db_connection()
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT 10", conn)
        result = {
            "columns": df.columns.tolist(),
            "data": df.to_dict(orient='records')
        }
    except Exception as e:
        result = {"error": str(e)}
    finally:
        conn.close()

elif action == 'get_column_stats':
    table_name = payload.get('table_name')
    conn = get_db_connection()
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        stats = {}
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                stats[col] = {
                    "type": "numeric",
                    "min": float(df[col].min()),
                    "max": float(df[col].max()),
                    "mean": float(df[col].mean()),
                    "median": float(df[col].median()),
                    "std": float(df[col].std()) if not pd.isna(df[col].std()) else 0
                }
            else:
                top_values = df[col].value_counts().head(10).to_dict()
                stats[col] = {
                    "type": "categorical",
                    "unique_count": int(df[col].nunique()),
                    "top_values": {str(k): int(v) for k, v in top_values.items()}
                }
        result = {"stats": stats}
    except Exception as e:
        result = {"error": str(e)}
    finally:
        conn.close()

elif action == 'execute_sql':
    sql = payload.get('sql')
    conn = get_db_connection()
    try:
        # 只允许 SELECT 语句
        if not sql.strip().lower().startswith('select'):
             result = {"error": "Only SELECT statements are allowed."}
        else:
            df = pd.read_sql_query(sql, conn)
            result = {
                "columns": df.columns.tolist(),
                "data": df.to_dict(orient='records')
            }
    except Exception as e:
        result = {"error": str(e)}
    finally:
        conn.close()

elif action == 'import_file':
    # 注意：在子应用中，文件上传通常通过 apiCall 传递文件路径
    file_path = payload.get('file_path')
    table_name = payload.get('table_name')
    
    if not file_path or not table_name:
        result = {"error": "Missing file_path or table_name"}
    else:
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            elif file_path.endswith(('.xls', '.xlsx')):
                df = pd.read_excel(file_path)
            else:
                raise ValueError("Unsupported file format")
            
            conn = get_db_connection()
            df.to_sql(table_name, conn, if_exists='replace', index=False)
            conn.close()
            result = {"success": True, "message": f"Table '{table_name}' created successfully."}
        except Exception as e:
            result = {"error": str(e)}
