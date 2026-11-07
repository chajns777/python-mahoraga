import os
import ast
import sys
import json
import time
import shutil
import inspect
import importlib.util
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
import ollama


# PATHS 


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")
LEARNED_FILE = os.path.join(BASE_DIR, "learned_tools.py")    
CANDIDATE_FILE = os.path.join(BASE_DIR, "learned_tools.candidate.py")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
OUTPUT_DIR = os.path.join(BASE_DIR, "output files")
MAX_BACKUPS = 5

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "name": "Aria",
    "model": "qwen2.5:1.5b",
    "speaking_style": "Short, direct and friendly. Never lecture.",
    "max_history_messages": 35,  
    "max_steps": 6,              
}

DEFAULT_MEMORY = {
    "history": [],      
    "user_facts": {},     
    "version_history": [],
}

# 1. CONFIG + MEMORY


def load_json(path: str, default: dict) -> dict:
    """Loads a json file, filling in any missing keys. Creates it if missing."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            print(f"[warning] {os.path.basename(path)} was unreadable, using defaults.")
            data = {}
    merged = {**default, **data}    
    if not os.path.exists(path) or merged != data:
        save_json(path, merged)
    return merged


def save_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)          


CONFIG = load_json(CONFIG_FILE, DEFAULT_CONFIG)
MEMORY = load_json(MEMORY_FILE, DEFAULT_MEMORY)


def remember_exchange(user_text: str, ai_text: str):
    MEMORY["history"].append({"role": "user", "content": user_text})
    MEMORY["history"].append({"role": "assistant", "content": ai_text})
    MEMORY["history"] = MEMORY["history"][-200:]
    save_json(MEMORY_FILE, MEMORY)


# 2. BACKUPS 


def list_backups() -> list:
    files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".py")]
    return sorted(files, reverse=True)    # newest first


def make_backup(tag: str = "before_update") -> str:
    """Copies the current learned_tools.py into backups/ and prunes old ones."""
    if not os.path.exists(LEARNED_FILE):
        return ""
    name = f"learned_tools_{time.strftime('%Y%m%d_%H%M%S')}_{tag}.py"
    shutil.copy2(LEARNED_FILE, os.path.join(BACKUP_DIR, name))
    for old in list_backups()[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return name


def rollback(index: int = 1) -> str:
    """Restores backup number `index` (1 = newest). Current version is backed up first."""
    backups = list_backups()
    if not backups:
        return "No backups available."
    if not 1 <= index <= len(backups):
        return f"Pick a number between 1 and {len(backups)}."
    chosen = backups[index - 1]
    make_backup("before_rollback")
    shutil.copy2(os.path.join(BACKUP_DIR, chosen), LEARNED_FILE)
    load_learned_tools()
    return f"Restored {chosen}. Learned tools now: {', '.join(LEARNED) or 'none'}"


# 3. LEARNED TOOLS 


LEARNED = {} 

BANNED_IMPORTS = {"subprocess", "shutil", "ctypes", "multiprocessing"}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "exit", "quit"}
BANNED_ATTRS = {
    "remove", "unlink", "rmdir", "rmtree", "removedirs", "system", "popen",
    "Popen", "kill", "chmod", "exit", "_exit", "execv", "execl",
}


def validate_code(code: str):
    """Returns an error string if the code is unsafe/invalid, else None."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    funcs = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            funcs += 1
            doc = ast.get_docstring(node)
            if not doc:
                return f"Function '{node.name}' needs a docstring describing what it does."
            first_line = doc.strip().splitlines()[0]
            if len(first_line) > 100:
                return (f"The first docstring line of '{node.name}' must be a short summary "
                        f"(under 100 characters). It is shown in the help list.")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            pass
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            pass
        else:
            return "Only 'import' lines and 'def' functions are allowed at the top level."
    if funcs == 0:
        return "No function found. Provide at least one complete 'def' function."

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_IMPORTS:
                    return f"Import of '{alias.name}' is not allowed."
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BANNED_IMPORTS:
                return f"Import from '{node.module}' is not allowed."
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return f"Use of '{node.id}' is not allowed."
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            return f"Use of '.{node.attr}' is not allowed (no deleting, shell or exiting)."
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = None
            if len(node.args) > 1:
                mode = node.args[1]
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = kw.value
            if mode is not None:
                if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
                    return "open() mode must be a plain string like 'r'."
                if any(c in mode.value for c in "wax+"):
                    return "Don't open files for writing. Use save_output(filename, content) instead."
    return None


