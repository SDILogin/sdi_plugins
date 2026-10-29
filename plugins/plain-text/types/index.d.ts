export type PlainTextView = {
  turnId: string
  status: 'working' | 'done' | 'failed'
  text: string
  reason?: string
  jev?: string
}

declare module 'claude-code' {
  interface PluginState {
    'plain-text': { view: PlainTextView | null }
  }
}
