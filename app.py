import os
import io
import traceback
from datetime import datetime, timedelta
from typing import List
import time 
import re # Added for regex operations

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from pymongo import MongoClient
import gridfs
from bson import ObjectId

import PyPDF2
import docx
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv("..env") 

# local config import
import config as cfg

app = Flask(__name__)
app.secret_key = cfg.SECRET_KEY

# --- MongoDB / GridFS setup ---
client = MongoClient(cfg.MONGO_URI)
db = client["college_kb"]
users_col = db["users"]            
kb_col = db["knowledgebase"]       
chats_col = db["chats"]            # Stores chat sessions {user_id, institute_id, created_at, messages: [{role, text, attachments: []}]}
fs = gridfs.GridFS(db)

# Allowed uploads
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "xlsx", "xls", "csv", "txt"}

# --- Helpers ---
def allowed_file(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXTENSIONS

def extract_text_from_pdf(stream) -> str:
    try:
        reader = PyPDF2.PdfReader(stream)
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return "\n".join(texts)
    except Exception:
        return ""

def extract_text_from_docx(stream) -> str:
    try:
        doc = docx.Document(stream)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception:
        return ""

def extract_text_from_excel(stream, filename) -> str:
    try:
        # read excel into DataFrame then to string
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in ("csv",):
            stream.seek(0)
            df = pd.read_csv(stream)
            return df.to_csv(index=False)
        else:
            # xlsx/xls
            stream.seek(0)
            df_dict = pd.read_excel(stream, sheet_name=None)  # dict of dfs
            parts = []
            for sheet, df in df_dict.items():
                parts.append(f"--- Sheet: {sheet} ---")
                parts.append(df.to_csv(index=False))
            return "\n".join(parts)
    except Exception:
        return ""

def extract_text(file_stream, filename):
    filename = filename.lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    # Ensure stream reset
    file_stream.seek(0)
    if ext == "pdf":
        return extract_text_from_pdf(file_stream)
    elif ext in ("docx", "doc"):
        return extract_text_from_docx(file_stream)
    elif ext in ("xlsx", "xls", "csv", "txt"):
        return extract_text_from_excel(file_stream, filename)
    else:
        return ""

# --- Authentication helpers ---
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user = users_col.find_one({"_id": ObjectId(uid)})
    return user

def login_user(user_doc):
    session["user_id"] = str(user_doc["_id"])
    session["role"] = user_doc["role"]
    session["institute_id"] = user_doc.get("institute_id")
    session.permanent = True

def logout_user():
    session.pop("user_id", None)
    session.pop("role", None)
    session.pop("institute_id", None)


# --- Chat History/Cleanup Functions ---
def cleanup_old_chats():
    """Deletes chat sessions older than 24 hours."""
    LAST_CLEANUP_KEY = "last_chat_cleanup"
    
    # Check if cleanup was run recently (e.g., in the last hour)
    last_cleanup = session.get(LAST_CLEANUP_KEY, 0)
    if time.time() - last_cleanup < 3600: # 3600 seconds = 1 hour
        return 

    # Use 24 hours ago, calculated relative to UTC now
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    result = chats_col.delete_many({"created_at": {"$lt": cutoff_time}})
    session[LAST_CLEANUP_KEY] = time.time()

def get_chat_history_sessions(user_id):
    """Retrieves all chat sessions for the current day (last 24 hours)."""
    # Use cutoff time logic for retrieval consistency with cleanup
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    
    sessions = list(chats_col.find({
        "user_id": user_id,
        "created_at": {"$gte": cutoff_time}
    }).sort("created_at", -1))
    
    return sessions

def create_new_chat_session(user_id, initial_message):
    user = current_user() 
    if not user: return None

    session_doc = {
        "user_id": user_id,
        "institute_id": user.get("institute_id"), 
        "created_at": datetime.utcnow(),
        "messages": [initial_message]
    }
    return chats_col.insert_one(session_doc).inserted_id

def append_to_chat_session(session_id, user_message, bot_message):
    # Ensure messages are dictionaries before pushing
    if not isinstance(user_message, dict): user_message = {"role": "user", "text": str(user_message)}
    if not isinstance(bot_message, dict): bot_message = {"role": "bot", "text": str(bot_message)}
    
    chats_col.update_one(
        {"_id": ObjectId(session_id)},
        {"$push": {"messages": {"$each": [user_message, bot_message]}}}
    )

# --- Routes ---
@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    
    if user.get("role") == "student":
        cleanup_old_chats()
        user_id = str(user["_id"])
        chat_sessions = get_chat_history_sessions(user_id)
        return render_template("home.html", user=user, chat_sessions=chat_sessions)

    return render_template("home.html", user=user) 

# Registration, Login, Logout, Dashboards, Upload, Manage, Delete, Download routes remain the same
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").strip().lower()
        password = request.form.get("password")
        role = request.form.get("role")
        institute_id = request.form.get("institute_id").strip()
        branch = request.form.get("branch", "").strip()
        year = request.form.get("year", "").strip()

        if role not in ("student", "teacher"):
            flash("Invalid role selected.", "danger")
            return redirect(url_for("register"))

        if users_col.find_one({"username": username, "institute_id": institute_id}):
            flash("User already exists for this institute.", "warning")
            return redirect(url_for("register"))

        pw_hash = generate_password_hash(password)
        user_doc = {
            "username": username,
            "password_hash": pw_hash,
            "role": role,
            "institute_id": institute_id,
            "branch": branch,
            "year": year,
            "created_at": datetime.utcnow()
        }
        res = users_col.insert_one(user_doc)
        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username").strip().lower()
        password = request.form.get("password")
        institute_id = request.form.get("institute_id").strip()
        user = users_col.find_one({"username": username, "institute_id": institute_id})
        if not user:
            flash("Invalid credentials or institute.", "danger")
            return redirect(url_for("login"))
        if not check_password_hash(user["password_hash"], password):
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))

        login_user(user)
        flash("Logged in.", "success")
        if user["role"] == "teacher":
            return redirect(url_for("teacher_dashboard"))
        return redirect(url_for("student_dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("index"))

# Student dashboard
@app.route("/student")
def student_dashboard():
    user = current_user()
    if not user or user["role"] != "student":
        flash("Unauthorized", "danger")
        return redirect(url_for("login"))
    return render_template("student_dashboard.html", user=user)

# Teacher dashboard
@app.route("/teacher")
def teacher_dashboard():
    user = current_user()
    if not user or user["role"] != "teacher":
        flash("Unauthorized", "danger")
        return redirect(url_for("login"))
    return render_template("teacher_dashboard.html", user=user)

# Upload file (teacher only)
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    user = current_user()
    if not user or user["role"] != "teacher":
        flash("Unauthorized", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("file")
        title = request.form.get("title", "").strip()
        section = request.form.get("section")
        branch = request.form.get("branch", "")
        year = request.form.get("year", "")
        subject = request.form.get("subject", "").strip()

        if not file or file.filename == "":
            flash("No file selected.", "warning")
            return redirect(url_for("upload_file"))

        if not allowed_file(file.filename):
            flash("File type not allowed.", "warning")
            return redirect(url_for("upload_file"))

        filename = secure_filename(file.filename)
        file.stream.seek(0)
        content_bytes = file.read()
        file_stream = io.BytesIO(content_bytes)

        # extract text
        extracted = extract_text(io.BytesIO(content_bytes), filename)

        # save file to GridFS
        file_stream.seek(0)
        grid_id = fs.put(file_stream, filename=filename, contentType=file.content_type, uploaded_by=str(user["_id"]), institute_id=user["institute_id"], upload_date=datetime.utcnow())

        meta = {
            "title": title or filename,
            "filename": filename,
            "section": section,
            "branch": branch,
            "year": year,
            "subject": subject,
            "institute_id": user["institute_id"],
            "uploaded_by": str(user["_id"]),
            "upload_date": datetime.utcnow(),
            "file_id": grid_id,
            "content": extracted[:200000]  # store bounded text (avoid huge docs)
        }
        kb_col.insert_one(meta)
        flash("File uploaded and processed.", "success")
        return redirect(url_for("teacher_dashboard"))

    return render_template("upload_file.html", user=user)

# Manage files (teacher)
@app.route("/manage_files")
def manage_files():
    user = current_user()
    if not user or user["role"] != "teacher":
        flash("Unauthorized", "danger")
        return redirect(url_for("login"))

    institute_id = user["institute_id"]
    docs = list(kb_col.find({"institute_id": institute_id}).sort("upload_date", -1))
    return render_template("manage_file.html", user=user, docs=docs)

# Delete file (teacher)
@app.route("/delete_file/<file_id>", methods=["POST"])
def delete_file(file_id):
    user = current_user()
    if not user or user["role"] != "teacher":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    try:
        oid = ObjectId(file_id)
    except:
        return jsonify({"status": "error", "message": "Invalid file ID"}), 400

    meta = kb_col.find_one({"_id": oid})
    if not meta:
        return jsonify({"status": "error", "message": "File not found"}), 404
    
    if meta["institute_id"] != user["institute_id"]:
        return jsonify({"status": "error", "message": "Not allowed to delete this file"}), 403

    try:
        # 1. Remove from GridFS
        fs.delete(meta["file_id"])
        # 2. Remove metadata from knowledgebase collection
        kb_col.delete_one({"_id": meta["_id"]})
        return jsonify({"status": "ok", "message": "File successfully deleted."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Server error during deletion: {str(e)}"}), 500

# NEW: Delete Chat Session (Student)
@app.route("/delete_chat/<session_id>", methods=["POST"])
def delete_chat(session_id):
    user = current_user()
    if not user or user["role"] != "student":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    try:
        oid = ObjectId(session_id)
    except:
        return jsonify({"status": "error", "message": "Invalid session ID"}), 400
        
    result = chats_col.delete_one({
        "_id": oid,
        "user_id": str(user["_id"])
    })

    if result.deleted_count == 1:
        return jsonify({"status": "ok", "message": "Chat session deleted."})
    else:
        return jsonify({"status": "error", "message": "Chat session not found or access denied."}), 404


# Download file
@app.route("/download/<file_id>")
def download_file(file_id):
    user = current_user()
    if not user:
        flash("Login required", "danger")
        return redirect(url_for("login"))

    meta = kb_col.find_one({"_id": ObjectId(file_id)})
    if not meta:
        flash("File not found", "danger")
        return redirect(url_for("index"))

    # enforce institute restriction
    if meta["institute_id"] != user["institute_id"]:
        flash("Not allowed to access this file", "danger")
        return redirect(url_for("index"))

    grid_id = meta["file_id"]
    grid_out = fs.get(grid_id)
    stream = io.BytesIO(grid_out.read())
    stream.seek(0)
    return send_file(stream, as_attachment=True, download_name=meta["filename"], mimetype=grid_out.contentType or "application/octet-stream")

# --- NEW: API to get a single chat session by ID ---
@app.route("/chat/session/<session_id>")
def get_chat_session(session_id):
    user = current_user()
    if not user or user["role"] != "student":
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        oid = ObjectId(session_id)
    except:
        return jsonify({"error": "Invalid session ID"}), 400
        
    session_doc = chats_col.find_one({
        "_id": oid,
        "user_id": str(user["_id"])
    })
    
    if not session_doc:
        return jsonify({"error": "Session not found"}), 404
        
    session_doc["_id"] = str(session_doc["_id"])
    
    # FIX: Ensure attachment file_ids are converted to strings for proper serialization
    # and are included in the message list returned to the frontend.
    messages_output = []
    for msg in session_doc["messages"]:
        attachments_list = []
        for att in msg.get("attachments", []):
            if isinstance(att.get("file_id"), ObjectId):
                att["file_id"] = str(att["file_id"])
            attachments_list.append(att)

        messages_output.append({
            "role": msg["role"],
            "text": msg["text"],
            "attachments": attachments_list
        })
    
    session_doc["messages"] = messages_output
    
    del session_doc["created_at"]
    
    return jsonify(session_doc)
# --- END NEW API ---

# --- Chat / Retrieval endpoint ---
def kb_search_for_institute(institute_id: str, query: str, branch: str = None, year: str = None, top_k=5) -> List[dict]:
    """
    Basic keyword search across title, filename, subject, section and content.
    Returns top_k KB entries as dicts.
    """
    # build regex-based search for simple relevancy
    q = re.escape(query)
    regex = {"$regex": q, "$options": "i"}
    base_filter = {"institute_id": institute_id}
    # Optionally restrict to branch/year if provided
    if branch:
        base_filter["branch"] = branch
    if year:
        base_filter["year"] = year

    # Search across multiple fields
    cond = {
        "$or": [
            {"title": regex},
            {"filename": regex},
            {"subject": regex},
            {"section": regex},
            {"content": regex},
        ]
    }
    results = list(kb_col.find({**base_filter, **cond}).limit(top_k))
    # If nothing found, broaden search by splitting query words
    if not results:
        parts = [p for p in query.split() if len(p) > 2]
        or_arr = []
        for part in parts[:5]:
            or_arr.extend([
                {"title": {"$regex": re.escape(part), "$options": "i"}},
                {"filename": {"$regex": re.escape(part), "$options": "i"}},
                {"subject": {"$regex": re.escape(part), "$options": "i"}},
                {"content": {"$regex": re.escape(part), "$options": "i"}},
            ])
        if or_arr:
            results = list(kb_col.find({**base_filter, "$or": or_arr}).limit(top_k))
    return results

def call_genai_model(prompt: str, api_key: str):
    """
    Calls the generative model endpoint. Adjusted for Gemini API v1/generateContent.
    """
    endpoint = cfg.GENAI_ENDPOINT
    headers = {
        "Content-Type": "application/json"
    }

    # Use the generationConfig block for parameters
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0, # Keep temperature low for factual retrieval
            "maxOutputTokens": 12000 # Increased from 800 to prevent MAX_TOKENS error
        }
    }

    if cfg.GENAI_USE_API_KEY_PARAM:
        # Use key as query param
        params = {"key": api_key}
        resp = requests.post(endpoint, json=body, headers=headers, params=params, timeout=30)
    else:
        # Use key as Bearer token
        headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.post(endpoint, json=body, headers=headers, timeout=30)

    if resp.status_code != 200:
        return f"(model error: {resp.status_code}) {resp.text}"
    try:
        data = resp.json()
        
        # Correctly extract text from v1/generateContent response
        if "candidates" in data and len(data["candidates"]) > 0:
            candidate = data["candidates"][0]
            if "content" in candidate:
                content = candidate["content"]
                parts = content.get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"]
        
        return str(data)
    except Exception:
        return resp.text

@app.route("/chat", methods=["POST"])
def chat():
    user = current_user()
    if not user:
        return jsonify({"error": "login_required"}), 401
    
    user_id = str(user["_id"])
    data = request.get_json()
    user_msg = (data.get("message") or "").strip()
    session_id = data.get("session_id") # Session ID passed from front-end
    branch = user.get("branch") or None
    year = user.get("year") or None

    if not user_msg:
        return jsonify({"reply": "Please send a message."})

    # 1. Handle Greetings Interactively
    lower_msg = user_msg.lower()
    greetings = ["hello", "hi", "hey", "good morning", "good evening", "greetings"]
    
    if any(g in lower_msg for g in greetings) and len(lower_msg.split()) < 4:
        username = user.get("username", "there")
        reply_text = f"Hello, {username}! How can I assist you with your institute's information today?"
        
        user_message = {"role": "user", "text": user_msg}
        bot_message = {"role": "bot", "text": reply_text}
        
        if not session_id:
            session_id = create_new_chat_session(user_id, user_message)
        
        # Append logic is designed to be safe even if session_id was just created
        append_to_chat_session(session_id, user_message, bot_message)
            
        return jsonify({"reply": reply_text, "session_id": str(session_id)})


    # 2. Proceed with KB Search and Generative Model Call
    
    # Search institute KB for relevant docs
    institute_id = user["institute_id"]
    found = kb_search_for_institute(institute_id, user_msg, branch, year, top_k=5)
    
    context_parts = []
    attachments = []
    
    # Create a structured attachments list with file_id for template rendering
    for doc in found:
        snippet = (doc.get("content") or "")[:2000]
        # Only include context if the snippet isn't empty (avoids garbage data in prompt)
        if snippet.strip():
            # FIX: Ensure all relevant sections are mentioned in the prompt context
            context_parts.append(f"--- File: {doc.get('title')} (section: {doc.get('section')}, subject: {doc.get('subject')}) ---\n{snippet}\n")
            
            attachments.append({
                "title": doc.get("title"),
                "file_id": str(doc.get("_id")),
                "filename": doc.get("filename")
            })

    # 3. Build AGGRESSIVE Prompt (RAG FIX)
    # MODIFICATION: Emphasize that ALL sections are part of the knowledge base.
    available_sections = ["notice", "result", "notes", "event_updated", "syllabuses"]
    
    prompt = (
        "You are an educational assistant for an institute. Your primary goal is to provide accurate and helpful information "
        "by extracting details ONLY from the provided Knowledgebase documents. Be direct and concise. "
        f"The Knowledgebase includes documents categorized as: {', '.join(available_sections)}. ALL are valid sources. "
        "CRITICAL INSTRUCTION: If the user asks about schedules, exams, or dates (like IAT, results, events), search aggressively "
        "within the documents and state the exact dates/times/names you find, even if the information seems incomplete. Do NOT refuse "
        "to answer if a date range is present. Always cite the file title(s) using the file reference format below. "
        "If you mention a downloadable file, include the exact file title in your response so it can be linked. "
        "If NO relevant information is found, state that politely, and offer general syllabus knowledge. "
        "Student/teacher institute id: {}\n\n".format(institute_id)
    )
    if context_parts:
        prompt += "Knowledgebase documents (only from this institute):\n\n" + "\n".join(context_parts) + "\n\n"
        
        # FIX: Explicit instruction for generic requests like "give me all documents" or "what notices do you have"
        if "all the documents" in lower_msg or "all documents" in lower_msg or "what documents" in lower_msg or "what notices" in lower_msg:
             prompt += (
                "User question: {}. The user wants a list. Extract the titles of ALL unique attached documents that match the query and list them by name (e.g., 'IAT NOTICE.pdf', 'Holiday Notice.pdf'). "
                "If the user asks specifically for notices, only list documents with a 'notice' section. Do not summarize the content, just list the titles.\n\nAnswer succinctly:".format(user_msg)
            )
        else:
            # Regular specific question
            prompt += f"User question: {user_msg}\n\nAnswer succinctly. If relevant files are attached (File Titles: {', '.join([a['title'] for a in attachments])}), mention them naturally in the text, DO NOT list them separately.\n"
    else:
        prompt += "No relevant institute documents were found. You are allowed to answer only educational questions related to the student's syllabus in a general manner. If the question is outside the syllabus or requires external institute data, say you have no info. User question: {}\n".format(user_msg)

    # 4. Call Model
    api_key = os.environ.get("GENAI_API_KEY")
    if not api_key:
        reply_text = "Server misconfigured: generative model API key not set. Contact admin."
    else:
        try:
            model_output = call_genai_model(prompt, api_key)
            reply_text = model_output
        except Exception as e:
            traceback.print_exc()
            reply_text = f"(model error: {str(e)})"

    # 5. Save Chat History
    user_message = {"role": "user", "text": user_msg}
    bot_message = {"role": "bot", "text": reply_text, "attachments": attachments}

    if not session_id:
        session_id = create_new_chat_session(user_id, user_message)
    
    append_to_chat_session(session_id, user_message, bot_message)
    
    # 6. Return reply plus attachments list for UI
    return jsonify({
        "reply": reply_text,
        "attachments": attachments,
        "session_id": str(session_id)
    })


# --- Minimal API for listing KB entries (students can view) ---
@app.route("/kb_list")
def kb_list():
    user = current_user()
    if not user:
        return jsonify({"error": "login_required"}), 401
    institute_id = user["institute_id"]
    section = request.args.get("section")
    q = {"institute_id": institute_id}
    if section:
        q["section"] = section
    docs = list(kb_col.find(q).sort("upload_date", -1).limit(100))
    out = []
    for d in docs:
        out.append({
            "id": str(d["_id"]),
            "title": d.get("title"),
            "filename": d.get("filename"),
            "section": d.get("section"),
            "subject": d.get("subject"),
            "branch": d.get("branch"),
            "year": d.get("year"),
            "upload_date": d.get("upload_date").isoformat() if d.get("upload_date") else None
        })
    return jsonify(out)

# --- Run ---
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    