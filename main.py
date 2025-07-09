import streamlit as st
from ui_components import render_resume_parser
from utils.logging import get_logger

logger = get_logger(__name__)

def main():
    """Main entry point for the Streamlit resume parser application."""
    logger.info("Starting Streamlit application")
    st.set_page_config(page_title="Resume Parser", page_icon="📄", layout="wide")
    render_resume_parser()

if __name__ == "__main__":
    main()