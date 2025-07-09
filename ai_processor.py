import json
import requests
from typing import Dict, Any
from utils.logging import get_logger
from config import OPENROUTER_API_KEY, CONFIG

logger = get_logger(__name__)

def process_resume_with_ai(text: str) -> Dict[str, Any]:
    """Send extracted resume text to AI for structured processing.

    Args:
        text (str): Extracted resume text.

    Returns:
        Dict[str, Any]: Parsed resume data in JSON format.

    Raises:
        ValueError: If AI request or response parsing fails.
    """
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not set")
        raise ValueError("API key not configured")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    prompt = f"""Please analyze this resume and extract information into a structured format.
    Here's the resume text:
    {text}

    You are given a resume text. Extract the information in **this exact JSON structure**:
        {json.dumps(CONFIG['resume_schema'], indent=2)}

    Only include fields if you actually have the data. 
    Calculate the total years of experience from the given resume by combining all the work experience accordingly to get the total. 
    Generate a summary from the overall text, if summary is not provided in the resume.
    By analyzing all the work experience, fill the proficiency (Basic, Intermediate, Advanced, Expert).
    Return valid JSON.

    Please provide the information in a structured JSON format following the schema provided.
    Only include fields where information is available in the resume.
    Make sure the output is valid JSON."""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "models": CONFIG["models"][0:2],
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "schema": CONFIG["resume_schema"],
                },
            },
        )
        response.raise_for_status()
        logger.info(f"AI request successful, status code: {response.status_code}")
    except requests.RequestException as e:
        logger.error(f"AI request failed: {str(e)}")
        raise ValueError(f"AI processing failed: {str(e)}")

    try:
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        start_idx = content.find("{")
        end_idx = content.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            json_str = content[start_idx:end_idx]
            parsed_data = json.loads(json_str)
        else:
            parsed_data = json.loads(content)
        logger.info("Successfully parsed AI response")
        return parsed_data
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse AI response: {str(e)}")
        raise ValueError(f"Invalid AI response format: {str(e)}")