# Resume AI Parser

A powerful resume parsing application combining Streamlit’s user-friendly interface and FastAPI’s robust API to extract structured data from PDF and DOCX resumes. Leveraging OpenRouter’s AI models, it processes resumes into a predefined JSON schema, with secure file handling, logging, and configuration management.

## Table of Contents
- [Features](#features)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Endpoints](#api-endpoints)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

## Features
- **Resume Parsing**: Extracts structured data (e.g., name, education, experience, skills) from PDF and DOCX resumes using OpenRouter’s AI models.
- **Streamlit UI**: Intuitive web interface for uploading and parsing resumes with real-time feedback.
- **FastAPI Backend**: Secure API endpoint for programmatic resume parsing with API key authentication.
- **File Validation**: Ensures only valid PDF/DOCX files are processed, with size and MIME type checks.
- **Logging**: Comprehensive logging for debugging and monitoring using a custom logger.
- **Configurable**: Supports customizable AI models and settings via `config.yml`.

## Project Structure
```
Resume-AI-Parser/
├── utils/
│   ├── logging.py           # Custom logging utility
│   ├── file_utils.py       # File handling and validation
├── api.py                  # FastAPI endpoint for resume parsing
├── ai_processor.py         # AI processing logic for resume data
├── config.py               # Configuration loader
├── config.yml              # Configuration file (models, settings)
├── main.py                 # Streamlit application entry point
├── ui_components.py        # Streamlit UI components
├── requirements.txt        # Python dependencies
├── .gitignore             # Git ignore file
├── .env                   # Environment variables (not tracked)
├── uploads/               # Temporary upload folder (not tracked)
└── README.md              # Project documentation
```

## Installation
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/NabiBukhsh-AI/Resume-AI-Parser.git
   cd Resume-AI-Parser
   ```

2. **Set Up a Virtual Environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   Ensure Python 3.8+ is installed. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```
   Note: On Windows, `python-magic-bin` is used. On Linux/macOS, install `python-magic` instead if needed.

4. **Set Up Environment Variables**:
   Create a `.env` file in the project root with your OpenRouter API key:
   ```bash
   OPENROUTER_API_KEY=your_openrouter_api_key
   SECURE_API_KEY=your_optional_api_key
   ```
   Obtain your OpenRouter API key from [OpenRouter](https://openrouter.ai). The `SECURE_API_KEY` is optional for securing the API and Streamlit UI.

## Configuration
- **config.yml**: Defines AI models, upload settings, and resume schema. The default configuration includes:
  ```yaml
  models:
    - "google/gemini-flash-1.5-8b"
    - "google/gemini-2.0-flash-exp:free"
    - "google/gemini-exp-1121:free"
  general:
    max_file_size: 16777216
    upload_folder: "uploads"
  ```
  Update `models` if you prefer different OpenRouter models.

- **Uploads Folder**: The `uploads/` folder is created automatically for temporary file storage and is excluded from Git via `.gitignore`.

## Usage
1. **Run the Streamlit Application**:
   ```bash
   streamlit run main.py
   ```
   Access the UI at `http://localhost:8501`. Upload a PDF or DOCX resume, enter an API key (if configured), and click "Parse Resume" to view structured JSON output.

2. **Run the FastAPI Application**:
   ```bash
   uvicorn api:app --host 0.0.0.0 --port 4000
   ```
   Access the API at `http://localhost:4000` or interactive docs at `http://localhost:4000/docs`.

3. **API Usage**:
   - Send a POST request to `/parse` with a resume file and optional API key:
     ```bash
     curl -X POST "http://localhost:4000/parse" -H "x-api-key: your_secure_api_key" -F "file=@path/to/resume.pdf"
     ```
   - Returns structured JSON data based on the resume schema.

## API Endpoints
- **POST /parse**: Parse a PDF or DOCX resume and return structured JSON data. Requires `x-api-key` header if `SECURE_API_KEY` is set.

## Contributing
Contributions are welcome! To contribute:
1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Commit your changes (`git commit -m 'Add your feature'`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a Pull Request.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for more details (create this file if you wish to formalize contribution guidelines).

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contact
Created by [NabiBukhsh-AI](https://github.com/NabiBukhsh-AI). For feedback or suggestions, open an issue or contact me via GitHub.