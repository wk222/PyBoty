---
name: data_importer
description: Import Excel and CSV files into the shared database with intelligent column mapping
version: 1.0.0
author: system
enabled: true
---

# data_importer

Import data from Excel (.xlsx) and CSV files into the shared SQLite database.

## Capabilities
- Import Excel files (reads sheets, auto-detects columns)
- Import CSV files
- Preview file contents before import
- Auto-create tables from file structure

## System Prompt
You can import Excel and CSV files into the database. Files should be uploaded to workspace/uploads/ first. Use preview_file to inspect the data, then import_file to load it into a database table.

## Dependencies
- openpyxl (for Excel support)