def _load_module(path: str):
    """Imports a tools file, giving it safe helpers. Raises on error."""
    spec = importlib.util.spec_from_file_location("learned_tools", path)
    mod = importlib.util.module_from_spec(spec)
    mod.save_output = save_output       
    mod.OUTPUT_DIR = OUTPUT_DIR
    spec.loader.exec_module(mod)
    return mod


def load_learned_tools():
    LEARNED.clear()
    if not os.path.exists(LEARNED_FILE):
        return
    try:
        mod = _load_module(LEARNED_FILE)
    except Exception as e:
        print(f"[warning] learned_tools.py failed to load: {e}\n"
              f"          Use /backups and /rollback to restore a working version.")
        return
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if fn.__module__ == "learned_tools" and not name.startswith("_"):
            LEARNED[name] = fn


PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def build_schema(fn) -> dict:
    props, required = {}, []
    for pname, p in inspect.signature(fn).parameters.items():
        props[pname] = {"type": PY_TO_JSON.get(p.annotation, "string")}
        if p.default is inspect.Parameter.empty:
            required.append(pname)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": "EXECUTE THIS TOOL: " + (inspect.getdoc(fn) or fn.__name__),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


def learn_new_tool(func_code: str) -> str:
    """Permanently adds (or replaces) a Python function in learned_tools.py."""
    code = strip_fences(func_code or "")
    error = validate_code(code)
    if error:
        return f"REJECTED: {error} Fix it and call learn_new_tool again."

    new_names = {n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef)}
    if new_names & CORE_TOOL_NAMES:
        return f"REJECTED: {sorted(new_names & CORE_TOOL_NAMES)} clash with built-in tools. Use a different name."

    segments = []
    if os.path.exists(LEARNED_FILE):
        existing = open(LEARNED_FILE, "r", encoding="utf-8").read()
        try:
            for node in ast.parse(existing).body:
                if isinstance(node, ast.FunctionDef) and node.name in new_names:
                    continue
                seg = ast.get_source_segment(existing, node)
                if seg:
                    segments.append(seg)
        except SyntaxError:
            segments = []
    segments.append(code)
    merged = "\n\n".join(segments) + "\n"

    with open(CANDIDATE_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
    try:
        _load_module(CANDIDATE_FILE)
    except Exception as e:
        os.remove(CANDIDATE_FILE)
        return f"REJECTED: the code failed to load: {e}. Nothing was changed."

    backup = make_backup("before_update")  
    os.replace(CANDIDATE_FILE, LEARNED_FILE)
    load_learned_tools()                    

    MEMORY.setdefault("version_history", []).append(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} learned: {', '.join(sorted(new_names))}")
    save_json(MEMORY_FILE, MEMORY)
    for n in sorted(new_names):
        print(f"  [help] added to help list: {n} - {short_desc(LEARNED[n]) if n in LEARNED else ''}")
    return (f"SUCCESS: learned {sorted(new_names)} and added to the help list. Backup of previous version: "
            f"{backup or 'none (first tool)'}. You can call the new tool right now.")


# 4. CORE TOOLS


def save_output(filename: str, content: str) -> str:
    """Writes a file inside the 'output files' folder (the only place the agent may write)."""
    if not filename:
        return "Error: filename cannot be empty."
    clean = os.path.basename(filename)
    with open(os.path.join(OUTPUT_DIR, clean), "w", encoding="utf-8") as f:
        f.write(content)
    return f"SUCCESS: saved '{clean}' in 'output files/'."


def create_file(filename: str, content: str = "") -> str:
    try:
        return save_output(filename, content)
    except Exception as e:
        return f"Error creating file: {e}"


SEARCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _format_results(results: list) -> str:
    """results = list of (title, snippet, url)"""
    lines = []
    for i, (title, snippet, url) in enumerate(results, 1):
        lines.append(f"{i}. {title}\n   {snippet}\n   {url}")
    return "\n".join(lines)[:3500]


def _search_ddgs(query: str):
    """Best backend: the 'ddgs' library (pip install ddgs). Returns (results, error)."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return None, "ddgs not installed (run: pip install ddgs)"
    try:
        hits = DDGS().text(query, max_results=6)
        return [(h.get("title", ""), h.get("body", ""), h.get("href", "")) for h in hits], None
    except Exception as e:
        return None, f"ddgs error: {e}"


def _clean_ddg_url(href: str) -> str:
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


def _search_ddg_html(query: str):
    """Fallback: scrape DuckDuckGo's HTML page (POST works better than GET)."""
    try:
        res = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                            headers=SEARCH_HEADERS, timeout=10)
    except Exception as e:
        return None, f"request failed: {e}"
    if res.status_code != 200:
        return None, f"HTTP {res.status_code} (DuckDuckGo is probably blocking scripts)"
    if "anomaly" in res.text.lower() or "captcha" in res.text.lower():
        return None, "DuckDuckGo showed a CAPTCHA / bot check"
    soup = BeautifulSoup(res.text, "html.parser")
    out = []
    for r in soup.select("div.result")[:6]:
        link = r.select_one("a.result__a")
        snip = r.select_one(".result__snippet")
        if link:
            out.append((link.get_text(strip=True),
                        snip.get_text(" ", strip=True) if snip else "",
                        _clean_ddg_url(link.get("href", ""))))
    return (out or None), (None if out else "page loaded but no results were parsed")


