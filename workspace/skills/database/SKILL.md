---
name: database
description: Shared SQLite database layer for all skills and apps
version: 1.0.0
author: system
enabled: true
---

# database

Provides shared SQLite database access for PyBot. All data is stored in `workspace/data/agent.db`.

## Capabilities
- Execute read-only SQL queries (SELECT/WITH)
- Execute write SQL (CREATE TABLE, INSERT, UPDATE, DELETE)
- List all tables and their schemas
- Import CSV data into tables
- Get table statistics

## System Prompt
You have access to a shared SQLite database at `workspace/data/agent.db`. Use the database tools to create tables, import data, run queries, and manage structured data. The database persists across sessions.

## Dependencies
- N/A (uses Python stdlib sqlite3)
