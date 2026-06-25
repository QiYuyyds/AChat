'use client'

export type UiCommand = 'open-settings' | 'open-agents'

const UI_COMMAND_EVENT = 'agenthub:ui-command'

export function emitUiCommand(command: UiCommand): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(UI_COMMAND_EVENT, { detail: { command } }))
}

export function subscribeUiCommand(handler: (command: UiCommand) => void): () => void {
  if (typeof window === 'undefined') return () => {}

  const listener = (event: Event) => {
    const command = (event as CustomEvent<{ command?: UiCommand }>).detail?.command
    if (command) handler(command)
  }

  window.addEventListener(UI_COMMAND_EVENT, listener)
  return () => window.removeEventListener(UI_COMMAND_EVENT, listener)
}
