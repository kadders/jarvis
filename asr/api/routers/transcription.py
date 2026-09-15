from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException, Depends, Form
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
import os
import uuid
import json
import httpx
from loguru import logger
import time

from api.deps import get_asr_handler
from models.asr_handler import ASRHandler
from utils.config import get_settings

router = APIRouter(tags=["transcription"])

class TranscriptionResponse(BaseModel):
    """Response model for transcription results"""
    task_id: str
    status: str
    text: Optional[str] = None
    audio_file: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    timestamp: float

@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: Optional[str] = Form("en"),
    forward_to_llm: Optional[bool] = Form(None),
    asr_handler: ASRHandler = Depends(get_asr_handler)
):
    """
    Transcribe an uploaded audio file to text
    
    - **file**: The audio file to transcribe (wav, mp3, ogg, flac supported)
    - **language**: Language code (default: en)
    - **forward_to_llm**: Whether to forward transcription to LLM API
    """
    settings = get_settings()
    
    # Validate file format
    filename = file.filename
    file_extension = filename.split(".")[-1].lower()
    
    if file_extension not in settings.supported_formats:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file format. Supported formats: {', '.join(settings.supported_formats)}"
        )
    
    # Generate unique ID for this task
    task_id = str(uuid.uuid4())
    
    # Save file to input directory
    timestamp = int(time.time())
    save_filename = f"{timestamp}_{task_id}.{file_extension}"
    save_path = os.path.join(settings.audio_input_dir, save_filename)
    
    try:
        # Save the uploaded file
        with open(save_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    
    # Process transcription in background to avoid blocking
    background_tasks.add_task(
        process_transcription,
        task_id=task_id,
        file_path=save_path,
        language=language,
        forward_to_llm=forward_to_llm if forward_to_llm is not None else settings.forward_to_llm,
        asr_handler=asr_handler
    )
    
    # Return initial response
    return TranscriptionResponse(
        task_id=task_id,
        status="processing",
        audio_file=save_filename,
        timestamp=time.time()
    )

@router.get("/transcription/{task_id}", response_model=TranscriptionResponse)
async def get_transcription(
    task_id: str, 
    asr_handler: ASRHandler = Depends(get_asr_handler)
):
    """
    Get the status or result of a transcription task
    
    - **task_id**: The ID of the transcription task
    """
    # Get task from handler
    task = asr_handler.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    return TranscriptionResponse(**task)

@router.get("/transcriptions", response_model=List[TranscriptionResponse])
async def list_transcriptions(
    limit: int = 10,
    offset: int = 0,
    asr_handler: ASRHandler = Depends(get_asr_handler)
):
    """
    List recent transcriptions
    
    - **limit**: Maximum number of transcriptions to return
    - **offset**: Offset for pagination
    """
    transcriptions = asr_handler.list_tasks(limit, offset)
    return transcriptions

async def process_transcription(
    task_id: str,
    file_path: str,
    language: str,
    forward_to_llm: bool,
    asr_handler: ASRHandler
):
    """Background task to process transcription and optionally forward to LLM API"""
    settings = get_settings()
    
    try:
        # Get transcription from audio
        result = await asr_handler.transcribe_audio(task_id, file_path, language)
        
        # Forward to LLM API if enabled
        if forward_to_llm and result.get("status") == "completed":
            try:
                await forward_transcription_to_llm(result)
            except Exception as e:
                logger.error(f"Error forwarding to LLM API: {str(e)}")
                # Don't fail the task if forwarding fails
    
    except Exception as e:
        logger.error(f"Error in transcription task: {str(e)}")
        # Update task with error
        asr_handler.update_task(task_id, {
            "status": "error",
            "error": str(e)
        })

async def forward_transcription_to_llm(transcription_result: Dict[str, Any]):
    """Forward transcription to LLM API for processing"""
    settings = get_settings()
    
    # Create the event payload
    payload = {
        "event_type": "transcription",
        "content": {
            "text": transcription_result.get("text", ""),
            "audio_file": transcription_result.get("audio_file", ""),
            "duration": transcription_result.get("duration", 0),
            "task_id": transcription_result.get("task_id", "")
        },
        "source": "asr_service",
        "timestamp": str(int(time.time()))
    }
    
    # Send to LLM API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.llm_api_url,
            json=payload,
            timeout=30.0
        )
        
        if response.status_code != 200:
            logger.error(f"Error from LLM API: {response.text}")
            raise Exception(f"Error from LLM API: {response.status_code}")
        
        return response.json() 