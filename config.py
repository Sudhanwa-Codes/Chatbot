import os

# Load settings from env variables
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://<user>:<pass>@cluster0.mongodb.net/?retryWrites=true&w=majority")
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change_this_in_production")
GENAI_API_KEY = os.environ.get("GENAI_API_KEY")  # set to your API key (do NOT put key in code)
GENAI_USE_API_KEY_PARAM = True  # if using API key param (True) else use Bearer tokens (False)
# If using Google Generative API, set REGION/ENDPOINT override if needed.
GENAI_ENDPOINT = os.environ.get("GENAI_ENDPOINT", "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent")

