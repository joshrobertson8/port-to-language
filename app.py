# app.py

import os
from flask import Flask, request, render_template, flash, redirect, url_for, jsonify, session
import google.generativeai as genai
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import PyPDF2
from uuid import uuid4
from werkzeug.exceptions import RequestEntityTooLarge

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret_key")  # Needed for flashing messages and session

# Configure Generative AI with API key from environment variables
genai.configure(api_key=os.getenv("GENAI_API_KEY"))

# File upload configurations
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
ALLOWED_EXTENSIONS = {'txt', 'pdf'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB limit

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Ensure the upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Maximum conversation history length
MAX_CONVERSATION_LENGTH = 10  # Adjust as needed

def allowed_file(filename):
    """Check if the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_file(file_path, file_extension):
    """Extract text from a given file based on its extension."""
    if file_extension == 'txt':
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    elif file_extension == 'pdf':
        text = ""
        try:
            with open(file_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return None
        return text
    else:
        return None

def translate_text(text, target_language):
    """Translate text to the target language using Google Generative AI."""
    try:
        model = genai.GenerativeModel(model_name="models/gemini-1.5-flash")
        prompt = f"Translate the following text to {target_language}: {text}"
        response = model.generate_content(
            contents=[{"role": "user", "parts": [prompt]}],
            generation_config=genai.GenerationConfig(
                max_output_tokens=2000,  # Increased token limit for longer texts
                temperature=0.5,
            ),
        )
        translation = response.candidates[0].content.parts[0].text.strip()
        return translation
    except Exception as e:
        # Log the exception as needed
        print(f"Error during translation: {e}")
        return None

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    """Handle files that exceed the maximum allowed size."""
    flash("File is too large. Maximum allowed size is 16 MB.", "error")
    return redirect(url_for('home')), 413

@app.route("/", methods=["GET", "POST"])
def home():
    """Handle the home route with GET and POST methods."""
    translation = ""
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        target_language = request.form.get("target_language", "").strip()
        file = request.files.get("file")

        if file and file.filename != '':
            # Handle file upload
            if allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_extension = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{uuid4().hex}.{file_extension}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)

                # Extract text from the uploaded file
                extracted_text = extract_text_from_file(file_path, file_extension)

                # Remove the file after extraction
                os.remove(file_path)

                if not extracted_text:
                    flash("Failed to extract text from the uploaded file.", "error")
                elif not target_language:
                    flash("Please select a target language.", "error")
                else:
                    translation = translate_text(extracted_text, target_language)
                    if not translation:
                        flash("An error occurred during translation. Please try again.", "error")
            else:
                flash("Unsupported file type. Please upload a .txt or .pdf file.", "error")
        else:
            # Handle text input
            if not text:
                flash("Please enter text to translate.", "error")
            elif not target_language:
                flash("Please select a target language.", "error")
            else:
                translation = translate_text(text, target_language)
                if not translation:
                    flash("An error occurred during translation. Please try again.", "error")

    return render_template("index.html", translation=translation)

@app.route("/chat", methods=["POST"])
def chat():
    """Handle chat messages for conversational translation."""
    data = request.get_json()
    user_message = data.get('message', '').strip()
    source_language = data.get('source_language', '').strip()
    target_language = data.get('target_language', '').strip()

    if not user_message:
        return jsonify({'error': 'Empty message received.'}), 400
    if not source_language or not target_language:
        return jsonify({'error': 'Source and target languages must be selected.'}), 400

    # Retrieve or initialize conversation history from the session
    conversation = session.get('conversation', [])

    # Append the user's message to the conversation with language context
    conversation.append({'role': 'user', 'content': user_message, 'source_language': source_language, 'target_language': target_language})

    # Truncate conversation history if it exceeds the maximum length
    if len(conversation) > MAX_CONVERSATION_LENGTH:
        conversation = conversation[-MAX_CONVERSATION_LENGTH:]
        session['conversation'] = conversation
    else:
        session['conversation'] = conversation

    # Construct the prompt with conversation history for context
    prompt = ""
    for entry in conversation:
        role = "User" if entry['role'] == 'user' else "Bot"
        lang = f" ({entry['source_language']} -> {entry['target_language']})" if entry['role'] == 'user' else ""
        prompt += f"{role}{lang}: {entry['content']}\n"

    # Add the translation instruction
    prompt += "Bot: "

    try:
        model = genai.GenerativeModel(model_name="models/gemini-1.5-flash")
        response = model.generate_content(
            contents=[{"role": "user", "parts": [prompt]}],
            generation_config=genai.GenerationConfig(
                max_output_tokens=2000,
                temperature=0.5,
            ),
        )
        bot_reply = response.candidates[0].content.parts[0].text.strip()

        # Append the bot's reply to the conversation with language context
        conversation.append({'role': 'bot', 'content': bot_reply, 'source_language': target_language, 'target_language': source_language})

        # Truncate conversation history if it exceeds the maximum length
        if len(conversation) > MAX_CONVERSATION_LENGTH:
            conversation = conversation[-MAX_CONVERSATION_LENGTH:]
        session['conversation'] = conversation

        return jsonify({'translation': bot_reply}), 200
    except Exception as e:
        print(f"Error during chat translation: {e}")
        return jsonify({'error': 'An error occurred during translation.'}), 500

@app.route("/reset_chat", methods=["POST"])
def reset_chat():
    """Reset the conversation history."""
    session.pop('conversation', None)
    return jsonify({'status': 'success'}), 200

if __name__ == "__main__":
    app.run(debug=True)
