import { useEffect, useState } from 'react'

const GLYPHS = '0123456789abcdef'

/**
 * Scrambles then resolves a string, character by character. Applied to hashes
 * and transaction ids so the moment a value is committed reads as an event.
 * Adapted from the React Bits DecryptedText pattern (MIT).
 */
export default function DecryptedText({ text = '', speed = 22, className = '' }) {
  const [shown, setShown] = useState('')

  useEffect(() => {
    if (!text) { setShown(''); return }
    let revealed = 0
    const id = setInterval(() => {
      revealed += 1
      if (revealed > text.length) { clearInterval(id); setShown(text); return }
      const head = text.slice(0, revealed)
      const tail = Array.from(text.slice(revealed))
        .map(() => GLYPHS[Math.floor(Math.random() * GLYPHS.length)])
        .join('')
      setShown(head + tail)
    }, speed)
    return () => clearInterval(id)
  }, [text, speed])

  return <span className={className}>{shown}</span>
}
