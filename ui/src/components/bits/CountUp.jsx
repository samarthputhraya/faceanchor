import { useEffect, useRef, useState } from 'react'

/**
 * Animates a number from 0 to `to`. Used for similarity scores so the value
 * visibly settles rather than appearing fully formed.
 * Adapted from the React Bits CountUp pattern (MIT).
 */
export default function CountUp({ to, decimals = 4, duration = 700, className = '' }) {
  const [value, setValue] = useState(0)
  const frame = useRef()

  useEffect(() => {
    const start = performance.now()
    const target = Number(to) || 0
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration)
      // ease-out cubic
      setValue(target * (1 - Math.pow(1 - t, 3)))
      if (t < 1) frame.current = requestAnimationFrame(tick)
    }
    frame.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame.current)
  }, [to, duration])

  return <span className={className}>{value.toFixed(decimals)}</span>
}
