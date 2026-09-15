import os
import asyncio
import time
from typing import Dict, Any, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from loguru import logger

# Global state for the monitor
_monitor_state: Dict[str, Any] = {
    "active": False,
    "task": None,
    "observer": None,
    "files_processed": 0,
    "last_processed_file": None,
    "start_time": None,
}

# Set to keep track of already processed files
_processed_files: Set[str] = set()

# Reference to ASR handler
_asr_handler = None

class AudioFileHandler(FileSystemEventHandler):
    """Handler for file system events in the watched directory"""
    
    def __init__(self, directory_path):
        self.directory_path = directory_path
        self.supported_extensions = {'.wav', '.mp3', '.ogg', '.flac', '.m4a'}
    
    def on_created(self, event):
        """Handle file creation events"""
        if not event.is_directory:
            file_path = event.src_path
            file_ext = os.path.splitext(file_path)[1].lower()
            
            if file_ext in self.supported_extensions:
                logger.info(f"New audio file detected: {file_path}")
                asyncio.create_task(process_audio_file(file_path))

async def process_audio_file(file_path: str):
    """Process a newly detected audio file"""
    global _asr_handler, _processed_files, _monitor_state
    
    # Skip if file already processed
    if file_path in _processed_files:
        return
    
    # Add to processed files set
    _processed_files.add(file_path)
    
    # Generate task ID from filename
    filename = os.path.basename(file_path)
    task_id = f"auto_{int(time.time())}_{filename}"
    
    try:
        # Call ASR handler to transcribe the file
        logger.info(f"Processing audio file: {file_path}")
        result = await _asr_handler.transcribe_audio(task_id, file_path, "en")
        
        # Update monitor state
        _monitor_state["files_processed"] += 1
        _monitor_state["last_processed_file"] = filename
        
        # Forward to LLM if transcription was successful
        if result.get("status") == "completed":
            from api.routers.transcription import forward_transcription_to_llm
            try:
                await forward_transcription_to_llm(result)
            except Exception as e:
                logger.error(f"Error forwarding to LLM API: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error processing file {file_path}: {str(e)}")

async def directory_polling_task(directory_path: str):
    """Task to poll directory for files that might have been added before monitor started"""
    global _processed_files, _monitor_state
    
    while _monitor_state["active"]:
        try:
            # Get all audio files in the directory
            for filename in os.listdir(directory_path):
                file_path = os.path.join(directory_path, filename)
                
                # Process only files (not directories) that we haven't seen yet
                if os.path.isfile(file_path) and file_path not in _processed_files:
                    file_ext = os.path.splitext(file_path)[1].lower()
                    if file_ext in {'.wav', '.mp3', '.ogg', '.flac', '.m4a'}:
                        await process_audio_file(file_path)
            
            # Wait for polling interval before checking again
            await asyncio.sleep(5)
        
        except Exception as e:
            logger.error(f"Error in directory polling task: {str(e)}")
            await asyncio.sleep(5)  # Wait before retrying

async def start_monitoring(directory_path: str, asr_handler):
    """Start monitoring the audio input directory"""
    global _monitor_state, _asr_handler, _processed_files
    
    if _monitor_state["active"]:
        logger.warning("Audio monitor already running")
        return
    
    # Set ASR handler
    _asr_handler = asr_handler
    
    # Reset state
    _monitor_state["active"] = True
    _monitor_state["start_time"] = time.time()
    _processed_files = set()
    
    # Create the directory if it doesn't exist
    os.makedirs(directory_path, exist_ok=True)
    
    # Set up watchdog observer
    event_handler = AudioFileHandler(directory_path)
    observer = Observer()
    observer.schedule(event_handler, directory_path, recursive=False)
    observer.start()
    _monitor_state["observer"] = observer

    # Start polling task for existing files
    polling_task = asyncio.create_task(directory_polling_task(directory_path))
    _monitor_state["task"] = polling_task

    logger.info(f"Started monitoring audio directory: {directory_path}")

async def stop_monitoring():
    """Stop the audio directory monitor"""
    global _monitor_state
    
    if not _monitor_state["active"]:
        return
    
    # Set state to inactive
    _monitor_state["active"] = False
    
    # Cancel polling task if running
    if _monitor_state["task"] and not _monitor_state["task"].done():
        _monitor_state["task"].cancel()
        try:
            await _monitor_state["task"]
        except asyncio.CancelledError:
            pass
    
    _monitor_state["task"] = None

    obs = _monitor_state.get("observer")
    if obs is not None:
        try:
            obs.stop()
            obs.join(timeout=5.0)
        except Exception as e:
            logger.warning(f"Error stopping watchdog observer: {e}")
        _monitor_state["observer"] = None

    logger.info("Stopped audio directory monitoring")


def bind_asr_handler(asr_handler) -> None:
    """Point the directory monitor at the current ASR handler (e.g. after config hot-reload)."""
    global _asr_handler
    _asr_handler = asr_handler


def get_monitor_status() -> Dict[str, Any]:
    """Get the current status of the audio monitor"""
    return {
        "active": _monitor_state["active"],
        "files_processed": _monitor_state["files_processed"],
        "last_processed_file": _monitor_state["last_processed_file"],
        "uptime": time.time() - _monitor_state["start_time"] if _monitor_state["start_time"] else 0
    } 