import { useRef, useState } from 'react'
import type { RevealApi } from 'reveal.js'
import { createRoot } from 'react-dom/client'
import { Deck, Markdown, Slide } from '@revealjs/react'
import 'reveal.js/reveal.css'
import 'katex/dist/katex.css'
import '../styles/index.css'
import content from '../slides.md?raw'
import { MemoryFootprint } from './MemoryFootprint'
import { CommandTokens } from './CommandTokens'
import { CodexBundle } from './CodexBundle'
const diagrams = import.meta.glob('../diagrams/*.d2', { eager: true, query: '?diagram', import: 'default' })
const pages = content.trim().split(/^---\s*$/m)
const charts = { 'command-tokens': CommandTokens, 'memory-footprint': MemoryFootprint, 'codex-bundle': CodexBundle }

function Presentation() {
  const deck = useRef<RevealApi | null>(null)
  const [current, setCurrent] = useState(1)
  return <><Deck deckRef={deck} onReady={api => setCurrent(api.getIndices().h + 1)} onSlideChange={() => setCurrent((deck.current?.getIndices().h ?? 0) + 1)} config={{ width: 980, height: 552, hash: true, center: false, transition: 'none', slideNumber: false, controls: false }}>
    {pages.map((page, index) => {
      const diagram = page.match(/^<!-- diagram: ([\w-]+\.d2) -->$/m)
      const chart = page.match(/^<!-- chart: (memory-footprint|codex-bundle|command-tokens) -->$/m)
      if (chart) {
        const Chart = charts[chart[1] as keyof typeof charts]
        const [body, notes] = page.split('Notes:')
        const title = body.match(/^# (.+)$/m)?.[1]
        const caption = body.match(/<p class="caption">(.+?)<\/p>/)?.[1]
        return <Slide key={index} notes={notes?.trim()}>
          <h1>{title}</h1>
          <Chart />
          {caption && <p className="caption">{caption}</p>}
        </Slide>
      }
      if (!diagram) return <Markdown key={index}>{page}</Markdown>
      const source = diagrams[`../diagrams/${diagram[1]}`]
      if (typeof source !== 'string') throw new Error(`Missing diagram source: ${diagram[1]}`)
      const title = page.match(/^# (.+)$/m)?.[1] ?? 'Diagram'
      const eyebrow = page.match(/<p class="eyebrow">(.+?)<\/p>/)?.[1]
      const caption = page.match(/<p class="caption">(.+?)<\/p>/)?.[1]
      return <Slide key={index} notes={page.split('Notes:')[1]?.trim()}>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        <img className="diagram" src={source} alt={page.match(/^<!-- alt: (.+) -->$/m)?.[1] ?? title} />
        {caption && <p className="caption">{caption}</p>}
      </Slide>
    })}
  </Deck>
    <nav className="deck-navigation" aria-label="Slide navigation">
      <button aria-label="Previous slide" disabled={current === 1} onClick={() => deck.current?.prev()}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 3-5 5 5 5" /></svg>
      </button>
      <output aria-label="Current slide" aria-live="polite">{current}<span> / {pages.length}</span></output>
      <button aria-label="Next slide" disabled={current === pages.length} onClick={() => deck.current?.next()}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>
      </button>
    </nav>
  </>
}

createRoot(document.getElementById('root')!).render(<Presentation />)
