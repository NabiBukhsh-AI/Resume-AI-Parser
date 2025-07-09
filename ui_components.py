import streamlit as st
from file_utils import allowed_file, extract_markdown, save_and_validate_file_sync
from ai_processor import process_resume_with_ai
from utils.logging import get_logger
from config import SECURE_API_KEY, MAX_FILE_SIZE
import os

logger = get_logger(__name__)


def render_resume_parser() -> None:
    """Render the Streamlit UI for resume parsing."""
    st.title("Resume Parser")
    st.markdown("Upload a PDF or DOCX resume to extract structured information.")

    api_key = st.text_input("API Key (if required)", type="password")
    uploaded_file = st.file_uploader("Choose a resume file", type=["pdf", "docx"])

    if st.button("Parse Resume"):
        if SECURE_API_KEY and (not api_key or api_key != SECURE_API_KEY):
            st.error("Invalid or missing API key")
            logger.warning("Unauthorized access attempt")
            return

        if not uploaded_file:
            st.error("Please upload a file")
            logger.warning("No file uploaded")
            return

        if not allowed_file(uploaded_file.name):
            st.error("Invalid file type. Only PDF and DOCX files are allowed.")
            logger.error(f"Invalid file type: {uploaded_file.name}")
            return

        content = uploaded_file.read()
        file_size = len(content)

        if file_size > MAX_FILE_SIZE:
            st.error(f"File size exceeds maximum limit of {MAX_FILE_SIZE} bytes")
            logger.error(f"File size too large: {file_size} bytes")
            return

        with st.spinner("Processing resume..."):
            file_path = None
            try:
                file_path = save_and_validate_file_sync(content, uploaded_file.name)

                text = extract_markdown(file_path)

                result = process_resume_with_ai(text)

                st.success("Resume processed successfully!")
                st.subheader("Parsed Resume Data")
                st.json(result)
                logger.info(f"Successfully processed resume: {uploaded_file.name}")
            except Exception as e:
                st.error(f"Error processing resume: {str(e)}")
                logger.error(f"Error processing {uploaded_file.name}: {str(e)}")
            finally:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Cleaned up file: {file_path}")
