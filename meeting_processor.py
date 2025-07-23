#!/usr/bin/env python3
"""
Meeting Video Processor and Documentation Tool

A comprehensive tool for processing video files and generating meeting documentation
using Google Gemini AI. Supports automatic video conversion, audio extraction,
frame extraction, and AI-powered meeting analysis.
"""

import os
import sys
import json
import logging
import argparse
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import tempfile
import platform

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    pass

# Import optional dependencies
try:
    import questionary
    import requests
except ImportError:
    questionary = None
    requests = None


@dataclass
class Config:
    """Configuration class for the meeting processor."""
    ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
    handbrake_path: str = os.getenv("HANDBRAKE_PATH", "HandBrakeCLI")
    handbrake_preset_file: str = os.getenv("HANDBRAKE_PRESET_FILE", "Meeting.json")
    handbrake_preset_name: str = os.getenv("HANDBRAKE_PRESET_NAME", "Meeting")
    frame_interval: int = int(os.getenv("FRAME_INTERVAL", "60"))
    default_prompt: str = os.getenv("DEFAULT_PROMPT", "prompt.txt")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    preferred_date_source: str = os.getenv("PREFERRED_DATE_SOURCE", "metadata")
    output_dir: str = ""
    target_directory: str = ""
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true"
    no_cleanup: bool = os.getenv("NO_CLEANUP", "false").lower() == "true"
    model_limits: Dict[str, Dict[str, Any]] = None
    
    def __post_init__(self):
        """Initialize model limits from file or defaults."""
        if self.model_limits is None:
            self.model_limits = self._load_model_limits()
    
    def _load_model_limits(self) -> Dict[str, Dict[str, Any]]:
        """Load model limits from file or return defaults."""
        model_limits_file = os.getenv("MODEL_LIMITS_FILE", "model_limits.json")
        model_limits_path = Path(__file__).parent / model_limits_file
        
        if model_limits_path.exists():
            try:
                with open(model_limits_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load model limits from {model_limits_file}: {e}")
                print("Using default model limits...")
        
        # Default model limits if file not found or invalid
        return {
            "gemini-2.5-pro": {
                "max_input_tokens": 1048576,
                "max_output_tokens": 65535,
                "max_images_per_prompt": 3000,
                "max_image_size_mb": 7,
                "max_audio_length_hours": 8.4,
                "max_audio_files_per_prompt": 1,
                "parameter_defaults": {
                    "temperature": 0.3,
                    "top_p": 0.95,
                    "top_k": 64,
                    "candidate_count": 1
                }
            },
            "gemini-2.5-flash": {
                "max_input_tokens": 1048576,
                "max_output_tokens": 65535,
                "max_images_per_prompt": 3000,
                "max_image_size_mb": 7,
                "max_audio_length_hours": 8.4,
                "max_audio_files_per_prompt": 1,
                "parameter_defaults": {
                    "temperature": 0.3,
                    "top_p": 0.95,
                    "top_k": 64,
                    "candidate_count": 1
                }
            },
            "gemini-2.0-flash": {
                "max_input_tokens": 1048576,
                "max_output_tokens": 8192,
                "max_images_per_prompt": 3000,
                "max_image_size_mb": 7,
                "max_audio_length_hours": 8.4,
                "max_audio_files_per_prompt": 1,
                "parameter_defaults": {
                    "temperature": 0.3,
                    "top_p": 0.95,
                    "top_k": 64,
                    "candidate_count": 1
                }
            }
        }
    
    def get_model_limits(self, model_name: str) -> Dict[str, Any]:
        """Get limits for a specific model."""
        return self.model_limits.get(model_name, self.model_limits.get("gemini-2.5-pro", {}))
    
    def get_parameter_defaults(self, model_name: str) -> Dict[str, Any]:
        """Get parameter defaults for a specific model."""
        model_limits = self.get_model_limits(model_name)
        defaults = model_limits.get("parameter_defaults", {
            "temperature": 0.3,
            "top_p": 0.95,
            "top_k": 64,
            "candidate_count": 1
        })
        
        # Validate parameter ranges
        validated_defaults = {}
        
        # Temperature: 0.0-2.0
        temp = defaults.get("temperature", 0.3)
        if temp < 0.0 or temp > 2.0:
            print(f"Warning: temperature {temp} is outside valid range [0.0-2.0], clamping to {max(0.0, min(2.0, temp))}")
        validated_defaults["temperature"] = max(0.0, min(2.0, temp))
        
        # topP: 0.0-1.0
        top_p = defaults.get("top_p", 0.95)
        if top_p < 0.0 or top_p > 1.0:
            print(f"Warning: top_p {top_p} is outside valid range [0.0-1.0], clamping to {max(0.0, min(1.0, top_p))}")
        validated_defaults["top_p"] = max(0.0, min(1.0, top_p))
        
        # topK: fixed at 64
        validated_defaults["top_k"] = 64
        
        # candidateCount: 1-8
        candidate_count = defaults.get("candidate_count", 1)
        if candidate_count < 1 or candidate_count > 8:
            print(f"Warning: candidate_count {candidate_count} is outside valid range [1-8], clamping to {max(1, min(8, int(candidate_count)))}")
        validated_defaults["candidate_count"] = max(1, min(8, int(candidate_count)))
        
        return validated_defaults


class MeetingProcessor:
    """Main class for processing meeting videos and generating documentation."""
    
    def __init__(self, config: Config):
        self.config = config
        self.target_dir = ""
        self.video_path = ""
        self.audio_path = ""
        self.frames_dir = ""
        self.meeting_md_path = ""
        self.note_path = ""
        self.prompt_path = ""
        self.process_log_path = ""
        
        # Setup logging
        self._setup_logging()
        
    def _setup_logging(self):
        """Setup logging configuration."""
        log_level = logging.DEBUG if self.config.debug else logging.INFO
        
        # Create handlers list
        handlers = [logging.StreamHandler(sys.stdout)]
        
        # Add file handler only if log path exists and directory is created
        if self.process_log_path and self.process_log_path.parent.exists():
            try:
                handlers.append(logging.FileHandler(self.process_log_path))
            except Exception:
                pass  # Fallback to console only
        
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=handlers
        )
        self.logger = logging.getLogger(__name__)
        
        # Suppress HTTP request logs from Google GenAI SDK
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("google.genai").setLevel(logging.WARNING)
    
    def _update_logging_with_file(self):
        """Update logging to include file handler after target directory is created."""
        if self.process_log_path and self.process_log_path.parent.exists():
            try:
                # Remove existing handlers to avoid duplicates
                for handler in self.logger.handlers[:]:
                    if isinstance(handler, logging.FileHandler):
                        self.logger.removeHandler(handler)
                
                # Add file handler
                file_handler = logging.FileHandler(self.process_log_path)
                file_handler.setFormatter(logging.Formatter(
                    '%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                ))
                self.logger.addHandler(file_handler)
                self.logger.info("Logging to file enabled")
            except Exception as e:
                self.logger.warning(f"Failed to add file logging: {e}")
    
    def _log_model_limits(self, model_limits: Dict[str, Any]):
        """Log model limits in a formatted way following Google GenAI standards."""
        self.logger.info(f"Using model limits for {self.config.gemini_model}:")
        
        # Log basic limits with consistent formatting
        limits_info = [
            f"max_input_tokens: {model_limits.get('max_input_tokens', 'N/A')}",
            f"max_output_tokens: {model_limits.get('max_output_tokens', 'N/A')}",
            f"max_images_per_prompt: {model_limits.get('max_images_per_prompt', 'N/A')}",
            f"max_image_size_mb: {model_limits.get('max_image_size_mb', 'N/A')}",
            f"max_audio_length_hours: {model_limits.get('max_audio_length_hours', 'N/A')}",
            f"max_audio_files_per_prompt: {model_limits.get('max_audio_files_per_prompt', 'N/A')}"
        ]
        
        for limit in limits_info:
            self.logger.info(f"  • {limit}")
        
        # Log parameter defaults with same formatting as limits
        parameter_defaults = model_limits.get('parameter_defaults', {})
        if parameter_defaults:
            self.logger.info("  • parameter_defaults:")
            param_info = [
                f"temperature: {parameter_defaults.get('temperature', 'N/A')}",
                f"top_p: {parameter_defaults.get('top_p', 'N/A')}",
                f"top_k: {parameter_defaults.get('top_k', 'N/A')}",
                f"candidate_count: {parameter_defaults.get('candidate_count', 'N/A')}"
            ]
            
            for param in param_info:
                self.logger.info(f"    • {param}")
    
    def _log_parameter_defaults(self, parameter_defaults: Dict[str, Any]):
        """Log parameter defaults in a formatted way following Google GenAI standards."""
        self.logger.info("Using parameter defaults:")
        
        # Log parameters with consistent formatting
        param_info = [
            f"temperature: {parameter_defaults.get('temperature', 'N/A')}",
            f"top_p: {parameter_defaults.get('top_p', 'N/A')}",
            f"top_k: {parameter_defaults.get('top_k', 'N/A')}",
            f"candidate_count: {parameter_defaults.get('candidate_count', 'N/A')}"
        ]
        
        for param in param_info:
            self.logger.info(f"  • {param}")
    
    def run(self, video_path: str, prompt_type: Optional[str] = None, notes: Optional[str] = None):
        """Main execution method."""
        try:
            self.video_path = video_path
            self.logger.info(f"Starting processing of video: {video_path}")
            
            # Step 1: Extract date and time
            recording_datetime = self._extract_datetime()
            
            # Step 2: Create target directory
            self._create_target_directory(recording_datetime)
            
            # Step 3: Convert video
            self._convert_video()
            
            # Step 4: Extract audio
            self._extract_audio()
            
            # Step 5: Extract frames (if needed)
            if prompt_type != "prompt_only_transcript.txt":
                self._extract_frames()
            
            # Step 6: Create meeting.md
            self._create_meeting_md()
            
            # Step 7: Select and copy prompt
            self._select_and_copy_prompt(prompt_type)
            
            # Step 8: Create note.txt
            self._create_note_txt(notes)
            
            # Step 9: Upload to Google Gemini
            self._upload_to_gemini()
            
            # Step 10: Cleanup
            if not self.config.no_cleanup:
                self._cleanup()
            
            self.logger.info("Processing completed successfully!")
            
        except Exception as e:
            self.logger.error(f"Error during processing: {str(e)}")
            # Clean up created files on failure, preserving original video
            self._cleanup_on_failure()
            raise
    
    def _extract_datetime(self) -> datetime:
        """Extract date and time based on PREFERRED_DATE_SOURCE setting."""
        self.logger.info(f"Extracting date and time using source: {self.config.preferred_date_source}")
        
        # Validate preferred_date_source
        valid_sources = ["metadata", "file_mtime", "manual"]
        if self.config.preferred_date_source not in valid_sources:
            self.logger.warning(f"Invalid PREFERRED_DATE_SOURCE '{self.config.preferred_date_source}'. Using 'metadata' as fallback.")
            self.config.preferred_date_source = "metadata"
        
        # Try metadata first if it's the preferred source or if we need to fall back to it
        if self.config.preferred_date_source == "metadata":
            dt = self._extract_datetime_from_metadata()
            if dt:
                return dt
            else:
                self.logger.warning("Metadata extraction failed, falling back to file_mtime")
                self.config.preferred_date_source = "file_mtime"
        
        # Try file modification time if it's the preferred source or as fallback
        if self.config.preferred_date_source == "file_mtime":
            dt = self._extract_datetime_from_file_mtime()
            if dt:
                return dt
            else:
                self.logger.warning("File modification time extraction failed, falling back to manual input")
                self.config.preferred_date_source = "manual"
        
        # Manual input as preferred source or final fallback
        if self.config.preferred_date_source == "manual":
            return self._get_manual_datetime()
        
        # This should never be reached, but just in case
        self.logger.error("All date extraction methods failed")
        if self.config.dry_run:
            dt = datetime.now()
            self.logger.info(f"DRY RUN: Using placeholder time: {dt}")
            return dt
        else:
            raise RuntimeError("Could not extract datetime from any source")
    
    def _extract_datetime_from_metadata(self) -> Optional[datetime]:
        """Extract date and time from video metadata."""
        self.logger.info("Extracting date and time from video metadata...")
        
        try:
            # Try to extract creation date using ffprobe
            cmd = [
                self.config.ffmpeg_path.replace("ffmpeg", "ffprobe"),
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                self.video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                metadata = json.loads(result.stdout)
                creation_time = metadata.get('format', {}).get('tags', {}).get('creation_time')
                if creation_time:
                    # Parse ISO format datetime
                    dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
                    self.logger.info(f"Found creation time from metadata: {dt}")
                    return dt
                else:
                    self.logger.warning("No creation_time found in video metadata")
            else:
                self.logger.warning(f"ffprobe failed with return code: {result.returncode}")
                
        except Exception as e:
            self.logger.warning(f"Could not extract datetime from metadata: {e}")
        
        return None
    
    def _extract_datetime_from_file_mtime(self) -> Optional[datetime]:
        """Extract date and time from file modification time."""
        self.logger.info("Extracting date and time from file modification time...")
        
        try:
            video_path = Path(self.video_path)
            if video_path.exists():
                mtime = video_path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                self.logger.info(f"Using file modification time: {dt}")
                return dt
            else:
                self.logger.warning(f"Video file does not exist: {self.video_path}")
        except Exception as e:
            self.logger.warning(f"Could not use file modification time: {e}")
        
        return None
    
    def _get_manual_datetime(self) -> datetime:
        """Get date and time from manual user input."""
        self.logger.info("Requesting manual date and time input...")
        
        if self.config.dry_run:
            dt = datetime.now()
            self.logger.info(f"DRY RUN: Using placeholder time: {dt}")
            return dt
        
        # Ask user for the recording time
        self.logger.info("Please enter the recording date and time manually")
        custom_date = questionary.text(
            "Enter the recording date (YYYY-MM-DD):"
        ).ask()
        custom_time = questionary.text(
            "Enter the recording time (HH:MM):"
        ).ask()
        dt = datetime.strptime(f"{custom_date} {custom_time}", "%Y-%m-%d %H:%M")
        self.logger.info(f"Using manually entered time: {dt}")
        return dt
    
    def _create_target_directory(self, recording_datetime: datetime):
        """Create target directory with timestamp format or use specified directory."""
        if self.config.target_directory:
            # Use specified directory
            self.target_dir = Path(self.config.target_directory)
            if not self.config.dry_run:
                self.target_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Using specified target directory: {self.target_dir}")
        else:
            # Create timestamp-based directory
            timestamp = recording_datetime.strftime("%Y-%m-%d_%H.%M")
            self.target_dir = Path.cwd() / timestamp
            
            if not self.config.dry_run:
                self.target_dir.mkdir(exist_ok=True)
                self.logger.info(f"Created target directory: {self.target_dir}")
        
        # Update paths
        self.audio_path = self.target_dir / "audio.m4a"
        self.frames_dir = self.target_dir / "frames"
        self.meeting_md_path = self.target_dir / "meeting.md"
        self.note_path = self.target_dir / "note.txt"
        self.prompt_path = self.target_dir / "prompt.txt"
        self.process_log_path = self.target_dir / "process.log"
        
        # Update logging to include file handler now that directory exists
        self._update_logging_with_file()
    
    def _convert_video(self):
        """Convert video using HandBrakeCLI with JSON preset."""
        self.logger.info("Converting video with HandBrakeCLI using JSON preset...")
        
        output_path = self.target_dir / "small.mp4"
        
        # Get preset file path
        preset_file_path = Path(__file__).parent / self.config.handbrake_preset_file
        
        if not preset_file_path.exists():
            raise FileNotFoundError(f"HandBrake preset file not found: {preset_file_path}")
        
        cmd = [
            self.config.handbrake_path,
            "--preset-import-file", str(preset_file_path),
            "-Z", self.config.handbrake_preset_name,
            "-i", str(self.video_path),
            "-o", str(output_path)
        ]
        
        if not self.config.dry_run:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"HandBrakeCLI failed: {result.stderr}")
            
            # Verify that the output file was actually created
            if not output_path.exists():
                raise RuntimeError(f"HandBrakeCLI reported success but output file was not created: {output_path}")
            
            # Check file size to ensure it's not empty
            if output_path.stat().st_size == 0:
                raise RuntimeError(f"HandBrakeCLI created empty output file: {output_path}")
            
            self.logger.info("Video conversion completed")
        else:
            self.logger.info(f"DRY RUN: Would run: {' '.join(cmd)}")
    
    def _extract_audio(self):
        """Extract audio from converted video."""
        self.logger.info("Extracting audio...")
        
        input_path = self.target_dir / "small.mp4"
        cmd = [
            self.config.ffmpeg_path,
            "-i", str(input_path),
            "-vn",
            "-c:a", "copy",
            str(self.audio_path)
        ]
        
        if not self.config.dry_run:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Audio extraction failed: {result.stderr}")
            self.logger.info("Audio extraction completed")
        else:
            self.logger.info(f"DRY RUN: Would run: {' '.join(cmd)}")
    
    def _extract_frames(self):
        """Extract frames from video."""
        self.logger.info("Extracting frames...")
        
        if not self.config.dry_run:
            self.frames_dir.mkdir(exist_ok=True)
        
        input_path = self.target_dir / "small.mp4"
        cmd = [
            self.config.ffmpeg_path,
            "-i", str(input_path),
            "-vf", f"fps=1/{self.config.frame_interval}",
            "-q:v", "2",
            str(self.frames_dir / "frame_%04d.jpg")
        ]
        
        if not self.config.dry_run:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Frame extraction failed: {result.stderr}")
            self.logger.info("Frame extraction completed")
        else:
            self.logger.info(f"DRY RUN: Would run: {' '.join(cmd)}")
    
    def _create_meeting_md(self):
        """Create empty meeting.md file."""
        self.logger.info("Creating meeting.md...")
        
        if not self.config.dry_run:
            self.meeting_md_path.touch()
            self.logger.info("Created meeting.md")
        else:
            self.logger.info("DRY RUN: Would create meeting.md")
    
    def _resolve_prompt_shortcut(self, prompt_input: str) -> str:
        """Resolve prompt shortcuts to actual file names."""
        shortcut_mapping = {
            "only transcript": "prompt_only_transcript.txt",
            "without transcript": "prompt_wo_transcript.txt"
        }
        
        return shortcut_mapping.get(prompt_input.lower(), prompt_input)
    
    def _select_and_copy_prompt(self, prompt_type: Optional[str] = None):
        """Select and copy the appropriate prompt template."""
        self.logger.info("Selecting prompt template...")
        
        # Use provided prompt type, or default from config, or ask user
        if prompt_type:
            prompt_file = self._resolve_prompt_shortcut(prompt_type)
        elif self.config.default_prompt:
            prompt_file = self._resolve_prompt_shortcut(self.config.default_prompt)
        else:
            # Interactive selection
            if not self.config.dry_run:
                prompt_file = questionary.select(
                    "Select prompt template:",
                    choices=[
                        "prompt.txt - Full analysis with transcript and visual content",
                        "prompt_wo_transcript.txt - Analysis without transcript (visual only)",
                        "prompt_only_transcript.txt - Transcript-only analysis (no visual content)",
                        "only transcript - Shortcut for transcript-only",
                        "without transcript - Shortcut for visual-only"
                    ]
                ).ask()
                prompt_file = self._resolve_prompt_shortcut(prompt_file.split(" - ")[0])
            else:
                prompt_file = "prompt.txt"  # Default for dry run
        
        # Copy prompt file to target directory
        source_prompt = Path(__file__).parent / prompt_file
        if source_prompt.exists():
            if not self.config.dry_run:
                shutil.copy2(source_prompt, self.prompt_path)
                self.logger.info(f"Copied prompt template: {prompt_file}")
            else:
                self.logger.info(f"DRY RUN: Would copy prompt template: {prompt_file}")
        else:
            raise FileNotFoundError(f"Prompt template not found: {prompt_file}")
    
    def _create_note_txt(self, notes: Optional[str] = None):
        """Create note.txt with user input."""
        self.logger.info("Creating note.txt...")
        
        if notes:
            note_content = notes
        else:
            if not self.config.dry_run:
                # Ask user for input method
                input_method = questionary.select(
                    "How would you like to input notes?",
                    choices=[
                        "Enter directly in terminal",
                        "Open in default editor"
                    ]
                ).ask()
                
                if input_method == "Enter directly in terminal":
                    note_content = questionary.text("Enter your notes:").ask() or ""
                else:
                    # Create temporary file and open in editor
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
                        tmp.write("# Enter your notes here\n\n")
                        tmp.flush()
                        
                        # Open in default editor
                        editor = os.environ.get('EDITOR', 'nano')
                        subprocess.run([editor, tmp.name])
                        
                        # Read back the content
                        with open(tmp.name, 'r') as f:
                            note_content = f.read()
                        
                        # Clean up temp file
                        os.unlink(tmp.name)
            else:
                note_content = "# DRY RUN - Notes would be entered here\n"
        
        if not self.config.dry_run:
            with open(self.note_path, 'w', encoding='utf-8') as f:
                f.write(note_content)
            self.logger.info("Created note.txt")
        else:
            self.logger.info("DRY RUN: Would create note.txt with user input")
    
    def _retry_with_exponential_backoff(self, func, max_retries=5, base_delay=60):
        """
        Retry a function with exponential backoff for 503 errors.
        
        Args:
            func: Function to retry
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds (will be doubled each retry)
        
        Returns:
            Result of the function if successful
        
        Raises:
            Exception: If all retries fail or if a non-503 error occurs
        """
        import time
        
        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                return func()
            except Exception as e:
                # Check if it's a 503 error - handle different error structures
                is_503_error = False
                
                # Check for status_code attribute
                if hasattr(e, 'status_code') and e.status_code == 503:
                    is_503_error = True
                
                # Check for error message containing 503
                elif hasattr(e, 'message') and '503' in str(e.message):
                    is_503_error = True
                
                # Check for error details containing 503
                elif hasattr(e, 'details') and '503' in str(e.details):
                    is_503_error = True
                
                # Check for error string containing 503 UNAVAILABLE
                elif '503' in str(e) and 'UNAVAILABLE' in str(e):
                    is_503_error = True
                
                if is_503_error:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)  # 1, 2, 4, 8, 16 minutes
                        self.logger.warning(f"503 UNAVAILABLE error (attempt {attempt + 1}/{max_retries + 1}). "
                                          f"Retrying in {delay} seconds...")
                        time.sleep(delay)
                        continue
                    else:
                        self.logger.error(f"All {max_retries + 1} attempts failed with 503 UNAVAILABLE error")
                        raise
                else:
                    # Non-503 error, don't retry
                    self.logger.error(f"Non-retryable error: {e}")
                    raise
        
        # This should never be reached, but just in case
        raise RuntimeError("Unexpected error in retry logic")
    
    def _cleanup_on_failure(self):
        """Clean up created directory and files on failure, preserving original video."""
        self.logger.info("Cleaning up created files due to failure...")
        
        try:
            if self.target_dir.exists():
                import shutil
                shutil.rmtree(self.target_dir)
                self.logger.info(f"Removed target directory: {self.target_dir}")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup target directory: {e}")
    
    def _upload_to_gemini(self):
        """Upload content to Google Gemini and save results."""
        self.logger.info("Uploading to Google Gemini...")
        
        if not self.config.gemini_api_key:
            raise ValueError("Gemini API key not configured")
        
        try:
            # Import the modern Google GenAI SDK
            from google import genai
            from google.genai import types
        except ImportError:
            self.logger.error("Google GenAI SDK not installed. Please install it with: pip install google-genai")
            raise
        
        # Setup client
        client = genai.Client(api_key=self.config.gemini_api_key)
        
        # Read prompt
        if self.config.dry_run:
            prompt_text = "# DRY RUN - Prompt content would be read here"
        else:
            with open(self.prompt_path, 'r', encoding='utf-8') as f:
                prompt_text = f.read()
        
        # Get model limits for the current model
        model_limits = self.config.get_model_limits(self.config.gemini_model)
        self._log_model_limits(model_limits)
        
        # Check input token limit for prompt
        if not self.config.dry_run:
            prompt_tokens = client.models.count_tokens(
                model=self.config.gemini_model,
                contents=prompt_text
            ).total_tokens
            self.logger.info(f"Prompt tokens: {prompt_tokens}")
            
            max_input_tokens = model_limits.get("max_input_tokens", 1048576)
            if prompt_tokens > max_input_tokens:
                self.logger.warning(f"Prompt tokens ({prompt_tokens}) exceed limit ({max_input_tokens})")
        
        # Collect all files to upload
        files_to_upload = []
        
        # Add audio file
        if self.audio_path.exists():
            # Check audio duration limits (not file size)
            try:
                import subprocess
                result = subprocess.run([
                    self.config.ffmpeg_path.replace("ffmpeg", "ffprobe"), 
                    '-v', 'quiet', 
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1', 
                    str(self.audio_path)
                ], capture_output=True, text=True, check=True)
                audio_duration_hours = float(result.stdout.strip()) / 3600
                max_audio_hours = model_limits.get("max_audio_length_hours", 8.4)
                if audio_duration_hours > max_audio_hours:
                    self.logger.warning(f"Audio duration ({audio_duration_hours:.2f} hours) exceeds limit ({max_audio_hours} hours)")
            except Exception as e:
                self.logger.warning(f"Could not check audio duration: {e}")
            files_to_upload.append(('audio', self.audio_path))
        
        # Add frames if they exist
        if self.frames_dir.exists():
            frame_files = list(self.frames_dir.glob("*.jpg"))
            max_images = model_limits.get("max_images_per_prompt", 3000)
            
            if len(frame_files) > max_images:
                self.logger.warning(f"Number of frames ({len(frame_files)}) exceeds limit ({max_images}), limiting to first {max_images}")
                frame_files = frame_files[:max_images]
            
            for frame_file in frame_files:
                # Check image size limit
                image_size_mb = frame_file.stat().st_size / (1024 * 1024)
                if image_size_mb > model_limits.get("max_image_size_mb", 7):
                    self.logger.warning(f"Image file {frame_file.name} size ({image_size_mb:.2f} MB) exceeds limit ({model_limits.get('max_image_size_mb', 7)} MB)")
                files_to_upload.append(('frame', frame_file))
        
        # Add note.txt if it exists and has content
        if self.note_path.exists() and self.note_path.stat().st_size > 0:
            files_to_upload.append(('note', self.note_path))
        
        if not self.config.dry_run:
            # Upload files with live progress tracking
            uploaded_files = {}
            total_files = len(files_to_upload)
            
            if total_files > 0:
                # Print initial status line with timestamp
                import time
                start_time = time.time()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                print(f"{timestamp} - INFO - Uploading files to Gemini: 0/{total_files}", end='', flush=True)
                
                for i, (file_type, file_path) in enumerate(files_to_upload, 1):
                    try:
                        if file_type == 'audio':
                            uploaded_file = client.files.upload(file=str(file_path))
                            uploaded_files['audio'] = uploaded_file
                        elif file_type == 'frame':
                            if 'frames' not in uploaded_files:
                                uploaded_files['frames'] = []
                            uploaded_file = client.files.upload(file=str(file_path))
                            uploaded_files['frames'].append(uploaded_file)
                        elif file_type == 'note':
                            uploaded_file = client.files.upload(file=str(file_path))
                            uploaded_files['note'] = uploaded_file
                        
                        # Update progress line in place with file type indicator
                        file_type_display = file_type.upper()
                        print(f"\r{timestamp} - INFO - Uploading files to Gemini: {i}/{total_files} ({file_type_display})", end='', flush=True)
                        
                        # Small delay to ensure progress is visible even for fast uploads
                        time.sleep(0.1)
                        
                    except Exception as e:
                        self.logger.error(f"Failed to upload {file_path.name}: {e}")
                        if file_type == 'audio':
                            print()  # New line after progress
                            raise  # Audio is critical, fail if it can't be uploaded
                        # Continue with other files for non-critical uploads
                
                # Final status update and new line
                print(f"\r{timestamp} - INFO - Uploading files to Gemini: {total_files}/{total_files} completed")
            
            # Prepare content for Gemini
            content_parts = [prompt_text]
            
            # Add uploaded files to content
            if 'audio' in uploaded_files:
                content_parts.append(uploaded_files['audio'])
            
            if 'frames' in uploaded_files:
                content_parts.extend(uploaded_files['frames'])
            
            if 'note' in uploaded_files:
                content_parts.append(uploaded_files['note'])
            
            # Generate content with Gemini using retry logic
            self.logger.info(f"Generating content with {self.config.gemini_model}...")
            
            # Apply model limits and parameter defaults to generation config
            max_output_tokens = model_limits.get("max_output_tokens", 65535)
            parameter_defaults = self.config.get_parameter_defaults(self.config.gemini_model)
            
            self.logger.info(f"Using max_output_tokens: {max_output_tokens}")
            self._log_parameter_defaults(parameter_defaults)
            
            def generate_content():
                """Function to generate content with Gemini (for retry logic)."""
                return client.models.generate_content(
                    model=self.config.gemini_model,
                    contents=content_parts,
                    config=types.GenerateContentConfig(
                        temperature=parameter_defaults.get("temperature", 0.3),
                        top_p=parameter_defaults.get("top_p", 0.95),
                        top_k=parameter_defaults.get("top_k", 64),
                        candidate_count=parameter_defaults.get("candidate_count", 1),
                        max_output_tokens=max_output_tokens
                    )
                )
            
            # Use retry logic for content generation
            response = self._retry_with_exponential_backoff(generate_content)
            
            if response.text:
                # Save to meeting.md
                with open(self.meeting_md_path, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                
                self.logger.info("Gemini analysis completed and saved to meeting.md")
            else:
                raise RuntimeError("Empty response from Gemini API")
            
            # Cleanup uploaded files
            if uploaded_files:
                self.logger.info("Cleaning up uploaded files...")
                cleanup_count = 0
                total_to_cleanup = 0
                
                try:
                    if 'audio' in uploaded_files:
                        total_to_cleanup += 1
                        client.files.delete(name=uploaded_files['audio'].name)
                        cleanup_count += 1
                    
                    if 'frames' in uploaded_files:
                        total_to_cleanup += len(uploaded_files['frames'])
                        for frame in uploaded_files['frames']:
                            client.files.delete(name=frame.name)
                            cleanup_count += 1
                    
                    if 'note' in uploaded_files:
                        total_to_cleanup += 1
                        client.files.delete(name=uploaded_files['note'].name)
                        cleanup_count += 1
                    
                    self.logger.info(f"Cleaned up {cleanup_count}/{total_to_cleanup} uploaded files")
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup some uploaded files: {e}")
        else:
            self.logger.info("DRY RUN: Would upload files to Gemini and save results")
    
    def _cleanup(self):
        """Clean up temporary files."""
        self.logger.info("Cleaning up temporary files...")
        
        if not self.config.dry_run:
            # Remove frames directory
            if self.frames_dir.exists():
                shutil.rmtree(self.frames_dir)
                self.logger.info("Removed frames directory")
            
            # Note: Original video file is preserved (not removed)
            # This allows for retry attempts and preserves user's original file
        else:
            self.logger.info("DRY RUN: Would clean up temporary files")


def load_config() -> Config:
    """Load configuration from environment variables."""
    # Load environment variables from .env file
    load_dotenv()
    
    # Create config with environment variables and defaults
    config = Config()
    
    return config





def setup_dependencies():
    """Install Python dependencies."""
    print("Installing Python dependencies...")
    
    requirements = [
        "requests",
        "questionary", 
        "ffmpeg-python",
        "google-genai",
        "python-dotenv>=1.0.0"
    ]
    
    for req in requirements:
        subprocess.run([sys.executable, "-m", "pip", "install", req])
    
    print("Dependencies installation completed!")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Meeting Video Processor and Documentation Tool")
    parser.add_argument("--video", help="Path to video file")
    parser.add_argument("--prompt", 
                       help="Prompt template to use. Options: prompt.txt, prompt_wo_transcript.txt, prompt_only_transcript.txt, 'only transcript', 'without transcript'")
    parser.add_argument("--notes", help="Notes content (alternative to interactive input)")
    parser.add_argument("-d", "--directory", help="Specify target directory (instead of auto-creating timestamp-based directory)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--dry-run", action="store_true", help="Simulate processing without making changes")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't clean up temporary files")
    parser.add_argument("--setup", action="store_true", help="Install Python dependencies")
    
    args = parser.parse_args()
    
    if args.setup:
        setup_dependencies()
        return
    
    # Check if required dependencies are available
    if questionary is None or requests is None:
        print("Error: Required dependencies not found!")
        print("Please run: python3 meeting_processor.py --setup")
        print("Or use the wrapper script: ./meeting.sh --setup")
        return 1
    
    # Load configuration
    config = load_config()
    config.debug = args.debug
    config.dry_run = args.dry_run
    config.no_cleanup = args.no_cleanup
    config.target_directory = args.directory
    
    # Get video path
    if args.video:
        video_path = args.video
    else:
        video_path = questionary.path("Enter path to video file:").ask()
    
    if not video_path or not Path(video_path).exists():
        print("Error: Video file not found!")
        return 1
    
    # Create processor and run
    processor = MeetingProcessor(config)
    processor.run(video_path, args.prompt, args.notes)
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 