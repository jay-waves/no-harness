import { lazy, Suspense, useRef, useState } from 'react'
import type { RevealApi } from 'reveal.js'
import { createRoot } from 'react-dom/client'
import { Deck, Markdown, Slide } from '@revealjs/react'
import 'reveal.js/reveal.css'
import 'katex/dist/katex.css'
import '../styles/index.css'
import content from '../slides.md?raw'
const MermaidDiagram = lazy(() => import('./MermaidDiagram').then(module => ({ default: module.MermaidDiagram })))

// Vite bundles the same diagram sources used by the proposal.
const diagrams = import.meta.glob('../../*.mmd', { eager: true, query: '?raw', import: 'default' })
const pages = content.trim().split(/^---\s*$/m)

function Presentation() {
  const deck = useRef<RevealApi | null>(null)
  const [current, setCurrent] = useState(1)
  return <><Deck deckRef={deck} onReady={api => setCurrent(api.getIndices().h + 1)} onSlideChange={() => setCurrent((deck.current?.getIndices().h ?? 0) + 1)} config={{ width: 980, height: 552, hash: true, center: false, transition: 'none', slideNumber: false, controls: false }}>
    {pages.map((page, index) => {
      const diagram = page.match(/^<!-- diagram: ([\w-]+\.mmd) -->$/m)
      if (!diagram) return <Markdown key={index}>{page}</Markdown>
      const source = diagrams[`../../${diagram[1]}`]
      if (typeof source !== 'string') throw new Error(`Missing diagram source: ${diagram[1]}`)
      const title = page.match(/^# (.+)$/m)?.[1] ?? 'Diagram'
      const eyebrow = page.match(/<p class="eyebrow">(.+?)<\/p>/)?.[1]
      const caption = page.match(/<p class="caption">(.+?)<\/p>/)?.[1]
      return <Slide key={index} notes={page.split('Notes:')[1]?.trim()}>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <Suspense fallback={<p role="status">Loading diagram…</p>}><MermaidDiagram source={source.trim()} /></Suspense>
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
