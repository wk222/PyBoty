---
name: circuit_breaker
description: Circuit Breaker for reliable service calls. Prevents cascading failures by opening when a resource fails repeatedly.
version: 1.0.0
author: PyBot Admin
tags: resiliency, reliability, infrastructure
---

# circuit_breaker

Circuit Breaker for reliable service calls. Prevents cascading failures by opening when a resource fails repeatedly.

## 使用说明

# Circuit Breaker Skill

This skill provides a mechanism to check and report the status of external resource calls.

## Usage

1. **Check Status**: Before calling a tool, use `check_circuit` to see if the resource is OPEN.
2. **Report Result**: After calling a tool, use `report_circuit_result` to update failure counts.

## State Logic
- Transition to OPEN if `consecutive_failures` >= `threshold` (default 5).
- Auto-reset (HALF_OPEN) after `reset_timeout_seconds` (default 60).

