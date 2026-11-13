## Local model (`ask_local`)

A local qwen is free but weaker than you. Use it to avoid *reading* bulk material, not to think.

Delegate: summarizing or extracting from large files/logs/diffs (pass `paths`, never paste content),
docstrings, boilerplate, commit messages, format conversion, "does X appear in these files".
Ask for bounded output ("max 5 bullets", "one line per file"), and give a self-contained task:
qwen cannot see this conversation.

Do it yourself: design, debugging, security, anything you will ship unchecked, edits, and small
inputs (under ~200 lines is cheaper to just read; each call has overhead).

Always sanity-check the answer against what you know, and heed any `NOTE:` line. It is weak at exact
de-duplication and counting. If the answer is poor, do the task yourself; do not retry more than once.
