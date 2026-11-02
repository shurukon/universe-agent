# Universe Agent GitHub upload scope

This repository contains the Universe Agent source code, core agent/orchestrator implementation, provider management, memory layers, connectors, skills taxonomy and loader, schemas/migrations, tests, documentation, and static skill data.

The upload intentionally excludes local runtime state and generated benchmark artifacts, including Python caches, pytest/ruff caches, temporary directories, rendered frames, output directories, probe artifacts, local project workspaces, databases, SQLite files, JSONL timelines, logs, compiled bytecode, virtual environments, Node modules, and build outputs. API keys, Personal Access Tokens, environment files, certificates, and private key files are also excluded.

The excluded files are local execution state rather than source required to install and run Universe Agent. Credentials must be supplied through the documented configuration flow at runtime and must never be committed.
