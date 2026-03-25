import { lazy, Suspense, type ComponentType } from 'react'
import remarkGfm from 'remark-gfm'
import type { Options } from 'react-markdown'

const MD = lazy(async () => {
  const mod = await import('react-markdown')
  return { default: mod.default as ComponentType<Options> }
})

export function MarkdownLazy({ text }: { text: string }) {
  return (
    <Suspense fallback={<pre className="msg-fallback">{text}</pre>}>
      <div className="md-body markdown-rendered">
        <MD remarkPlugins={[remarkGfm]}>{text}</MD>
      </div>
    </Suspense>
  )
}
