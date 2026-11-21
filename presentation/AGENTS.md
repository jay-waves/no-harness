# Presentation subproject guidance

- The deck follows the repository’s `proposal.md`.
- Stack: reveal.js with @revealjs/react, React + TypeScript, Markdown and global CSS, built with Vite. Do not introduce Vue, Slidev, bridge components, or a second SPA/router.
- HTML static output is primary: `npm run build` produces `dist/`. Keep assets local and use relative paths. PDF is optional.
- Use npm with the committed `package-lock.json`.
- Use only Paul Tol Muted chromatic colors. Source: https://sronpersonalpages.nl/~pault/ (Muted scheme). White and dark neutral text are allowed for readability.
- Palette: indigo #332288, cyan #88CCEE, teal #44AA99, green #117733, olive #999933, sand #DDCC77, rose #CC6677, wine #882255, purple #AA4499, grey #DDDDDD.
- Define raw colors only in styles/index.css under --tol-<name>. Components use semantic aliases: --color-background, --color-text, --color-primary, --color-accent, --color-success, --color-warning, --color-danger, --color-unknown, --color-border, --color-surface, --color-on-primary. Add aliases centrally when needed.
- No inline hex/rgb/hsl colors or framework color utilities. Charts read CSS variables and use palette names for category aliases. Unknown is grey and must also carry a text label; never communicate meaning by color alone.
- Use dark text on light palette fills; do not assume every palette pair has sufficient text contrast. Preserve keyboard focus and reduced-motion support.
- Global typography, spacing and shared visual rules belong in styles/index.css. Use 16:9 slides, one main point per slide, reusable components for repeated structures.
- Use synthetic examples or reviewed data; never include credentials or raw unreviewed traces. Label illustrative data, unknown metrics and unmeasured results.
- Validate with `npm ci`, `npm run typecheck`, `npm run build`; manually inspect HTML for overflow and interactions.

- Use @visx/shape and @visx/scale for charts. Keep fabricated fixtures labeled visibly on every chart and in accessible source tables. Define chart series aliases centrally; never present fixtures as measured results.

## Keep the presentation small

- Follow the root development, CI/CD, and Git rules. Keep presentation-specific changes within this directory, except its workflow and shared proposal documentation and diagram sources.
- Keep slides.md as the deck entry; split chapters only when the deck becomes difficult to maintain. Do not create empty pages/layouts/data directories or one component per static slide.
- Use Markdown for static content and React components for the deck and meaningful interaction. Reuse chart primitives when that reduces duplication; do not build a general visualization framework.
- Keep fabricated values in one clearly identified source per dataset. Do not copy the same dataset into slides, chart code, and documentation. Keep source tables generated from the plotted values.
- Retain the existing npm lockfile and minimal visx packages. Do not add another chart library, CSS framework, package manager, or build pipeline without a concrete requirement.
- Keep generated HTML, exported PDFs, screenshots, and node_modules ignored. Commit authored source and local assets only.

## Presentation CI and delivery

- The presentation workflow must run npm ci, npm run typecheck, and npm run build. PRs validate only; deployment uploads only presentation/dist from main.
- Preserve relative asset paths and hash routing for GitHub Pages repository hosting. No runtime API, secret, or external font should be required for the demo.
- Review rendered slides after layout or chart changes: overflow, text size, labels, palette aliases, keyboard interaction, and visible simulated-data labels. State explicitly if browser inspection was unavailable.
- Keep proposal content and measured results distinct. Replacing fixtures with real data requires documented provenance, units, sample size, configuration, and corresponding README/proposal updates.


- Use --font-presentation (Georgia with serif fallbacks) throughout, including SVG labels.

- Render Mermaid through Pele in React, with `<!-- diagram: name.mmd -->` references in `slides.md` to the shared root Mermaid files. Register only the diagram types in use, preserve mount cleanup, and map --pele-* variables to global palette/font aliases. Do not add Mermaid.js or a second diagram renderer. Keep every diagram legible within the slide and provide source disclosure.
