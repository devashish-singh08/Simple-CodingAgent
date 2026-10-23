# Simple C++ Coding Agent

A small Groq-powered CLI agent for fixing simple bugs in C/C++ source files. It uses
three tools: `search_files`, `search_in_file`, and `replace_line`. The agent makes at
most three model attempts and prints each tool call and its result.

## Run

Set `GROQ_API_KEY` in the repository-level `.env` file or your environment, then run
from this directory:

```powershell
uv run codingagent
```

The first run creates `buggy_program.cpp` if it does not exist. Add your bug to that
file, run the command, and describe the bug when prompted. The default model is
`openai/gpt-oss-120b`; set `GROQ_MODEL` to select another Groq-supported model.
