"""A small Groq-powered agent for fixing simple C++ bugs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq


PROJECT_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = PROJECT_DIR.parents[1]
SAMPLE_CPP = PROJECT_DIR / "buggy_program.cpp"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_ATTEMPTS = 3
IGNORED_DIRS = {".git", ".venv", "__pycache__", "build", "dist"}


def _inside_project(path: str) -> Path:
    """Resolve an agent-supplied path and reject paths outside this project."""
    candidate = (PROJECT_DIR / path).resolve()
    try:
        candidate.relative_to(PROJECT_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Path must stay inside the codingagent project.") from exc
    if not candidate.is_file():
        raise ValueError(f"File does not exist: {path}")
    return candidate


def search_files(query: str) -> str:
    """Find project source files whose names or contents match bug-related words."""
    print(f"\n[TOOL] search_files(query={query!r})")
    terms = [word.lower() for word in query.split() if len(word) > 2]
    matches: list[tuple[int, str]] = []
    for path in PROJECT_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".cpp", ".cc", ".c", ".h", ".hpp"}:
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(PROJECT_DIR).parts):
            continue
        relative = path.relative_to(PROJECT_DIR)
        try:
            content = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError as exc:
            print(f"[TOOL] Could not read {relative}: {exc}")
            continue
        score = sum(term in relative.name.lower() or term in content for term in terms)
        if score or not terms:
            matches.append((score, relative.as_posix()))
    matches.sort(key=lambda item: (-item[0], item[1]))
    result = "\n".join(path for _, path in matches) or "No matching C/C++ files found."
    print(f"[TOOL] search_files found {len(matches)} file(s).")
    return result


def search_in_file(file_path: str, query: str) -> str:
    """Find lines in a project file that contain one or more query words."""
    print(f"\n[TOOL] search_in_file(file_path={file_path!r}, query={query!r})")
    path = _inside_project(file_path)
    terms = [word.lower() for word in query.split() if len(word) > 2]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = [
        f"{number}: {line}"
        for number, line in enumerate(lines, start=1)
        if (not terms or any(term in line.lower() for term in terms))
    ]
    if not found:
        # A bug report often describes the expected value, while the source has
        # the incorrect value. Return the file with line numbers as fallback context.
        found = [f"{number}: {line}" for number, line in enumerate(lines, start=1)]
        result = "No exact matches; showing the complete file for context:\n" + "\n".join(found)
    else:
        result = "\n".join(found)
    print(f"[TOOL] search_in_file returned {len(found)} matching line(s).")
    return result


def replace_line(file_path: str, line_number: int, replacement: str) -> str:
    """Replace exactly one numbered line in a project file."""
    print(f"\n[TOOL] replace_line(file_path={file_path!r}, line_number={line_number})")
    path = _inside_project(file_path)
    if line_number < 1:
        return "Replacement failed: line_number must be 1 or greater."
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    if line_number > len(lines):
        return f"Replacement failed: file has only {len(lines)} line(s)."
    ending = "\n" if lines[line_number - 1].endswith("\n") else ""
    lines[line_number - 1] = replacement.rstrip("\r\n") + ending
    path.write_text("".join(lines), encoding="utf-8")
    result = f"Replaced line {line_number} in {path.relative_to(PROJECT_DIR).as_posix()}."
    print(f"[TOOL] {result}")
    return result


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find C/C++ files related to the reported bug by searching filenames and contents.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Bug symptoms or relevant code concepts."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": "Find relevant lines in a C/C++ file and return their 1-based line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Project-relative path from search_files."},
                    "query": {"type": "string", "description": "Bug symptom or code concept to locate."},
                },
                "required": ["file_path", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_line",
            "description": "Replace one complete line at a given 1-based line number in a project file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Project-relative path from search_files."},
                    "line_number": {"type": "integer", "description": "1-based line number to replace."},
                    "replacement": {"type": "string", "description": "The complete replacement line, without a line number."},
                },
                "required": ["file_path", "line_number", "replacement"],
            },
        },
    },
]

AVAILABLE_TOOLS = {
    "search_files": search_files,
    "search_in_file": search_in_file,
    "replace_line": replace_line,
}

SYSTEM_PROMPT = """You are a careful coding agent that fixes simple bugs in C/C++ source files.
Use search_files to identify relevant source files, then search_in_file to inspect the
relevant numbered lines. A bug description may state the expected value, which may not
appear in the source; if a search has no matches, use the returned file context. Make
the smallest justified change with replace_line. Do not modify unrelated lines. Never
claim a fix you did not make. You have at most three model attempts, including
tool-using attempts. If you cannot confidently solve the bug, say so clearly and do not
guess at a code change."""


def _client() -> Groq:
    load_dotenv(REPOSITORY_DIR / ".env")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing from the repository .env or environment.")
    return Groq(api_key=api_key)


def run_agent(bug_description: str, client: Groq | None = None) -> str:
    """Run the tool-calling repair loop with no more than three model attempts."""
    client = client or _client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": bug_description},
    ]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n=== Agent attempt {attempt} of {MAX_ATTEMPTS} ===")
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as exc:
            print(f"[AGENT] The Groq request failed: {exc}")
            break
        message = response.choices[0].message
        messages.append(message)
        if not message.tool_calls:
            answer = message.content or "I couldn't solve the bug within the retry limit."
            print(f"\n[AGENT] {answer}")
            return answer
        print(f"[AGENT] The model requested {len(message.tool_calls)} tool call(s).")
        fix_applied = False
        for call in message.tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
                if name not in AVAILABLE_TOOLS:
                    result = f"Unknown tool: {name}"
                    print(f"[AGENT] Unknown tool requested: {name}")
                else:
                    result = AVAILABLE_TOOLS[name](**arguments)
                    if name == "replace_line" and str(result).startswith("Replaced line "):
                        fix_applied = True
            except Exception as exc:
                result = f"Tool failed: {exc}"
                print(f"[TOOL] {name} failed: {exc}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
        if fix_applied:
            answer = "Applied the requested code change. Review the updated C++ file and compile it to confirm the fix."
            print(f"\n[AGENT] {answer}")
            return answer
    answer = "I couldn't solve the bug within the 3-attempt limit."
    print(f"\n[AGENT] {answer}")
    return answer


def main() -> None:
    if not SAMPLE_CPP.exists():
        SAMPLE_CPP.write_text(
            "#include <iostream>\n"
            "\n"
            "int main() {\n"
            '    std::cout << "Hello from the C++ starter program!\\n";\n'
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        print(f"Created a clean C++ starter file at {SAMPLE_CPP}.")
    print("Simple C++ coding agent (Groq)")
    print(f"Project directory: {PROJECT_DIR}")
    bug_description = input("Describe the bug you want fixed: ").strip()
    if not bug_description:
        print("No bug description was provided.")
        return
    try:
        run_agent(bug_description)
    except RuntimeError as exc:
        print(f"[AGENT] {exc}")


if __name__ == "__main__":
    main()
