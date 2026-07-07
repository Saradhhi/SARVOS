from agents.base import BaseAgent
from core.schemas import Task, AgentResult
from agents.autodeveloper_agent import AutoDeveloperCore
class LocalAutoDeveloperAgent(BaseAgent):
    def __init__(self):
        self.core = AutoDeveloperCore(config_path="config.json")
    def execute(self, task: Task) -> AgentResult:
        success = self.core.step_execution_pipeline(task.description)
        state = self.core.get_current_state()
        if success:
            return AgentResult(
                output=f"[GATED] Pipeline stopped at Gate 1. Current State: {state['current_state']}. Message: {state['message']}",
                new_tasks=[]
            )
        else:
            return AgentResult(
                output=f"[FAILED] Pipeline aborted. Message: {state['message']}",
                new_tasks=[]
            )
