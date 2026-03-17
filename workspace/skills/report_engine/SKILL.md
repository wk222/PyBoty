---
name: report_engine
description: JSON-configured SQL report templates with parameterized queries
version: 1.0.0
author: system
enabled: true
---

# report_engine

Create and run parameterized SQL report templates. Templates are stored in this skill's config.json file.

## Capabilities
- Create report templates with parameterized SQL
- Run reports with parameter substitution
- List available report templates
- Reports query the shared database (workspace/data/agent.db)

## System Prompt
You can create and run SQL report templates. Use create_report_template to define reusable reports with parameters, then run_report to execute them. Reports query the shared SQLite database. Templates persist in the skill config file.

## Dependencies
- N/A (uses Python stdlib sqlite3)
