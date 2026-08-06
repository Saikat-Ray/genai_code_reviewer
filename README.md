# genai_code_reviewer

A lightweight prototype inspired by Uber's uReview blog post for AI-assisted code review.

## What this tool does

- Reads a Git diff from the current repository
- Extracts changed file hunks for source files
- Uses OpenAI chat completions to generate review comments
- Grades review comments with a second prompt for confidence
- Filters duplicate and low-confidence comments

## Features

- `standard` assistant: catches bugs, logic issues, error handling, and correctness problems
- `best_practices` assistant: suggests maintainability, naming, performance, and style improvements
- CLI output with line ranges and confidence scores

## Setup

```bash
cd /Users/admin/Documents/rag/genai_code_reviewer
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set your OpenAI key:

```bash
export OPENAI_API_KEY="your_api_key"
```

## Usage

Review the current unstaged diff:

```bash
python reviewer.py review --base HEAD
```

Review staged changes:

```bash
python reviewer.py review --staged
```

Review with the best practices assistant:

```bash
python reviewer.py review --assistant best_practices
```

Print JSON output:

```bash
python reviewer.py review --output-json
```

## Notes

This prototype is intentionally simple. It is meant to demonstrate the architecture of a GenAI code review tool:

1. Ingestion and preprocessing
2. Comment generation by specialized assistants
3. Quality grading and filtering
4. Actionable delivery for developers
