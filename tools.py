from datetime import datetime
from ddgs import DDGS
import io
import contextlib
import traceback
import os
import shlex
import subprocess
import threading
import uuid
import re



def get_system_time() ->str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def search_web(query: str) -> list:
    with DDGS() as ddgs:
        result = ddgs.text(
            query,
            max_results=5

        )
    return list(result)



def execute_python_code(code_string: str) ->str:
    captured_output = io.StringIO()
    print(f"\准备执行大模型写的代码:\n{code_string}\n")

    try:
        with contextlib.redirect_stdout(captured_output):
            print(f"Executing code:\n{code_string}")
            exec(code_string, {})
        return captured_output.getvalue()
    except  Exception:
        error_message = traceback.format_exc()
        return f"Error executing code:\n{error_message}"
    


def create_new_skills(skill_name:str, python_code:str,yaml_config:str) -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    skill_dir = os.path.join(base_dir, "skills")

    if not os.path.exists(skill_dir):
        os.makedirs(skill_dir)


    py_file_path = os.path.join(skill_dir, f"{skill_name}.py")
    with open(py_file_path, "w", encoding="utf-8") as f:
        f.write(python_code)
    

    dynamic_yaml_path = os.path.join(base_dir, f"dynamic_config.yaml")
    with open(dynamic_yaml_path, "a",encoding="utf-8") as f:
        f.write(f"\n{yaml_config.strip()}\n")

    return f"Skill '{skill_name}' created successfully with Python code and new YAML config."


DANGEROUS_COMMANDS = {
    "rm",
    "reboot",
    "shutdown",
    "poweroff",
    "halt",
    "mkfs",
    "dd",
    "fdisk",
    "killall",
    "chown",
}

SCHEDULED_TASKS = {}


def is_dangerous_command(command: str) -> bool:
    if not isinstance(command, str) or not command.strip():
        return True

    command_segments = [
        segment.strip()
        for segment in re.split(r"(?:\r?\n|;|&&|\|\||\|)", command)
        if segment.strip()
    ]
    if not command_segments:
        return True

    if re.search(r"\bsudo\s+rm\b", command, flags=re.IGNORECASE):
        return True
    if ":(){:|:&};:" in command.replace(" ", ""):
        return True

    risky_patterns = [r"\brm\s+-", r"\bmkfs\b", r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b"]

    for segment in command_segments:
        if re.search("|".join(risky_patterns), segment, flags=re.IGNORECASE):
            return True

    try:
        parsed_segments = [shlex.split(segment) for segment in command_segments]
    except Exception:
        return True

    for tokens in parsed_segments:
        if not tokens:
            continue

        first = tokens[0].lower()
        if first in DANGEROUS_COMMANDS:
            return True

    return False


def exec_cli_command(command: str) -> str:
    """
    受控 CLI 执行工具：
    1) 先做危险命令拦截
    2) 由外层 Agent 决定是否在拿到人类许可后调用
    """
    if is_dangerous_command(command):
        return "已拒绝执行：检测到危险命令或非法命令。"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return stdout or "命令执行成功（无输出）。"
        return f"命令执行失败（code={result.returncode})\\nstdout:\\n{stdout}\\nstderr:\\n{stderr}"
    except Exception as e:
        return f"命令执行异常：{e}"


def schedule_cli_command(delay_seconds: int, command: str) -> str:
    """
    定时执行 CLI 命令（秒级）。
    - 仅在到点时执行
    - 底层复用 exec_cli_command（危险命令仍会被拦截）
    """
    if delay_seconds <= 0:
        return "delay_seconds 必须大于 0。"
    if delay_seconds > 86400:
        return "delay_seconds 过大，当前仅支持 86400 秒内任务。"
    if is_dangerous_command(command):
        return "已拒绝定时任务：检测到危险命令。"

    task_id = uuid.uuid4().hex[:8]

    def _run():
        result = exec_cli_command(command)
        SCHEDULED_TASKS[task_id]["status"] = "done"
        SCHEDULED_TASKS[task_id]["result"] = result
        print(f"[SCHEDULE][{task_id}] command done: {command} -> {str(result)[:180]}")

    timer = threading.Timer(delay_seconds, _run)
    timer.daemon = True
    SCHEDULED_TASKS[task_id] = {
        "status": "scheduled",
        "delay_seconds": delay_seconds,
        "command": command,
        "result": "",
    }
    timer.start()

    return f"定时任务已创建，task_id={task_id}，将在 {delay_seconds} 秒后执行：{command}"












































AVAILABLE_TOOLS = {
    "get_system_time": get_system_time,
    "web_search": search_web,
    "execute_python_code": execute_python_code,
    "create_new_skills": create_new_skills,
    "exec_cli_command": exec_cli_command,
    "schedule_cli_command": schedule_cli_command,
} 
