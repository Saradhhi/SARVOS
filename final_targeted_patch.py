import os

# 1. Patch agents/planner.py safely
p_path = "agents/planner.py"
if os.path.exists(p_path):
    with open(p_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Add the wrapper import cleanly
    if "from agents.autodeveloper_wrapper import LocalAutoDeveloperAgent" not in content:
        content = content.replace(
            "from agents.base import BaseAgent",
            "from agents.base import BaseAgent\nfrom agents.autodeveloper_wrapper import LocalAutoDeveloperAgent"
        )

    # Inject routing logic using the exact signature: def _decompose(self, task: Task) -> list[Task]:
    target_signature = "    def _decompose(self, task: Task) -> list[Task]:"
    routing_hook = (
        "    def _decompose(self, task: Task) -> list[Task]:\n"
        "        text = task.instruction.lower()\n"
        "        if 'develop' in text or 'autodeveloper' in text:\n"
        "            return [Task(agent=AgentName.AUTODEVELOPER, instruction=task.instruction, risk=RiskLevel.HIGH)]"
    )

    if "AgentName.AUTODEVELOPER" not in content:
        content = content.replace(target_signature, routing_hook)

    with open(p_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[SUCCESS] Planner micro-agent routing complete.")

# 2. Patch core/orchestrator.py safely
o_path = "core/orchestrator.py"
if os.path.exists(o_path):
    with open(o_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Target the end of the multi-line signature / docstring to avoid syntax breakage
    target_str = ") -> list[AgentResult]:\n        \"\"\"Called after the user answers a confirmation prompt.\"\"\""

    hook_code = (
        ") -> list[AgentResult]:\n"
        "        \"\"\"Called after the user answers a confirmation prompt.\"\"\"\n"
        "        if task.agent.value == 'autodeveloper' or getattr(task.agent, 'name', '') == 'AUTODEVELOPER':\n"
        "            from agents.autodeveloper_wrapper import LocalAutoDeveloperAgent\n"
        "            dev_agent = LocalAutoDeveloperAgent()\n"
        "            if approved:\n"
        "                dev_agent.core.step_deployment_pipeline()\n"
        "                dev_agent.core.execute_deployment_and_monitor()\n"
        "                dev_agent.core.run_telemetry_loop()\n"
        "                return [AgentResult(task_id=task.task_id, agent=task.agent, success=True, output='Local AutoDeveloper deployment executed and active monitoring established.', new_tasks=[])]\n"
        "            else:\n"
        "                dev_agent.core.update_state('IDLE', 'Execution directive rejected by user.')\n"
        "                return [AgentResult(task_id=task.task_id, agent=task.agent, success=False, output='AutoDeveloper workspace modifications discarded.', new_tasks=[])]\n"
    )

    if "LocalAutoDeveloperAgent" not in content:
        content = content.replace(target_str, hook_code)

    with open(o_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[SUCCESS] Orchestrator integration complete.")		
