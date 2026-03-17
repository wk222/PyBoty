# SCHEDULE — 定时任务配置

> 定义智能体的自动化周期任务。格式为 YAML 代码块。

## 任务列表

```yaml
tasks: []
# 示例:
# tasks:
#   - name: daily_summary
#     description: "生成每日工作摘要"
#     cron: "0 18 * * *"
#     prompt: "总结今天所有会话的关键内容，生成每日摘要"
#     enabled: false
#
#   - name: tool_health_check
#     description: "检查所有工具的健康状态"
#     cron: "0 */6 * * *"
#     prompt: "列出所有已创建的工具，检查它们是否正常工作"
#     enabled: false
```
