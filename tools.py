from datetime import datetime
from ddgs import DDGS
import io
import contextlib
import traceback
import os



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












































AVAILABLE_TOOLS = {
    "get_system_time": get_system_time,
    "web_search": search_web,
    "execute_python_code": execute_python_code,
    "create_new_skills": create_new_skills
}