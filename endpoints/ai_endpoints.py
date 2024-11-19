from fastapi import APIRouter, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import os
import logging

from pydub import AudioSegment
from llm_helpers import transcribe_audio_chunk

router = APIRouter(
    prefix="/ai",
    tags=["AI Services"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)

@router.post(
    "/transcribe",
    summary="Transcribe audio to text",
    description=(
        "Transcribes uploaded audio files to text using OpenAI's Whisper model. "
        "Supported formats: FLAC, M4A, MP3, MP4, MPEG, MPGA, OGA, OGG, WAV, WEBM."
    ),
    response_model=dict,
)
async def transcribe_audio(file: UploadFile):
    # Mapping of MIME types to file extensions
    supported_formats = {
        "audio/flac": ".flac",
        "audio/m4a": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".mp4",
        "audio/ogg": ".ogg",
        "audio/ogg;codecs=opus": ".ogg",
        "application/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/webm": ".webm",
        "audio/webm;codecs=opus": ".webm",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }

    logger.debug(f"Received file: {file.filename} with content type: {file.content_type}")

    # Add debug logging for file size
    content = await file.read()
    logger.debug(f"Received file size: {len(content)} bytes")
    
    if len(content) < 100:  # Arbitrary small size check
        raise HTTPException(
            status_code=400,
            detail="File appears to be too small to be valid audio",
        )

    if file.content_type not in supported_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format. Content-Type must be one of: {', '.join(supported_formats.keys())}",
        )

    # Reset file pointer after reading
    await file.seek(0)

    # Get the correct file extension based on content type
    file_extension = supported_formats[file.content_type]

    # Create temporary file with the correct extension
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension, mode='wb') as temp_file:
        try:
            # Write uploaded file to temporary file
            content = await file.read()
            if not content:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded file is empty.",
                )
            temp_file.write(content)
            temp_file.flush()

            logger.debug(f"Temporary file created at: {temp_file.name}")

            # Enhanced audio validation with format detection
            try:
                audio = AudioSegment.from_file(temp_file.name)
                format_info = f"Channels: {audio.channels}, Frame rate: {audio.frame_rate}, Duration: {len(audio)/1000}s"
                logger.debug(f"Audio file validation successful. {format_info}")
            except Exception as e:
                logger.error(f"Audio validation failed: {str(e)}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid audio file. Unable to process the uploaded file.",
                )

            # Transcribe the audio using llm_helpers
            transcript = transcribe_audio_chunk(temp_file.name)

            if transcript is None:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transcribe audio",
                )

            return {"text": transcript}

        except HTTPException as he:
            logger.error(f"HTTPException: {he.detail}")
            raise he
        except Exception as e:
            logger.error(f"Error processing audio file: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error processing audio file: {str(e)}",
            )
        finally:
            # Clean up temporary file
            try:
                # os.unlink(temp_file.name)
                logger.debug(f"Temporary file {temp_file.name} deleted.")
            except Exception as e:
                logger.error(f"Error cleaning up temporary file: {str(e)}") 