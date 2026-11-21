# Lightweight Remote Execution for a Local LLM Agent

COMP7607A Natural Language Processing Project

[proposal.md](proposal.md) defines the project. One local agent will manage persistent Python REPL sessions on SSH, Docker, and WSL targets. The evaluation will compare direct commands, a full agent per target, and persistent remote REPLs across 24 tasks with three repetitions per configuration.

The proposal links to the Mermaid sources rendered in the presentation.

The repository contains the proposal, its [course presentation](presentation/README.md), and an initial [SSH MCP implementation](ssh-mcp/README.md) with persistent Bash sessions and SFTP file copying. The proposed persistent Python REPL, Docker and WSL adapters, reproducible tasks, and evaluation remain planned deliverables.

## GitHub Actions

- CI typechecks and builds the standalone presentation and tests the SSH MCP implementation on pushes and pull requests.
- Presentation Pages validates changes to `presentation/`, `proposal.md`, root `*.mmd` diagrams, and the Pages workflow. Successful builds on `main`, including manual runs, can deploy `presentation/dist/`. Set the repository's Pages source to GitHub Actions and allow `main` in the `github-pages` environment.
- Release runs for `v*` tags and publishes the standalone `remote-repl-presentation.html` and `SHA256SUMS`.
