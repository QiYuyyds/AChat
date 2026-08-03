'use client'

import { useEffect, useRef } from 'react'

interface Particle {
  x: number
  y: number
  ox: number
  oy: number
  vx: number
  vy: number
}

interface ParticleBackgroundProps {
  colorVar?: string
  particleSize?: number
  spacing?: number
  baseOpacity?: number
}

export function ParticleBackground({
  colorVar = '--primary',
  particleSize = 26,
  spacing = 34,
  baseOpacity = 0.07,
}: ParticleBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const mouseRef = useRef({ x: -9999, y: -9999 })

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    let particles: Particle[] = []
    let animationId = 0
    let color = ''

    function readColor() {
      const tmp = document.createElement('div')
      tmp.style.color = `var(${colorVar})`
      tmp.style.display = 'none'
      document.body.appendChild(tmp)
      const resolved = getComputedStyle(tmp).color
      document.body.removeChild(tmp)
      color = resolved || 'rgb(0,122,255)'
    }

    function resize() {
      const parent = canvas!.parentElement
      if (!parent) return
      const w = parent.clientWidth
      const h = parent.clientHeight
      canvas!.width = Math.max(1, w * dpr)
      canvas!.height = Math.max(1, h * dpr)
      canvas!.style.width = w + 'px'
      canvas!.style.height = h + 'px'
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)

      particles = []
      for (let y = spacing / 2; y < h + spacing; y += spacing) {
        for (let x = spacing / 2; x < w + spacing; x += spacing) {
          particles.push({ x, y, ox: x, oy: y, vx: 0, vy: 0 })
        }
      }
    }

    function animate() {
      const w = canvas!.width / dpr
      const h = canvas!.height / dpr
      ctx!.clearRect(0, 0, w, h)

      const mouse = mouseRef.current
      const repelRadius = 140
      const repelForce = 0.7
      const spring = 0.022
      const damping = 0.87
      const half = particleSize / 2

      for (const p of particles) {
        const dx = p.x - mouse.x
        const dy = p.y - mouse.y
        const dist = Math.sqrt(dx * dx + dy * dy)

        if (dist < repelRadius && dist > 0.1) {
          const force = (1 - dist / repelRadius) * repelForce
          p.vx += (dx / dist) * force
          p.vy += (dy / dist) * force
        }

        p.vx += (p.ox - p.x) * spring
        p.vy += (p.oy - p.y) * spring
        p.vx *= damping
        p.vy *= damping
        p.x += p.vx
        p.y += p.vy

        const disp = Math.abs(p.x - p.ox) + Math.abs(p.y - p.oy)
        const alpha = baseOpacity + Math.min(disp / 80, 0.3)

        ctx!.globalAlpha = alpha
        ctx!.fillStyle = color

        const px = p.x - half
        const py = p.y - half

        ctx!.beginPath()
        if (typeof ctx!.roundRect === 'function') {
          ctx!.roundRect(px, py, particleSize, particleSize, 4)
        } else {
          ctx!.rect(px, py, particleSize, particleSize)
        }
        ctx!.fill()
      }
      ctx!.globalAlpha = 1

      animationId = requestAnimationFrame(animate)
    }

    function handleMouseMove(e: MouseEvent) {
      const rect = canvas!.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      if (x >= -80 && x <= rect.width + 80 && y >= -80 && y <= rect.height + 80) {
        mouseRef.current = { x, y }
      } else {
        mouseRef.current = { x: -9999, y: -9999 }
      }
    }

    readColor()
    resize()
    animate()

    window.addEventListener('resize', resize)
    window.addEventListener('mousemove', handleMouseMove)

    const observer = new MutationObserver(readColor)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    })

    return () => {
      cancelAnimationFrame(animationId)
      window.removeEventListener('resize', resize)
      window.removeEventListener('mousemove', handleMouseMove)
      observer.disconnect()
    }
  }, [colorVar, particleSize, spacing, baseOpacity])

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-0"
      aria-hidden="true"
    />
  )
}
