# agent.py
import os
from typing import TypedDict, Annotated, List, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage
from prompts import CHAT_SYSTEM_PROMPT
from dotenv import load_dotenv

load_dotenv()

# Define the state schema
class AgentState(TypedDict):
    messages: Annotated[List[Any], add_messages]
    encounter_note: Dict[str, Any]
    is_complete: bool

# Initialize Azure OpenAI Chat model
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    temperature=0.2,
)

def chat_node(state: AgentState):
    """Generates the next response and checks for the completion trigger."""
    # Inject encounter note into system prompt dynamically
    system_message = SystemMessage(
        content=CHAT_SYSTEM_PROMPT.format(encounter_note=state["encounter_note"])
    )
    
    # Construct conversation
    conversation = [system_message] + state["messages"]
    
    # Call the LLM
    response = llm.invoke(conversation)
    
    # Check if the flow is complete
    is_complete = "[FLOW_COMPLETE]" in response.content
    cleaned_content = response.content.replace("[FLOW_COMPLETE]", "").strip()
    
    return {
        "messages": [AIMessage(content=cleaned_content)],
        "is_complete": is_complete
    }

def should_continue(state: AgentState) -> str:
    """Routing function to determine if the graph should end."""
    if state.get("is_complete", False):
        return END
    return END

# Build the LangGraph
workflow = StateGraph(AgentState)
workflow.add_node("chat_assistant", chat_node)
workflow.set_entry_point("chat_assistant")
workflow.add_conditional_edges("chat_assistant", should_continue)

# Compile graph
chat_app = workflow.compile()