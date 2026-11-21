# Lightweight Remote Execution for a Local LLM Agent

COMP7607A Natural Language Processing Project

[proposal.md](proposal.md) defines the project. One local agent will manage persistent Python REPL sessions on SSH, Docker, and WSL targets. The evaluation will compare direct commands, a full agent per target, and persistent remote REPLs across 24 tasks with three repetitions per configuration.

The proposal links to the Mermaid sources rendered in the presentation.

The [SSH MCP prototype](ssh-mcp/README.md) supports persistent Bash and Python sessions on Linux, a version handshake with remote system metadata, and SFTP file copying. The [report](REPORT.md) records public harness resource measurements and a loopback SSH pilot. The [course presentation](presentation/README.md) covers the proposed design. Docker and WSL adapters and evaluation across hosts are planned.

## GitHub Actions

- CI typechecks and builds the standalone presentation and tests the SSH MCP implementation on pushes and pull requests.
- Presentation Pages validates changes to `presentation/`, `proposal.md`, root `*.mmd` diagrams, and the Pages workflow. Successful builds on `main`, including manual runs, can deploy `presentation/dist/`. Set the repository's Pages source to GitHub Actions and allow `main` in the `github-pages` environment.
- Release runs for `v*` tags and publishes the standalone `remote-repl-presentation.html` and `SHA256SUMS`.
