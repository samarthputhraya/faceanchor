import { motion } from 'motion/react'

/**
 * Horizontal progress rail across the five pipeline stages.
 * Adapted from the React Bits Stepper pattern (MIT).
 */
export default function Stepper({ steps, current, failed = -1 }) {
  return (
    <div className="flex items-center gap-2 w-full">
      {steps.map((label, i) => {
        const done = i < current
        const active = i === current
        const bad = i === failed
        const color = bad ? 'var(--color-bad)'
          : done ? 'var(--color-good)'
          : active ? 'var(--color-accent)' : 'var(--color-edge)'
        return (
          <div key={label} className="flex items-center gap-2 flex-1 last:flex-none">
            <div className="flex flex-col items-center gap-1.5 min-w-24">
              <motion.div
                className="grid h-8 w-8 place-items-center rounded-full border-2 text-xs font-semibold"
                style={{ borderColor: color, color }}
                animate={active ? { scale: [1, 1.12, 1] } : { scale: 1 }}
                transition={active ? { repeat: Infinity, duration: 1.6 } : {}}
              >
                {bad ? '!' : done ? '✓' : i + 1}
              </motion.div>
              <span
                className="text-[11px] uppercase tracking-wider"
                style={{ color: active || done || bad ? color : 'var(--color-mute)' }}
              >
                {label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className="h-px flex-1 bg-edge relative overflow-hidden">
                <motion.div
                  className="absolute inset-y-0 left-0"
                  style={{ background: 'var(--color-good)' }}
                  initial={{ width: 0 }}
                  animate={{ width: done ? '100%' : '0%' }}
                  transition={{ duration: 0.45 }}
                />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
