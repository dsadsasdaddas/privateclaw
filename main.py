import os
from openai import OpenAI
import yaml
import json
from tools import AVAILABLE_TOOLS
from state_thinking import AgentFSM
from deepsearch import DeepSearch






def _get_api_key() -> str:
    """
    安全读取 API Key：
    1) 优先读取环境变量 DASHSCOPE_API_KEY
    2) 未配置时抛出明确错误，避免把密钥硬编码进代码仓库
    """
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未检测到 DASHSCOPE_API_KEY。请先在环境变量中设置，例如："
            "export DASHSCOPE_API_KEY='your_api_key'"
        )
    return api_key


client = OpenAI(
    api_key=_get_api_key(),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

#CALL THE LLM

def call_model(user_input:str, history_list: list):
    history_list.append({"role":"user","content":user_input})


    
    response = client.chat.completions.create(
        model="qwen3.5-plus",
        messages=history_list,
        stream=False
        )
    #export DASHSCOPE_API_KEY= "<API_KEY>"

    message =  response.choices[0].message
    print(response)
    msg_dict =  message.model_dump(exclude_none=True)
    if msg_dict.get("content") is None:
            msg_dict["content"] = ""
        
    history_list.append(msg_dict)
    return msg_dict["content"]
        

def llm_router(user_input: str)->str:
    messages = [
          {
            "role":"system",
            "content":"你是一个严格的意图分类器。如果用户的输入需要搜索外部信息、查天气、查新闻或执行代码类似复杂任务或者用户要求要用复杂模式，请仅回复 'COMPLEX'。如果用户只是简单的日常闲聊、打招呼或情感交流，请仅回复 'SIMPLE'。除了这两个词，绝对不要输出任何标点符号或额外字符。"
          },
          {"role":"user", "content": user_input}
     ]

    response = client.chat.completions.create(
          model = "qwen-turbo",
          messages = messages,
          temperature = 0.0,
          stream = False    

     )
    
    result = response.choices[0].message.content.strip().upper()

    if "COMPLEX" in result:
        return "COMPLEX"
    return "SIMPLE"


           


    
    


          

     

           


    
    

#deactivate python -m venv myenv  .\myenv\Scripts\activate python main.py
def _read_yaml_file(file_path:str)->list:
    if not os.path.exists(file_path):
        return []
    
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            date = yaml.safe_load(file)
            if date is None:
                return []
            return date
    except Exception as e:
        return[]


def load_tool_config(file_path:str)->list:#载入文件
    core_tools = _read_yaml_file("tool_config.yaml")
    dynamic_tools = _read_yaml_file("dynamic_config.yaml")

    if isinstance(dynamic_tools, dict):
        dynamic_tools = [dynamic_tools]
    if not isinstance(dynamic_tools, list):
        dynamic_tools = [] 
    return core_tools + dynamic_tools
    







def main():
        
    tool_config = load_tool_config("tool_config.yaml")


    fsm_agent = AgentFSM(client, tool_config,AVAILABLE_TOOLS)
    deep_search_agent = DeepSearch(client)

    chat_history = [
        {"role":"system", "content":"你是一个无敌的助手,不完成任务不结束对话"}
    ]


    while True:
        user_input = input("User:")
        if user_input == "quit":
            break

        if "深度搜索" in user_input:
            query = user_input.replace("深度搜索", "", 1).strip()
            if not query:
                query = user_input
            result = deep_search_agent.run(query)
            print(f"output:{result}")
            continue

        task_type = llm_router(user_input)

        if task_type == "COMPLEX":
            result = fsm_agent.run(user_input)
        else:
            result = call_model(user_input,chat_history)
       

        
        print(f"output:{result}")
        


if __name__ == "__main__":
    main()
