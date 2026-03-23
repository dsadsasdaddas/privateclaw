import json

class AgentFSM:
    def __init__(self,client,tool_config,available_tools, external_model=None):
        self.state = "thinking"
        self.client = client 
        self.tool_config = tool_config
        self.available_tools = available_tools
        self.external_model = external_model
        self.session_messages = [{"role":"system", "content":"你是一个无敌的助手,不完成任务不结束对话"} ]
        self.current_tool_calls = []
        self.final_answer = ""
    
#cheng xu qi dong
    def run(self, user_input:str) -> str:
        self.session_messages.append({"role":"user", "content":  user_input})
        self.state = "THINKING"
        

        while self.state != "FINISHED":

            if self.state == "THINKING":
                self._think()
            elif self.state == "EXECUTING":
                self._execute()
        return self.final_answer
    
    def _think(self):
        response = self.client.chat.completions.create(
            model="qwen-max",
            messages=self.session_messages,
            tools = self.tool_config,
            stream=False

        )
        
        message = response.choices[0].message

        msg_dict = message.model_dump(exclude_none = True)
        if msg_dict.get("content") is None:
            msg_dict["content"] = ""
        self.session_messages.append(msg_dict)

        if message.tool_calls:
            self.current_tool_calls = message.tool_calls
            self.state = "EXECUTING"
        else:
            self.final_answer = message.content
            self.state = "FINISHED"


    def _execute(self):
        for tool_call in self.current_tool_calls:
            
        



            func_name = tool_call.function.name
            func_args_str = tool_call.function.arguments

            tool_result = f"error: tool '{func_name}' not found."
            if func_name in self.available_tools:
                func = self.available_tools.get(func_name)

                
                
                try:
                    json_args = json.loads(func_args_str)#解包json字符串
                    tool_result = func(**json_args)
                except json.JSONDecodeError as e:
                    tool_result = f"致命格式错误：你生成的 arguments 不是合法的 JSON 格式。报错细节：{str(e)}。请检查是否有多余的文本、未转义的引号或代码块标记，并重新严格生成纯 JSON 数据！"
                except Exception as e:
                    tool_result = f"Error executing tool '{func_name}': {str(e)},pleaseYAML 说明书，提供所有 required 的必填参数（如 yaml_config）后重新调用本工具！"
                except Exception as e:
                    tool_result = f"Error executing tool '{func_name}': {str(e)}请分析错误并修改你的代码或参数。"
                
            
                
        
            
                
            
                
        
            tool_response = {
            "role":"tool",
            "content":str(tool_result),
            "tool_call_id": tool_call.id,
            "name":func_name,
        }
        self.session_messages.append(tool_response)
    
        self.state = "THINKING"
        print("继续思考")



