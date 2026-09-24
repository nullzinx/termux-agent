#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx


API_URL = os.getenv(
    "AGENT_API_URL",
    "https://api.groq.com/openai/v1/chat/completions",
)
API_KEY = os.getenv("AGENT_API_KEY")
MODEL = os.getenv("AGENT_MODEL")

MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "12"))
WORKDIR = Path(os.getenv("AGENT_WORKDIR", ".")).expanduser().resolve()

SYSTEM = """You are a lightweight coding agent running inside Termux on Android.
Work carefully and minimize expensive operations.

Rules:
- Work only inside the configured working directory unless explicitly allowed.
- Prefer small, targeted changes.
- Before editing an existing file, read it.
- Never expose secrets from environment variables, .env files, SSH keys, tokens, or credentials.
- Do not run destructive commands such as rm -rf, git reset --hard, git clean -fd, or commands involving sudo.
- Do not install packages unless the user explicitly asks.
- After changes, inspect the relevant diff when possible.
- Explain what you changed and what remains uncertain.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workdir.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file inside the workdir. Use only after reading an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search text recursively in project files using Python. Skips .git and virtual environments.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a safe, allowlisted command in the workdir. No shell syntax.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show the current git diff.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

ALLOWED = {
    "python", "python3", "git", "grep", "find", "ls", "pwd",
    "head", "tail", "wc", "file", "pytest", "ruff",
}

BLOCKED_ARGS = {
    "rm", "rmdir", "sudo", "su", "dd", "mkfs", "shutdown",
    "reboot", "chmod", "chown",
}


def inside_workdir(path: str) -> Path:
    p = (WORKDIR / path).resolve()
    try:
        p.relative_to(WORKDIR)
    except ValueError:
        raise RuntimeError("Path escapes AGENT_WORKDIR")
    return p


def read_file(path: str):
    p = inside_workdir(path)
    if p.name in {".env", ".env.local"} or ".git" in p.parts:
        raise RuntimeError("Access to secrets or .git internals is blocked")
    return p.read_text(encoding="utf-8", errors="replace")[:50000]


def write_file(path: str, content: str):
    p = inside_workdir(path)
    if p.name in {".env", ".env.local"} or ".git" in p.parts:
        raise RuntimeError("Writing secrets or .git internals is blocked")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p.relative_to(WORKDIR)} ({len(content)} bytes)"


def search_files(query: str):
    results = []
    for p in WORKDIR.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".git", ".venv", "venv", "__pycache__", "node_modules"} for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if query.lower() in text.lower():
            results.append(str(p.relative_to(WORKDIR)))
        if len(results) >= 100:
            break
    return "\n".join(results) or "No matches."


def run_command(command):
    if not command or command[0] not in ALLOWED:
        raise RuntimeError(f"Command not allowed: {command!r}")
    if any(arg in BLOCKED_ARGS for arg in command):
        raise RuntimeError("Blocked command/argument")
    if any(token in " ".join(command) for token in ["&&", "||", ";", "|", ">", "<", "`", "$("]):
        raise RuntimeError("Shell syntax is disabled; pass arguments separately.")
    proc = subprocess.run(
        command,
        cwd=WORKDIR,
        text=True,
        capture_output=True,
        timeout=30,
    )
    output = (proc.stdout + proc.stderr).strip()
    return f"exit={proc.returncode}\n{output[-12000:]}"


def tool_call(name, args):
    if name == "read_file":
        return read_file(args["path"])
    if name == "write_file":
        return write_file(args["path"], args["content"])
    if name == "search_files":
        return search_files(args["query"])
    if name == "run_command":
        return run_command(args["command"])
    if name == "git_diff":
        return run_command(["git", "diff", "--", "."])
    raise RuntimeError(f"Unknown tool: {name}")


def ask(messages):
    if not API_KEY:
        raise RuntimeError("Set AGENT_API_KEY first.")
    if not MODEL:
        raise RuntimeError("Set AGENT_MODEL first.")

    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.1,
    }

    with httpx.Client(timeout=60) as client:
        r = client.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


def main():
    if len(sys.argv) < 2:
        print('Uso: agent "tarefa"')
        sys.exit(1)

    task = " ".join(sys.argv[1:])
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Working directory: {WORKDIR}\n\nTask:\n{task}",
        },
    ]

    for step in range(MAX_TOOL_CALLS + 1):
        try:
            msg = ask(messages)
        except Exception as e:
            print(f"API error: {e}", file=sys.stderr)
            sys.exit(1)

        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            print(msg.get("content", ""))
            return

        if step >= MAX_TOOL_CALLS:
            print("Maximum tool calls reached.", file=sys.stderr)
            return

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"].get("arguments", "{}"))
            try:
                result = tool_call(name, args)
            except Exception as e:
                result = f"TOOL ERROR: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

            print(f"[tool] {name}", file=sys.stderr)

if __name__ == "__main__":
    main()
