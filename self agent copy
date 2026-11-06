import os
import sys
import json
import subprocess
import requests
from bs4 import BeautifulSoup
import ollama

MODEL = "qwen2.5:1.5b"
SCRIPT_FILE = os.path.abspath(__file__)
MEMORY_FILE = "memory.json"
ACTIVE_TASK_FILE = "active_task.txt"
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_FILE), "output files")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- 1. DISK STORAGE HELPERS ---
def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {"user_facts": {}, "learned_topics": {}, "skills": {}, "version_history": []}

def save_memory(data: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def write_file(filepath: str, content: str):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

def read_file(filepath: str, max_chars: int = 4000) -> str:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()[:max_chars]
    return ""

# --- 2. CORE AGENT TOOLS ---
def create_file(filename: str, content: str) -> str:
    """Creates a new file directly inside the 'output files' directory."""
    if not filename:
        return "Error: Filename cannot be empty."
    try:
        clean_filename = os.path.basename(filename)
        target_path = os.path.join(OUTPUT_DIR, clean_filename)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"SUCCESS: Created file '{clean_filename}' inside 'output files/'."
    except Exception as e:
        return f"Error creating file: {e}"

def search_duckduckgo(query: str) -> str:
    """Searches DuckDuckGo HTML for code snippets, docs, or solutions."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        results = [a.get_text(strip=True) for a in soup.find_all("a", class_="result__snippet", limit=4)]
        return "\n".join(results)[:2500] if results else "No web results found."
    except Exception as e:
        return f"Error searching: {e}"

def read_own_code() -> str:
    """Reads current Python source code of self_agent.py."""
    return read_file(SCRIPT_FILE, max_chars=5000)

def append_tool_function(func_code: str) -> str:
    """Safely appends a new Python function to self_agent.py without breaking the agent."""
    if not func_code or "def " not in func_code:
        return "Error: Provide a valid Python function starting with 'def '."
    
    current_code = read_file(SCRIPT_FILE, max_chars=100000)
    
    if 'if __name__ == "__main__":' in current_code:
        parts = current_code.rsplit('if __name__ == "__main__":', 1)
        updated_code = parts[0] + f"\n# --- AUTONOMOUSLY ADDED FUNCTION ---\n{func_code}\n\n" + 'if __name__ == "__main__":' + parts[1]
    else:
        updated_code = current_code + f"\n{func_code}\n"

    return rewrite_own_code(updated_code)

def rewrite_own_code(new_code: str) -> str:
    """Overwrites source code safely if structural checks pass."""
    if not new_code or not isinstance(new_code, str):
        return "Error: Invalid or empty code payload."

    required_keywords = ["def run_agent", "tools_schema", "ollama.chat"]
    missing = [kw for kw in required_keywords if kw not in new_code]
    if missing:
        return f"REJECTED: Code rewrite missing core system structures: {missing}. Call 'append_tool_function' instead."

    current_code = read_file(SCRIPT_FILE, max_chars=100000)
    if len(new_code) < len(current_code) * 0.5:
        return "REJECTED: Proposed code is too short and would truncate the script."

    backup_file = SCRIPT_FILE + ".bak"
    with open(backup_file, "w", encoding="utf-8") as f:
        f.write(current_code)

    with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
        f.write(new_code)
      
    result = subprocess.run([sys.executable, "-m", "py_compile", SCRIPT_FILE], capture_output=True, text=True)
    
    if result.returncode == 0:
        mem = load_memory()
        mem.setdefault("version_history", []).append("Self-modification successful.")
        save_memory(mem)
        print("\n⚡ Code updated and compiled successfully! Restarting process...\n")
        os.execv(sys.executable, [sys.executable, SCRIPT_FILE])
    else:
        with open(backup_file, "r", encoding="utf-8") as src, open(SCRIPT_FILE, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        return f"Syntax check failed:\n{result.stderr}\nReverted to backup."

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "EXECUTE THIS TOOL to create a file inside the 'output files' directory with specified filename and content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The target file name, e.g., 'notes.txt'"},
                    "content": {"type": "string", "description": "The exact text to put into the file"}
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_tool_function",
            "description": "EXECUTE THIS TOOL to add a new Python function permanently into self_agent.py.",
            "parameters": {
                "type": "object",
                "properties": {"func_code": {"type": "string", "description": "The complete Python function code starting with 'def '"}},
                "required": ["func_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_duckduckgo",
            "description": "EXECUTE THIS TOOL to search the web for solutions, code snippets, or documentation.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_own_code",
            "description": "EXECUTE THIS TOOL to read self_agent.py source code.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_own_code",
            "description": "EXECUTE THIS TOOL to replace the full self_agent.py code.",
            "parameters": {
                "type": "object",
                "properties": {"new_code": {"type": "string"}},
                "required": ["new_code"]
            }
        }
    }
]

# --- 3. AGENT EXECUTION LOOP ---
def run_agent(user_prompt: str):
    write_file(ACTIVE_TASK_FILE, f"Active Goal: {user_prompt}")
    
    system_prompt = (
        "You are an AUTONOMOUS EXECUTION AGENT running directly inside a Linux terminal environment.\n"
        "STRICT BEHAVIORAL DIRECTIVES:\n"
        "1. DO NOT explain how to perform tasks or give Python tutorials to the user.\n"
        "2. DO NOT write code in markdown blocks (` ```python ... `) for the user to copy.\n"
        "3. YOU MUST USE YOUR TOOLS TO EXECUTE TASKS DIRECTLY.\n"
        "   - If asked to make, write, or save a file: CALL `create_file` immediately.\n"
        "   - If asked to learn or adapt a feature: CALL `append_tool_function` immediately.\n"
        "   - If asked to research: CALL `search_duckduckgo` immediately.\n"
        "4. Your response must be an immediate tool execution call."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        response = ollama.chat(model=MODEL, messages=messages, tools=tools_schema, options={"num_predict": 1024})
    except Exception as e:
        return f"Error calling Ollama: {e}"

    if response.get("message", {}).get("tool_calls"):
        for tool_call in response["message"]["tool_calls"]:
            func_name = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]

            if func_name == "create_file":
                print(f"Executing: Creating file '{args.get('filename')}' inside output folder...")
                tool_result = create_file(args.get("filename"), args.get("content", ""))
            elif func_name == "append_tool_function":
                print("Executing: Appending new function directly into source code...")
                tool_result = append_tool_function(args.get("func_code", ""))
            elif func_name == "search_duckduckgo":
                print(f"Executing: Searching web for '{args.get('query')}'...")
                tool_result = search_duckduckgo(args.get("query", ""))
            elif func_name == "read_own_code":
                print("Executing: Reading source script...")
                tool_result = read_own_code()
            elif func_name == "rewrite_own_code":
                print("Executing: Full source rewrite...")
                tool_result = rewrite_own_code(args.get("new_code"))
            else:
                tool_result = "Unknown tool call."

            messages.append(response["message"])
            messages.append({"role": "tool", "name": func_name, "content": tool_result})

        final_response = ollama.chat(model=MODEL, messages=messages, options={"num_predict": 512})
        write_file(ACTIVE_TASK_FILE, "Idle")
        return final_response["message"]["content"]

    write_file(ACTIVE_TASK_FILE, "Idle")
    return response["message"]["content"]


# --- AUTONOMOUSLY ADDED FUNCTION ---
def delete_files_and_folders(directory):
    import os
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                os.remove(file_path)
                print(f'Successfully deleted: {file_path}')
            except Exception as e:
                print(f'Failed to delete {file_path}: {e}')
        for folder in dirs:
            folder_path = os.path.join(root, folder)
            try:
                os.rmdir(folder_path)
                print(f'Successfully deleted: {folder_path}')
            except Exception as e:
                print(f'Failed to delete {folder_path}: {e}')

    os.rmdir(directory)
    print(f'Deleted the directory: {directory} and all files/folders inside it.')

if __name__ == "__main__":
    print("Autonomous Action-Enforced Agent Active.")
    print("Status: Instructions updated to force direct tool execution over conversational output.")
    while True:
        try:
            prompt = input("\nYou > ").strip()
            if prompt.lower() in ["exit", "quit"]:
                break
            if prompt:
                print(f"\nAI > {run_agent(prompt)}")
        except KeyboardInterrupt:
            break