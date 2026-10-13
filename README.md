# College Knowledgebase Chatbot (Flask + MongoDB Atlas + Gemini model)

## Overview
Single Flask app which provides:
- Registration/login for multiple institutes (users supply institute_id at registration).
- Student role: can chat with bot and download institute files.
- Teacher role: can upload/delete files into institute-specific knowledgebase (sections).
- Upload automatically extracts text from PDF/DOCX/XLSX/CSV and stores content into metadata for retrieval.
- Chat endpoint queries institute KB and passes snippets to the generative model (Gemini) to answer.

## Setup

1. Clone project and create a Python environment:
```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
