import { useEffect, useRef } from 'react'
import { mount, register } from 'pele/core'
import flowchart from 'pele/diagrams/flowchart'

register(flowchart)

export function MermaidDiagram({ source }: { source: string }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    // Only repository-authored Mermaid is rendered; Pele handles SVG generation.
    const diagram = mount(host.current, source, {
      maxWidth: 924,
      autoDirection: false,
      config: { flowchart: { nodeSpacing: 16, rankSpacing: 20 } },
      fontSize: 24,
      padding: 8,
      zoom: false,
    })
    return () => diagram.destroy()
  }, [source])
  return <div ref={host} className="diagram" />
}
