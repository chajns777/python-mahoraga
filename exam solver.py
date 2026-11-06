import os
import json
import streamlit as st
import ollama
from pypdf import PdfReader

UPLOAD_DIR = "app_data/uploads"
HISTORY_DIR = "app_data/history"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

st.set_page_config(page_title="Offline Exam Solver", layout="wide")
st.title("Local PDF & Image Exam Solver")

def save_chat_history(session_id, messages):
    path = os.path.join(HISTORY_DIR, f"{session_id}.json")
    with open(path, "w") as f:
        json.dump(messages, f)

def load_chat_history(session_id):
    path = os.path.join(HISTORY_DIR, f"{session_id}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []

def delete_chat(session_id):
    path = os.path.join(HISTORY_DIR, f"{session_id}.json")
    if os.path.exists(path):
        os.remove(path)

st.sidebar.header("Chat & File History")

saved_chats = [f.replace(".json", "") for f in os.listdir(HISTORY_DIR) if f.endswith(".json")]
selected_chat = st.sidebar.selectbox("Select History", ["New Chat"] + saved_chats)

if selected_chat != "New Chat":
    session_id = selected_chat
    st.session_state.messages = load_chat_history(session_id)
else:
    session_id = "chat_1"
    if "messages" not in st.session_state:
        st.session_state.messages = []

if st.sidebar.button("Delete Selected Chat") and selected_chat != "New Chat":
    delete_chat(selected_chat)
    st.session_state.messages = []
    st.rerun()

uploaded_file = st.file_uploader("Upload an Exam Paper (PDF or Image)", type=["pdf", "png", "jpg"])
extracted_text = ""

if uploaded_file:
    file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    st.success(f"Saved to local folder: `{file_path}`")
    
    if uploaded_file.name.endswith(".pdf"):
        reader = PdfReader(file_path)
        for page in reader.pages:
            extracted_text += page.extract_text() or ""

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_query = st.chat_input("Ask a question about the uploaded paper...")

if user_query:

    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    full_prompt = user_query
    if extracted_text:
        full_prompt = f"Context from uploaded exam:\n{extracted_text}\n\nUser Question: {user_query}"

    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        response = ollama.chat(
            model="qwen2.5-vl", 
            messages=[{"role": "user", "content": full_prompt}]
        )
        
        answer = response['message']['content']
        response_placeholder.markdown(answer)
    
        st.session_state.messages.append({"role": "assistant", "content": answer})
        save_chat_history(session_id, st.session_state.messages)
