from app.agents.state import TripGraphState
from app.core.logger import node_log


@node_log
def compose_response(state: TripGraphState) -> dict:
    result = state["validation_result"]
    warnings = [i.message for i in result.issues if i.severity == "warning"]
    text = f"行程已保存为 v{state['saved_version_no']}。"
    if warnings:
        text += "\n提醒：" + "；".join(warnings)
    return {"response": text}


@node_log
def compose_unresolved(state: TripGraphState) -> dict:
    errors = [
        issue.message
        for issue in state["validation_result"].issues
        if issue.severity == "error"
    ]
    return {"response": "行程暂未保存，请修改以下问题：\n" + "\n".join(f"- {e}" for e in errors)}
