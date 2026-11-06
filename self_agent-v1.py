import os
import sys
import json
import subprocess
import requests
from bs4 import BeautifulSoup
import ollama

MODEL = "qwen2.5:3b"
SCRIPT_FILE = os.path.abspath(__file__)
MEMORY_FILE = "memory.json"

# --- 1. PERSISTENT MEMORY ---
def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {"user_facts": {}, "learned_topics": {}, "version_history": []}

def save_memory(data: dict):
    with open(MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- 2. TOOLS: WEB SCRAPING & SELF-EDITING ---
def scrape_webpage(url: str) -> str:
    """Fetch and clean text from an online URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()
        return soup.get_text(separator=" ", strip=True)[:4000]
    except Exception as e:
        return f"Error scraping web page: {e}"

def read_own_code() -> str:
    with open(SCRIPT_FILE, "r") as f:
        content = f.read()
    return content[:2500]

def rewrite_own_code(new_code: str) -> str:
    """Overwrites the script's source code, validates syntax, and restarts if successful."""
    backup_file = SCRIPT_FILE + ".bak"

    with open(backup_file, "w") as f:
        f.write(read_own_code())

    with open(SCRIPT_FILE, "w") as f:
        f.write(new_code)
      
    result = subprocess.run([sys.executable, "-m", "py_compile", SCRIPT_FILE], capture_output=True, text=True)
    
    if result.returncode == 0:
        mem = load_memory()
        mem.setdefault("version_history", []).append("Self-modification successful.")
        save_memory(mem)
        
        print("\n⚡ Code successfully compiled! Restarting script with new features...\n")
        os.execv(sys.executable, [sys.executable, SCRIPT_FILE])
    else:

        with open(backup_file, "r") as src, open(SCRIPT_FILE, "w") as dst:
            dst.write(src.read())
        return f"Self-modification failed compilation error:\n{result.stderr}\nReverted back to previous stable code."

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "scrape_webpage",
            "description": "Scrape raw text content from an online web link.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_own_code",
            "description": "Read the entire current Python source code of this agent.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_own_code",
            "description": "Rewrite the agent's Python code file to add features, fix bugs, or improve logic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_code": {"type": "string", "description": "The complete Python code file content"}
                },
                "required": ["new_code"]
            }
        }
    }
]

# --- 3. AGENT EXECUTION LOOP ---
def run_agent(user_prompt: str):
    memory = load_memory()
    
    relevant_context = [
        f"- Learned topic [{topic}]: {summary}"
        for topic, summary in memory.get("learned_topics", {}).items()
        if topic.lower() in user_prompt.lower()
    ]
    context_str = "\n".join(relevant_context) if relevant_context else "No prior background needed."

    system_prompt = (
        "You are an autonomous, self-improving AI script.\n"
        "You can inspect your own source code (`read_own_code`) and overwrite your code (`rewrite_own_code`) to adapt or add features.\n"
        f"RECALLED MEMORY:\n{context_str}\n"
        "Focus on current user tasks without letting unneeded memory interfere."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    response = ollama.chat(model=MODEL, messages=messages, tools=tools_schema)

    if response.get("message", {}).get("tool_calls"):
        for tool_call in response["message"]["tool_calls"]:
            func_name = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]

            if func_name == "scrape_webpage":
                print(f"Reading online link: {args.get('url')}...")
                tool_result = scrape_webpage(args.get("url"))
            elif func_name == "read_own_code":
                print("Reading own Python source file...")
                tool_result = read_own_code()
            elif func_name == "rewrite_own_code":
                print("Rewriting own source code...")
                tool_result = rewrite_own_code(args.get("new_code"))

            messages.append(response["message"])
            messages.append({"role": "tool", "name": func_name, "content": tool_result})

        final_response = ollama.chat(model=MODEL, messages=messages)
        return final_response["message"]["content"]

    return response["message"]["content"]

# --- 4. MAIN USER INTERACTION ---
if __name__ == "__main__":
    print("Self-Editing Agent Started.")
    print("Commands: Ask a question, or command: 'Inspect your code and add a feature to save responses to a txt file'")
    
    while True:
        try:
            prompt = input("\nYou > ").strip()
            if prompt.lower() in ["exit", "quit"]:
                break
            if not prompt:
                continue
            output = run_agent(prompt)
            print(f"\nAI > {output}")
        except KeyboardInterrupt:
            break
