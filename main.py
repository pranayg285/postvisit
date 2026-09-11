# main.py
import os
import uuid
import json
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import AsyncAzureOpenAI
from dotenv import load_dotenv

load_dotenv()

# Import the LangGraph app and prompts
from agent import chat_app
from prompts import STABILITY_SCORE_PROMPT, DOCTOR_SUMMARY_PROMPT

app = FastAPI(title="Post-Visit Check-in API")

# Initialize the Azure OpenAI async client
client = AsyncAzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION")
)
DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

# --- In-Memory Database for Testing ---
sessions_db = {}

# --- Schemas ---
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    encounter_note: Optional[Dict[str, Any]] = None
    user_message: str

class ChatResponse(BaseModel):
    session_id: str
    assistant_message: str
    is_completed: bool
class DimensionBreakdown(BaseModel):
    condition_trajectory_score: str # Changed from int to str for "X/40" format
    adherence_score: str            # Changed from int to str for "X/30" format
    red_flag_score: str             # Changed from int to str for "X/30" format
    rationale: str

class StabilityScoreResponse(BaseModel):
    total_score: str                # Changed from int to str for "X/100" format
    tier: str
    tier_description: str
    tier_scale: str                 # NEW: Static message showing the three tiers
    breakdown: DimensionBreakdown
    clinical_summary: str

class ScoreRequest(BaseModel):
    session_id: str

class DoctorSummaryResponse(BaseModel):
    summary: str

# --- Endpoints ---
@app.post("/chat", response_model=ChatResponse)
async def post_visit_chat(request: ChatRequest):
    from langchain_core.messages import HumanMessage, AIMessage
    
    session_id = request.session_id

    if not session_id or session_id not in sessions_db:
        if not request.encounter_note:
            raise HTTPException(status_code=400, detail="encounter_note is required to start a new session.")
        
        session_id = str(uuid.uuid4())
        sessions_db[session_id] = {
            "encounter_note": request.encounter_note,
            "history": []
        }
        
    session_data = sessions_db[session_id]
    
    langchain_messages = []
    for msg in session_data["history"]:
        if msg["role"] == "user":
            langchain_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            langchain_messages.append(AIMessage(content=msg["content"]))
            
    langchain_messages.append(HumanMessage(content=request.user_message))
    session_data["history"].append({"role": "user", "content": request.user_message})
    
    initial_state = {
        "messages": langchain_messages,
        "encounter_note": session_data["encounter_note"],
        "is_complete": False
    }
    
    try:
        final_state = chat_app.invoke(initial_state)
        latest_message = final_state["messages"][-1].content
        
        session_data["history"].append({"role": "assistant", "content": latest_message})
        
        return ChatResponse(
            session_id=session_id,
            assistant_message=latest_message,
            is_completed=final_state["is_complete"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stability-score", response_model=StabilityScoreResponse)
async def calculate_stability_score(request: ScoreRequest):
    if request.session_id not in sessions_db:
        raise HTTPException(status_code=404, detail="Session not found.")
        
    session_data = sessions_db[request.session_id]
    
    formatted_transcript = "\n".join(
        [f"{msg['role'].upper()}: {msg['content']}" for msg in session_data["history"]]
    )
    
    prompt = STABILITY_SCORE_PROMPT.format(
        encounter_note=json.dumps(session_data["encounter_note"], indent=2),
        transcript=formatted_transcript
    )

    try:
        completion = await client.beta.chat.completions.parse(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": "You are a clinical scoring agent."},
                {"role": "user", "content": prompt}
            ],
            response_format=StabilityScoreResponse,
            temperature=0.1
        )
        return completion.choices[0].message.parsed
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/doctor-summary", response_model=DoctorSummaryResponse)
async def generate_doctor_summary(request: ScoreRequest):
    if request.session_id not in sessions_db:
        raise HTTPException(status_code=404, detail="Session not found.")
        
    session_data = sessions_db[request.session_id]
    
    formatted_transcript = "\n".join(
        [f"{msg['role'].upper()}: {msg['content']}" for msg in session_data["history"]]
    )
    
    prompt = DOCTOR_SUMMARY_PROMPT.format(
        encounter_note=json.dumps(session_data["encounter_note"], indent=2),
        transcript=formatted_transcript
    )

    try:
        completion = await client.chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": "You are a clinical summarization agent."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        return DoctorSummaryResponse(summary=completion.choices[0].message.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)