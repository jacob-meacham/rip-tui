# Claude Code Instructions for rip

## Constitution

This project follows the engineering constitution at `/home/jacob/code/agent-instructions/coding/constitution.md`.

Key principles:
- **Simplicity over cleverness** - Write obvious code
- **Explicit error handling** - Never swallow errors, always log context
- **Single responsibility** - Functions should do one thing well
- **Test everything that matters** - New code requires tests
- **Document public APIs** - Complex logic needs comments

## Python Reference

This project follows the Python reference architecture at `/home/jacob/code/agent-instructions/coding/python-reference.md`.

Key rules:
- **Always use `uv`** as the package manager and task runner (never pip, virtualenv, or python -m directly)
- **`uv run`** to execute Python code — never `python` directly
- **`uv add`** to add dependencies — never `pip install`
- **`ruff`** for both linting and formatting (replaces black, isort, flake8)
- **`pyright`** for type checking
- **`pytest`** for testing (never unittest directly)

## Codebase

This is a 4K Blu-ray ripping utility for Emby media servers, implemented as a Python TUI application using Textual. It:
- Uses `makemkvcon` (via python-makemkv) to rip discs
- Organizes output into Emby-compatible folder structures
- Supports single-disc movies, multi-disc movies, and TV shows
- Handles extras classification into Emby categories
- Integrates with TMDb for intelligent metadata lookup
- Uses RapidFuzz for fuzzy title matching

## Code Quality Standards

When working on this codebase:
1. Use `uv run` for all execution (`uv run pytest`, `uv run ruff check .`, etc.)
2. Log all errors with context
3. Validate inputs before processing
4. Keep functions focused and < 50 lines
5. Use meaningful variable names
6. Add comments for complex logic
7. Type annotate all public functions

## Skills

Skills are available in `.claude/skills/` for code review workflows.
