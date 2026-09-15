#!/usr/bin/env python3

"""
Generate test audio files for ASR testing using text-to-speech.
This script creates WAV files with spoken text that can be used to test the ASR service.
"""

import os
import argparse
from pathlib import Path
import time
from datetime import datetime
from gtts import gTTS
import uuid

# Default output directory
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", "audio_input")

def generate_audio(text, output_dir, lang="en", slow=False):
    """Generate audio file from text using Google Text-to-Speech"""
    # Create output dir if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Create TTS object
    tts = gTTS(text=text, lang=lang, slow=slow)
    
    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"test_audio_{timestamp}_{uuid.uuid4().hex[:8]}.mp3"
    output_path = os.path.join(output_dir, filename)
    
    # Save audio file
    tts.save(output_path)
    print(f"Generated audio file: {output_path}")
    return output_path

def main():
    parser = argparse.ArgumentParser(description="Generate test audio for ASR testing")
    parser.add_argument("-t", "--text", required=False, 
                        default="This is a test audio file for speech recognition. It should be transcribed by the ASR service.",
                        help="Text to convert to speech")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-l", "--lang", default="en", 
                        help="Language code (default: en)")
    parser.add_argument("-s", "--slow", action="store_true",
                        help="Speak slowly")
    parser.add_argument("-c", "--count", type=int, default=1,
                        help="Number of sample files to generate")
    parser.add_argument("--sample-texts", action="store_true",
                        help="Use sample texts instead of the provided text")
    
    args = parser.parse_args()
    
    sample_texts = [
        "Hello, this is a test of the automatic speech recognition system.",
        "The quick brown fox jumps over the lazy dog.",
        "Please transcribe this audio and send it to the language model for processing.",
        "Artificial intelligence is transforming how we interact with machines.",
        "Speech recognition technology enables voice-driven user interfaces.",
        "Can you understand what I'm saying? This is a test of accuracy."
    ]
    
    # Generate the specified number of audio files
    for i in range(args.count):
        if args.sample_texts and args.count > 1:
            # Use sample texts if requested
            text = sample_texts[i % len(sample_texts)]
        else:
            text = args.text
            
        # Add number if generating multiple files with the same text
        if args.count > 1 and not args.sample_texts:
            text = f"Sample {i+1}: {text}"
            
        generate_audio(text, args.output_dir, args.lang, args.slow)
        
        # Add a small delay if generating multiple files
        if args.count > 1:
            time.sleep(0.5)
    
    print(f"Generated {args.count} audio file(s) in {args.output_dir}")
    
if __name__ == "__main__":
    main() 