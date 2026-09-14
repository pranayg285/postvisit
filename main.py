# main.py
import os
import uuid
import json
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import AsyncAzureOpenAI
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from fastapi.responses import FileResponse
from pathlib import Path
from datetime import datetime
from fastapi.concurrency import run_in_threadpool

load_dotenv()

# Import the PDF generator script we just created
from postvisit_report_gen import generate_postvisit_pdf

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
    total_score: str                
    tier: str
    tier_description: str
    tier_scale: str                 
    breakdown: DimensionBreakdown
    clinical_summary: str
    reference_links: List[str]

class ScoreRequest(BaseModel):
    session_id: str

class DoctorSummaryResponse(BaseModel):
    summary: str
    
class ReportRequest(BaseModel):
    session_id: str

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
    
    # 1. Dynamically fetch 2 real, working US clinical links based on the condition
    # Fallback to "general health" if diagnosis isn't found in the note
    condition = session_data["encounter_note"].get("diagnosis", "general health")
    try:
        query = f"{condition} patient guidelines site:cdc.gov OR site:aafp.org"
        search_results = DDGS().text(query, max_results=2)
        fetched_links = [res.get("href") for res in search_results]
    except Exception:
        # Fallback if search fails
        fetched_links = ["https://www.cdc.gov", "https://www.aafp.org"]
        
    formatted_links = "\n".join(fetched_links)
    
    # 2. Inject the links into the prompt alongside the note and transcript
    prompt = STABILITY_SCORE_PROMPT.format(
        encounter_note=json.dumps(session_data["encounter_note"], indent=2),
        transcript=formatted_transcript,
        fetched_links=formatted_links
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

@app.post("/generate-report", summary="Generate and Download Post-Visit PDF")
async def generate_report(request: ReportRequest):
    session_id = request.session_id
    
    # Define paths
    db_path = Path("DB") / session_id
    json_file_path = db_path / "postvisit.json"
    
    # 1. Check if the session is currently active in memory
    if session_id in sessions_db:
        session_data = sessions_db[session_id]
        encounter_note = session_data.get("encounter_note", {})

        # Await the LLM calls for Score and Summary
        score_request = ScoreRequest(session_id=session_id)
        stability_response = await calculate_stability_score(score_request)
        summary_response = await generate_doctor_summary(score_request)

        # Build the combined JSON payload
        report_data = {
            "patient_info": {
                "name": encounter_note.get("patient_name", "Unknown"),
                "dob": encounter_note.get("dob", "N/A"),
                "age": encounter_note.get("age", "N/A"),
                "sex": encounter_note.get("gender", "N/A")
            },
            "facility_info": {
                "name": "Smart EHR System",
                "phone": "☎️ (703) 202-1655",
                "address": "851 N Glebe Rd, Arlington, VA 22203"
            },
            "session_details": {
                "session_id": session_id,
                "session_date": datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            },
            "chief_complaint": encounter_note.get("chief_complaint", "Not specified"),
            "doctor_summary": summary_response.summary,
            "stability_score": stability_response.model_dump()
        }

        # Create directory and save JSON
        db_path.mkdir(parents=True, exist_ok=True)
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=4)
            
    # 2. If it's not in memory, check if the JSON file already exists on disk
    elif not json_file_path.exists():
        raise HTTPException(
            status_code=404, 
            detail="Session not found in memory, and no previously saved JSON report exists."
        )

    # 3. Generate the PDF (Runs whether we just created the JSON or found an existing one)
    pdf_path = await run_in_threadpool(generate_postvisit_pdf, session_id, "DB")
    
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=500, detail="Failed to generate PDF.")

    # 4. Return the PDF file for download
    return FileResponse(
        path=pdf_path, 
        filename=f"{session_id}_postvisit_report.pdf", 
        media_type="application/pdf"
    )
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)