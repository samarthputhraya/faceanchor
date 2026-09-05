import { useRef } from 'react'

/**
 * A card that lights up under the cursor.
 * Adapted from the React Bits SpotlightCard pattern (MIT).
 */
export default function SpotlightCard({ children, className = '', glow = '76, 194, 255' }) {
  const ref = useRef(null)

  const onMove = (e) => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${e.clientX - r.left}px`)
    el.style.setProperty('--my', `${e.clientY - r.top}px`)
    el.style.setProperty('--op', '1')
  }
  const onLeave = () => ref.current?.style.setProperty('--op', '0')

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      className={`relative overflow-hidden rounded-xl border border-edge bg-panel/70 ${className}`}
    >
      <div
        className="pointer-events-none absolute inset-0 transition-opacity duration-300"
        style={{
          opacity: 'var(--op, 0)',
          background: `radial-gradient(320px circle at var(--mx) var(--my), rgba(${glow}, 0.14), transparent 45%)`,
        }}
      />
      <div className="relative">{children}</div>
    </div>
  )
}
