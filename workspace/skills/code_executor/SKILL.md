---
name: code_executor
description: 代码执行技能，让智能体能够编写和运行Python代码
version: 1.0.0
author: system
enabled: true
---

# 代码执行器

让智能体能够编写和运行Python代码来解决问题。

## 能力
- 执行Python代码片段
- 数据处理和分析
- 文件读写操作
- 数学计算

## 系统提示
你可以通过创建工具来执行任意Python代码。工具代码在隔离的环境中运行，支持安装第三方依赖。
当需要进行计算、数据处理或文件操作时，优先创建工具来自动化这些任务。
