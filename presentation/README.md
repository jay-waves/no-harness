# Remote REPL presentation

The deck follows [the project proposal](../proposal.md). It uses reveal.js with the official React bindings, TypeScript, Markdown, Vite, and the Paul Tol Muted palette. Vite renders D2 diagrams and KaTeX display math at build time.

Use Node.js 22.12+ or a newer supported release with npm.

```sh
npm ci
npm run typecheck
npm run build
npm run build:single
npm run preview
```

Use `npm run dev` for authoring. The Pages build in `dist/` uses relative paths and hash navigation. Open `dist-single/index.html` directly to use the standalone build.

Edit `slides.md` for slide content, `---` separators, and `Notes:` speaker notes. Use `<!-- diagram: name.d2 -->` and `<!-- alt: Diagram description -->` on diagram slides. Edit the deck diagrams in `diagrams/` and the proposal diagrams in the root Mermaid files. Both deck builds include the rendered SVGs and their icons. Use standalone `$$` fences for display math. Global typography, spacing, and palette aliases live in `styles/index.css`.

The [root README](../README.md) documents CI, Pages, and tag releases.

The deck uses the [OpenAI](https://openai.com/favicon.svg) mark for Codex and the [Anthropic](https://www.anthropic.com/favicon.ico) mark for Claude Code. The [OpenCode](https://opencode.ai/brand) and [DeepSeek](https://www.deepseek.com/favicon.ico) marks come from their official sites. Docker and Linux icons are from [Simple Icons](https://simpleicons.org/), and the WSL icon is from [Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/media/wsl-icon.svg). The Grok mark is from [TheSVG](https://thesvg.org/icon/grok-xai), and the nanobot mark is from the [HKUDS project repository](https://github.com/HKUDS/nanobot/blob/main/images/nanobot_mark.svg). The REPL loop uses [Lucide Refresh CW](https://lucide.dev/icons/refresh-cw).
