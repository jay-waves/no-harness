# Remote REPL presentation

The deck follows [the project proposal](../proposal.md). It uses reveal.js with the official React bindings, TypeScript, Markdown, Vite, and the Paul Tol Muted palette. Pele renders Mermaid diagrams and KaTeX supports build-time display math.

Use Node.js 22.12+ or a newer supported release with npm.

```sh
npm ci
npm run typecheck
npm run build
npm run build:single
npm run preview
```

Use `npm run dev` for authoring. The Pages build in `dist/` uses relative paths and hash navigation. Open `dist-single/index.html` directly to use the standalone build.

Edit `slides.md` for slide content, `---` separators, and `Notes:` speaker notes. Use `<!-- diagram: name.mmd -->` on diagram slides. Vite bundles the shared root Mermaid files into both builds. Use standalone `$$` fences for display math. Global typography, spacing, and palette aliases live in `styles/index.css`.

The [root README](../README.md) documents CI, Pages, and tag releases.