def _search_wikiquote(query: str):
    """Last resort (great for quotes): Wikiquote's free API, no blocking."""
    api = "https://en.wikiquote.org/w/api.php"
    try:
        r = requests.get(api, params={"action": "query", "list": "search", "srsearch": query,
                                      "format": "json", "srlimit": 3},
                         headers=SEARCH_HEADERS, timeout=10).json()
        hits = r.get("query", {}).get("search", [])
        if not hits:
            return None, "no Wikiquote hits"
        title = hits[0]["title"]
        ex = requests.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                       "exchars": 2500, "titles": title, "redirects": 1,
                                       "format": "json"},
                          headers=SEARCH_HEADERS, timeout=10).json()
        page = next(iter(ex["query"]["pages"].values()))
        text = " ".join((page.get("extract") or "").split())
        url = "https://en.wikiquote.org/wiki/" + title.replace(" ", "_")
        return [(f"Wikiquote: {title}", text[:2500], url)], None
    except Exception as e:
        return None, f"wikiquote error: {e}"


def search_duckduckgo(query: str) -> str:
    """Searches the web, trying several backends until one works."""
    notes = []
    for name, backend in (("ddgs", _search_ddgs), ("duckduckgo-html", _search_ddg_html),
                          ("wikiquote", _search_wikiquote)):
        results, err = backend(query)
        if results:
            return f"[source: {name}]\n" + _format_results(results)
        print(f"  [search] {name} failed: {err or 'no results'}")    
        notes.append(f"{name}: {err or 'no results'}")
    return ("SEARCH FAILED (" + " | ".join(notes) + "). Retry with different, simpler keywords "
            "before giving up.")


