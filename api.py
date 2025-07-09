import os
from fastapi import FastAPI, File, UploadFile, HTTPException, Header
from fastapi.responses import JSONResponse
from config import UPLOAD_FOLDER, SECURE_API_KEY, MAX_FILE_SIZE
from file_utils import allowed_file, extract_markdown, save_and_validate_file
from ai_processor import process_resume_with_ai

app = FastAPI()

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.post("/parse")
async def parse_resume(
    file: UploadFile = File(...),
    x_api_key: str = Header(None)
):
    """Endpoint to parse and process resumes."""
    if SECURE_API_KEY and (x_api_key is None or x_api_key != SECURE_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF and DOCX files are allowed.")

    file_content = await file.read()
    file_size = len(file_content)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File size exceeds maximum limit of {MAX_FILE_SIZE} bytes")

    await file.seek(0)

    file_path = None
    try:
        file_path = await save_and_validate_file(file, file.filename, file_size)

        text = extract_markdown(file_path)

        result = process_resume_with_ai(text)

        return JSONResponse(content=result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)