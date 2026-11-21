import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import katex from 'katex'
import { viteSingleFile } from 'vite-plugin-singlefile'

// Only the authored deck is transformed. KaTeX never enters the browser module graph.
function prerenderMath(): Plugin {
  return {
    name: 'prerender-slide-math',
    enforce: 'pre',
    load(id) {
      if (!id.endsWith('/slides.md?raw')) return
      const path = id.slice(0, -4)
      this.addWatchFile(path)
      const markdown = readFileSync(path, 'utf8')
      // Keep code fences (including Mermaid) literal; display math uses standalone $$ lines.
      const rendered = markdown.replace(/(^```[^\n]*\n[\s\S]*?^```\s*$)|^\$\$\r?\n([\s\S]*?)^\$\$\s*$/gm, (block, fence, formula) => {
        if (fence) return block
        return `<div class="equation">${katex.renderToString(formula.trim(), {
          displayMode: true,
          output: 'htmlAndMathml',
          throwOnError: true,
          strict: 'error',
          trust: false,
        })}</div>\n`
      })
      return `export default ${JSON.stringify(rendered)}`
    },
    transform(code, id) {
      if (!id.replaceAll('\\', '/').endsWith('/katex/dist/katex.css')) return
      // Supported modern browsers use WOFF2; avoid shipping duplicate WOFF/TTF fonts.
      return code.replace(/,\s*url\([^)]*\.(?:woff|ttf)\)\s*format\("[^"]*"\)/g, '')
    },
  }
}

export default defineConfig(({ mode }) => ({
  server: { fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] } },
  base: './',
  plugins: [prerenderMath(), react(), ...(mode === 'single' ? [viteSingleFile()] : [])],
  build: { outDir: mode === 'single' ? 'dist-single' : 'dist' },
}))
