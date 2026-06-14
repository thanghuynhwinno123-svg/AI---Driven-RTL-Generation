from langchain_core.messages import HumanMessage, SystemMessage
from .model_config import model_for


class TBAgent:
    def __init__(self, invoke_llm, extract_code, model=None):
        self.invoke_llm = invoke_llm
        self.extract_code = extract_code
        self.model = model or model_for("tb")

    async def invoke(self, system_prompt: str, human_prompt: str):
        response = await self.invoke_llm([SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)], model=self.model)
        return self.extract_code(response.content)
