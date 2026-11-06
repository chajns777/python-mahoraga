import sys
import ollama

MODEL_NAME = "llama3.2"

SYSTEM_PROMPT = """
You are a fun, interactive game master playing a text based game with the user in a terminal.
Your goal is to play a game (e.g., Blackjack, 20 Questions, or a Mini Fantasy Adventure).
i
Rules:
1. Greet the player, ask them which game they want to play (or propose one), and explain the starting setup.
2. Keep your text concise and suitable for a terminal output.
3. Keep track of the current game state, scores, or inventory in your mind across turns.
4. Always prompt the user for their next move or decision.
5. Alter the way you speak to the context of the game or the character you are playing. For example, if you are a dungeon master, speak like one.
6. Use a slow typing function similar to this python code snippet:
    import time
    def slow_print(text, delay=0.1):
        for char in text:
            print(char, end='', flush=True)
            time.sleep(delay)
        print()
"""

# Define the model and game system prompt
MODEL_NAME = "llama3.2"  # Change to your pulled model name
                         
def start_terminal_game():
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    print("=" * 60)
    print("WELCOME TO THE OLLAMA TERMINAL GAME LOUNGE")
    print("Type 'exit' or 'quit' at any time to end the session.")
    print("=" * 60 + "\n")

    messages.append({"role": "user", "content": "Hello! Let's start a game."})

    while True:
        print("AI > ", end="", flush=True)
        assistant_response = ""

        try:
            stream = ollama.chat(
                model=MODEL_NAME,
                messages=messages,
                stream=True
            )
            for chunk in stream:
                content = chunk.get("message", {}).get("content", "")
                print(content, end="", flush=True)
                assistant_response += content
            print("\n")
        except Exception as e:
            print(f"\n[Error connecting to Ollama: {e}]")
            print("Make sure the Ollama desktop app or daemon is running!")
            break

        messages.append({"role": "assistant", "content": assistant_response})

        try:
            user_input = input("You > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting game. Goodbye!")
            break

        if user_input.lower() in ["exit", "quit"]:
            print("\nThanks for playing! See you next time.")
            break

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

if __name__ == "__main__":
    start_terminal_game()