def fetch_webpage(url: str) -> str:
    """Opens a web page and returns its readable text."""
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"
    try:
        res = requests.get(url, headers=SEARCH_HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
        return text[:3500] or "The page had no readable text."
    except Exception as e:
        return f"Error fetching page: {e}"


def remember_fact(key: str, value: str) -> str:
    MEMORY.setdefault("user_facts", {})[key] = value
    save_json(MEMORY_FILE, MEMORY)
    return f"SUCCESS: remembered {key} = {value}"


def read_my_tools() -> str:
    if not os.path.exists(LEARNED_FILE):
        return "No learned tools yet."
    return open(LEARNED_FILE, "r", encoding="utf-8").read()[:5000]


def list_my_tools() -> str:
    core = sorted(CORE_TOOL_NAMES)
    return f"Built-in: {core}\nLearned: {sorted(LEARNED) or 'none'}"


CORE_FUNCS = {
    "create_file": create_file,
    "search_duckduckgo": search_duckduckgo,
    "fetch_webpage": fetch_webpage,
    "learn_new_tool": learn_new_tool,
    "remember_fact": remember_fact,
    "read_my_tools": read_my_tools,
    "list_my_tools": list_my_tools,
}
CORE_TOOL_NAMES = set(CORE_FUNCS)


# --- HELP LIST ---


CORE_HELP = {
    "create_file": "Create a text/code file in the 'output files' folder",
    "search_duckduckgo": "Search the web (tries several search backends)",
    "fetch_webpage": "Open a web page and read its text",
    "learn_new_tool": "Teach itself a new ability (saved permanently)",
    "remember_fact": "Remember something about you across restarts",
    "read_my_tools": "Show the code of everything it has learned",
    "list_my_tools": "List every tool it currently has",
}

COMMANDS_HELP = {
    "help": "Show this list",
    "/search <text>": "Run a web search yourself (for testing)",
    "/tools": "List all tools",
    "/code": "Show the code of learned tools",
    "/backups": "List backups of learned tools",
    "/rollback [n]": "Restore a backup (1 = newest)",
    "/forget": "Clear conversation history (facts are kept)",
    "exit": "Quit",
}


def short_desc(fn) -> str:
    doc = (inspect.getdoc(fn) or "").strip()
    line = doc.splitlines()[0] if doc else "(no description)"
    return line[:100]


def help_text() -> str:
    width = max([len(n) for n in list(CORE_HELP) + list(LEARNED) + list(COMMANDS_HELP)] + [10]) + 2
    lines = ["", "=== WHAT I CAN DO ===", "Built-in tools (just ask me in plain English):"]
    for name, desc in CORE_HELP.items():
        lines.append(f"  {name.ljust(width)}{desc}")
    lines.append("")
    lines.append("Things I have learned:")
    if LEARNED:
        for name, fn in sorted(LEARNED.items()):
            lines.append(f"  {name.ljust(width)}{short_desc(fn)}")
    else:
        lines.append("  (nothing yet - ask me to learn something!)")
    lines.append("")
    lines.append("Commands you can type:")
    for cmd, desc in COMMANDS_HELP.items():
        lines.append(f"  {cmd.ljust(width)}{desc}")
    return "\n".join(lines)

tools_schema = [
    {"type": "function", "function": {
        "name": "create_file",
        "description": "EXECUTE THIS TOOL to create a file in the 'output files' folder.",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string", "description": "e.g. 'notes.txt'"},
            "content": {"type": "string", "description": "Exact text for the file"}},
            "required": ["filename", "content"]}}},
    {"type": "function", "function": {
        "name": "learn_new_tool",
        "description": ("EXECUTE THIS TOOL whenever the user wants a new ability or feature. Pass complete "
                        "Python function(s) with type hints and a docstring whose first line is a short summary "
                        "(shown in the help list). The function becomes a permanent "
                        "tool you can call immediately afterwards."),
        "parameters": {"type": "object", "properties": {
            "func_code": {"type": "string", "description": "Complete Python code: imports and def functions only"}},
            "required": ["func_code"]}}},
    {"type": "function", "function": {
        "name": "search_duckduckgo",
        "description": ("EXECUTE THIS TOOL to search the web. Returns titles, snippets and URLs. "
                        "Use short keyword queries, e.g. 'Dante Inferno famous quotes'."),
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_webpage",
        "description": "EXECUTE THIS TOOL to open a URL from search results and read the page text.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "remember_fact",
        "description": "EXECUTE THIS TOOL to permanently remember a fact about the user (name, preferences, etc).",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {
        "name": "read_my_tools",
        "description": "EXECUTE THIS TOOL to read the source code of the tools you have learned so far.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_my_tools",
        "description": "EXECUTE THIS TOOL to list every tool you currently have.",
        "parameters": {"type": "object", "properties": {}}}},
]


def get_tools_schema() -> list:
    return tools_schema + [build_schema(fn) for fn in LEARNED.values()]


def execute_tool(name: str, args) -> str:
    if not isinstance(args, dict):
        args = {}
    fn = CORE_FUNCS.get(name) or LEARNED.get(name)
    if fn is None:
        return f"Unknown tool '{name}'. Available: {list_my_tools()}"
    try:
        return str(fn(**args))
    except TypeError as e:
        return f"Bad arguments for {name}: {e}"
    except Exception as e:
        return f"Tool '{name}' crashed: {e}"


# 5. AGENT LOOP


