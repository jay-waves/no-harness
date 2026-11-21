import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import katex from 'katex'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { D2 } from '@d2lang/d2'

const asset = (name: string) => fileURLToPath(new URL(`./assets/${name}`, import.meta.url))
const diagramTheme = fileURLToPath(new URL('./diagrams/theme.d2', import.meta.url))
const palettePath = fileURLToPath(new URL('./styles/index.css', import.meta.url))

function iconData(name: string): string {
  const type = name.endsWith('.svg') ? 'image/svg+xml' : 'image/png'
  return `data:${type};base64,${readFileSync(asset(name)).toString('base64')}`
}

function renderDiagrams(): Plugin {
  return {
    name: 'render-d2-diagrams',
    enforce: 'pre',
    async load(id) {
      if (!id.endsWith('.d2?diagram')) return
      const path = id.slice(0, -8)
      this.addWatchFile(path)
      this.addWatchFile(diagramTheme)
      this.addWatchFile(palettePath)
      const styles = readFileSync(palettePath, 'utf8')
      const colors = Object.fromEntries([...styles.matchAll(/--tol-(\w+): (#\w+);/g)].map(([, name, color]) => [name, color]))
      const theme = readFileSync(diagramTheme, 'utf8').replace(/#@(\w+)/g, (_, name: string) => {
        if (!colors[name]) throw new Error(`Missing D2 palette color: ${name}`)
        return colors[name]
      })
      const renderer = new D2()
      try {
        const compiled = await renderer.compile(theme + '\n' + readFileSync(path, 'utf8'), {
          layout: 'elk', pad: 16, noXMLTag: true,
        })
        const svg = (await renderer.render(compiled.diagram, compiled.renderOptions))
          .replace(/https:\/\/deck-icons\.invalid\/([\w-]+\.(?:svg|png))/g, (_, name: string) => {
            this.addWatchFile(asset(name))
            return iconData(name)
          })
          .replace(/@font-face\s*\{[^}]*\}/g, '')
          .replace(/<svg\b[^>]*>/, tag => `${tag}<style>text { font-family: Georgia, "Times New Roman", serif !important; }</style>`)
        return `export default ${JSON.stringify(`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`)}`
      } finally {
        await renderer.dispose()
      }
    },
  }
}

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
      // Keep code fences literal; display math uses standalone $$ lines.
      const branded = markdown.replace(/brand-icon:([\w-]+\.(?:svg|png))/g, (_, name: string) => {
        this.addWatchFile(asset(name))
        return iconData(name)
      })
      const rendered = branded.replace(/(^```[^\n]*\n[\s\S]*?^```\s*$)|^\$\$\r?\n([\s\S]*?)^\$\$\s*$/gm, (block, fence, formula) => {
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
  plugins: [renderDiagrams(), prerenderMath(), react(), ...(mode === 'single' ? [viteSingleFile()] : [])],
  build: { outDir: mode === 'single' ? 'dist-single' : 'dist' },
}))
