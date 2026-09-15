import os
import uuid
import time
import json
import asyncio
from typing import Dict, Any, List, Optional
import tempfile
from loguru import logger
import numpy as np

# Handle imports for speech processing
try:
    import librosa
    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    logger.warning("Transformers or audio libraries not available, using mock mode")
    TRANSFORMERS_AVAILABLE = False

# Try to import SpeechRecognition as a fallback
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    logger.warning("SpeechRecognition not available")
    SR_AVAILABLE = False

from utils.config import get_settings

class ASRHandler:
    """
    Handler for Automatic Speech Recognition (ASR) tasks
    Supports multiple ASR backends and handles transcription tasks
    """
    
    def __init__(self, model_path: str, model_name: str = "facebook/wav2vec2-base-960h"):
        """
        Initialize the ASR handler
        
        Args:
            model_path: Path to the model or model directory
            model_name: Name of the model to load
        """
        self.model_path = model_path
        self.model_name = model_name
        self.tasks_store = {}  # In-memory store for tasks
        self.sr_recognizer = None  # SpeechRecognition recognizer
        self.stream_sessions: Dict[str, Dict[str, Any]] = {}
        
        # Initialize models based on what's available
        if TRANSFORMERS_AVAILABLE:
            logger.info(f"Loading ASR model {model_name}")
            try:
                self._load_model()
            except Exception as e:
                logger.error(f"Error loading ASR model: {str(e)}")
                logger.warning("Using fallback ASR options")
                self.processor = None
                self.model = None
        else:
            logger.warning("Using fallback ASR options")
            self.processor = None
            self.model = None
        
        # Initialize SpeechRecognition as fallback
        if SR_AVAILABLE:
            self.sr_recognizer = sr.Recognizer()
            logger.info("SpeechRecognition initialized as fallback")
    
    def _load_model(self):
        """Load the ASR model and processor"""
        # Check if model exists locally first
        if os.path.exists(os.path.join(self.model_path, "processor_config.json")):
            logger.info(f"Loading model from local path: {self.model_path}")
            self.processor = Wav2Vec2Processor.from_pretrained(self.model_path)
            self.model = Wav2Vec2ForCTC.from_pretrained(self.model_path)
        else:
            # Download from Hugging Face
            logger.info(f"Downloading model from Hugging Face: {self.model_name}")
            self.processor = Wav2Vec2Processor.from_pretrained(self.model_name)
            self.model = Wav2Vec2ForCTC.from_pretrained(self.model_name)
            
            # Save model locally for future use
            os.makedirs(self.model_path, exist_ok=True)
            self.processor.save_pretrained(self.model_path)
            self.model.save_pretrained(self.model_path)
        
        # Move model to GPU if available
        if torch.cuda.is_available():
            self.model = self.model.to("cuda")
            logger.info("ASR model moved to GPU")
    
    async def transcribe_audio(self, task_id: str, file_path: str, language: str = "en") -> Dict[str, Any]:
        """
        Transcribe an audio file to text
        
        Args:
            task_id: Unique identifier for this transcription task
            file_path: Path to the audio file
            language: Language code for transcription
            
        Returns:
            Dictionary with transcription results
        """
        # Store initial task state
        filename = os.path.basename(file_path)
        task = {
            "task_id": task_id,
            "status": "processing",
            "audio_file": filename,
            "timestamp": time.time()
        }
        self.tasks_store[task_id] = task
        
        try:
            # Process in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            transcription = await loop.run_in_executor(
                None, self._process_audio_file, file_path, language
            )
            
            # Update task with results
            task.update({
                "status": "completed",
                "text": transcription["text"],
                "duration": transcription["duration"],
                "timestamp": time.time()
            })
            
            # Move the processed file to output directory if needed
            settings = get_settings()
            output_path = os.path.join(settings.audio_output_dir, filename)
            if os.path.exists(file_path) and file_path != output_path:
                try:
                    # Copy or move file to output dir
                    import shutil
                    shutil.copy2(file_path, output_path)
                except Exception as e:
                    logger.error(f"Error moving file to output directory: {str(e)}")
            
            # Save transcription to file
            try:
                transcription_file = os.path.join(
                    settings.transcriptions_dir, 
                    f"{os.path.splitext(filename)[0]}.json"
                )
                os.makedirs(settings.transcriptions_dir, exist_ok=True)
                with open(transcription_file, 'w') as f:
                    json.dump(task, f, indent=2)
            except Exception as e:
                logger.error(f"Error saving transcription to file: {str(e)}")
            
            return task
            
        except Exception as e:
            logger.error(f"Error transcribing audio: {str(e)}")
            task.update({
                "status": "error",
                "error": str(e),
                "timestamp": time.time()
            })
            return task
    
    def _process_audio_file(self, file_path: str, language: str) -> Dict[str, Any]:
        """
        Process audio file with the appropriate ASR engine
        
        Args:
            file_path: Path to the audio file
            language: Language code
            
        Returns:
            Dictionary with text and metadata
        """
        # Load and preprocess the audio file
        try:
            # Get file duration
            y, sr = librosa.load(file_path, sr=16000, mono=True)
            duration = librosa.get_duration(y=y, sr=sr)
            
            # Check if we should use wav2vec2 model
            if TRANSFORMERS_AVAILABLE and self.model and self.processor:
                return self._transcribe_with_wav2vec2(y, sr, duration)
                
            # Fallback to SpeechRecognition
            elif SR_AVAILABLE and self.sr_recognizer:
                return self._transcribe_with_speech_recognition(file_path, duration, language)
                
            else:
                # Mock transcription if no ASR available
                return {
                    "text": f"[Mock transcription for {os.path.basename(file_path)}]",
                    "duration": duration,
                    "engine": "mock"
                }
                
        except Exception as e:
            logger.error(f"Error processing audio file: {str(e)}")
            raise
    
    def _transcribe_with_wav2vec2(self, audio_array, sample_rate, duration):
        """Transcribe with wav2vec2 model"""
        # Preprocess audio
        inputs = self.processor(
            audio_array, 
            sampling_rate=sample_rate, 
            return_tensors="pt"
        )
        
        # Move to GPU if available
        if torch.cuda.is_available():
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        
        # Get logits
        with torch.no_grad():
            logits = self.model(inputs.input_values).logits
        
        # Get predicted IDs and decode
        predicted_ids = torch.argmax(logits, dim=-1)
        transcription = self.processor.batch_decode(predicted_ids)[0]
        
        return {
            "text": transcription,
            "duration": duration,
            "engine": "wav2vec2"
        }
    
    def _transcribe_with_speech_recognition(self, file_path, duration, language):
        """Transcribe with SpeechRecognition library"""
        with sr.AudioFile(file_path) as source:
            audio_data = self.sr_recognizer.record(source)
            
            # Use Google's ASR
            text = self.sr_recognizer.recognize_google(audio_data, language=language)
            
            return {
                "text": text,
                "duration": duration,
                "engine": "google_speech_api"
            }
    
    async def stream_session_open(self, session_id: Optional[str] = None) -> str:
        """Register a WebSocket streaming session (see docs/asr-streaming)."""
        sid = session_id or str(uuid.uuid4())
        self.stream_sessions[sid] = {"opened_at": time.time(), "chunks": 0}
        return sid

    async def stream_session_close(self, session_id: str) -> None:
        self.stream_sessions.pop(session_id, None)

    async def process_audio_chunk_scoped(
        self, session_id: str, audio_chunk: bytes
    ) -> Dict[str, Any]:
        """Process chunk within a session (increments per-session stats)."""
        sess = self.stream_sessions.get(session_id)
        if sess is not None:
            sess["chunks"] = sess.get("chunks", 0) + 1
        return await self.process_audio_chunk(audio_chunk)

    async def process_audio_chunk(self, audio_chunk: bytes) -> Dict[str, Any]:
        """
        Process a chunk of audio data from streaming sources

        Args:
            audio_chunk: Raw audio data bytes

        Returns:
            Dictionary with partial transcription results
        """
        # Save to temp file for processing
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_chunk)
            temp_path = temp_file.name

        try:
            # Process the audio file
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._process_audio_file, temp_path, "en"
            )

            # Clean up temp file
            os.unlink(temp_path)

            return {
                "status": "success",
                "text": result["text"],
                "is_final": True,
                "timestamp": time.time(),
            }

        except Exception as e:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except Exception:
                pass

            logger.error(f"Error processing audio chunk: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
            }
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a transcription task by ID"""
        return self.tasks_store.get(task_id)
    
    def update_task(self, task_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a transcription task"""
        if task_id in self.tasks_store:
            self.tasks_store[task_id].update(update_data)
            return self.tasks_store[task_id]
        return None
    
    def list_tasks(self, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        """List recent transcription tasks with pagination"""
        # Sort by timestamp (newest first)
        sorted_tasks = sorted(
            self.tasks_store.values(),
            key=lambda x: x.get("timestamp", 0),
            reverse=True
        )
        
        # Apply pagination
        return sorted_tasks[offset:offset+limit] 