def build_system_prompt() -> str:
    facts = MEMORY.get("user_facts", {})
    facts_text = "\n".join(f"- {k}: {v}" for k, v in facts.items()) or "- (none yet)"
    return (
        f"You are {CONFIG['name']}, an AUTONOMOUS agent running on the user's computer.\n"
        f"How you speak: {CONFIG['speaking_style']}\n\n"
        "THINGS YOU KNOW ABOUT THE USER:\n" + facts_text + "\n\n"
        "RULES:\n"
        "1. The user wants YOU to do things, never to be told how. Do not explain steps, do not give tutorials, "
        "and never paste code in the chat.\n"
        "2. Act by calling tools immediately.\n"
        "3. If you lack an ability, call learn_new_tool with a complete Python function (type hints + docstring), "
        "then in the next step CALL that new tool to finish the user's request.\n"
        "4. Learned tools cannot delete files, run shell commands or exit. To write files they must use "
        "save_output(filename, content), which is already available inside learned tools.\n"
        "5. If the user tells you something about themselves, call remember_fact.\n"
        "   The FIRST LINE of every learned function's docstring must be a short plain-English summary "
        "(under 100 characters) - it is shown to the user in the help list.\n"
        "6. RESEARCH: use short keyword queries. If a search fails or looks unhelpful, search again with "
        "different keywords (at least 2 more tries) or use fetch_webpage on a result URL. Never say you "
        "couldn't find something until you have tried this. Once you have the information, finish the "
        "task yourself (e.g. call create_file with the real text you found, not a placeholder).\n"
        "7. After tools finish, reply with ONE or TWO short sentences saying what you did.\n\n"
        f"Current tools: {list_my_tools()}"
    )


def looks_like_advice(text: str) -> bool:
    t = text.lower()
    return "```" in t or any(p in t for p in (
        "you can ", "you could", "here's how", "here is how", "you should", "to do this", "step 1"))


def run_agent(user_prompt: str) -> str:
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages += MEMORY["history"][-CONFIG["max_history_messages"]:]
    messages.append({"role": "user", "content": user_prompt})

    nudged = False
    final_text = ""
    for _ in range(CONFIG["max_steps"]):
        try:
            response = ollama.chat(model=CONFIG["model"], messages=messages,
                                   tools=get_tools_schema(), options={"num_predict": 1024})
        except Exception as e:
            return f"Error calling Ollama: {e}"

        msg = response["message"]
        calls = msg.get("tool_calls")

        if not calls:
            final_text = msg.get("content") or ""

            if not nudged and looks_like_advice(final_text):
                nudged = True
                messages.append(msg)
                messages.append({"role": "user", "content":
                                 "You explained instead of acting. Do NOT explain or show code. "
                                 "Call the correct tool now and do it yourself."})
                continue
            break

        messages.append(msg)
        for call in calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            print(f"  [tool] {name}")
            messages.append({"role": "tool", "name": name, "content": execute_tool(name, args)[:3000]})
    else:
        final_text = final_text or "I hit my step limit before finishing. Try breaking the request up."

    remember_exchange(user_prompt, final_text)
    return final_text


# 6. COMMANDS + MAIN


def handle_command(line: str) -> bool:
    """Returns True if the line was a /command."""
    if not line.startswith("/"):
        return False
    parts = line.split()
    cmd = parts[0].lower()
    if cmd == "/backups":
        backups = list_backups()
        print("\n".join(f"{i}. {b}" for i, b in enumerate(backups, 1)) or "No backups yet.")
    elif cmd == "/rollback":
        idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        print(rollback(idx))
    elif cmd == "/search":
        query = line[len("/search"):].strip()
        print(search_duckduckgo(query) if query else "Usage: /search <text>")
    elif cmd == "/tools":
        print(list_my_tools())
    elif cmd == "/code":
        print(read_my_tools())
    elif cmd == "/forget":
        MEMORY["history"] = []
        save_json(MEMORY_FILE, MEMORY)
        print("Conversation history cleared (facts kept).")
    else:
        print('Unknown command. Type "help" to see everything I can do.')
    return True


if __name__ == "__main__":
    load_learned_tools()
    print(f"{CONFIG['name']} is active. Model: {CONFIG['model']}")
    print(f"Loaded {len(MEMORY['history'])} remembered messages, {len(LEARNED)} learned tools.")
    print('\nType "help" for a list of what I can do (you can type it at any time), or just start chatting.')
    while True:
        try:
            prompt = input("\nYou > ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if prompt.lower() in ("exit", "quit"):
            break
        if not prompt:
            continue
        if prompt.lower() in ("help", "/help", "?"):
            print(help_text())
            continue
        if handle_command(prompt):
            continue
        print(f"\n{CONFIG['name']} > {run_agent(prompt)}")