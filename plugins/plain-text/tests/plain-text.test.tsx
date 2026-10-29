import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

const PANE = {
  plugin: 'plain-text',
  component: 'Pane',
  requestId: 'plain-text',
  props: {
    title: 'Plain text',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const

const USAGE = {
  input_tokens: 1,
  output_tokens: 1,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
}

const LONG_REPLY =
  'Work stopped. Task 255 has the status in_progress and waits for four answers from Human.'

const JEV_STDOUT = JSON.stringify(
  {
    answers: {
      clarity: { choice: 'simplify' },
      relevance: { choice: 'focused' },
      ste: { choice: 'non_compliant' },
    },
  },
  null,
  2,
)

const turn = (turnId: string, answer: string) => ({
  answer,
  turnId,
  durationMs: 1,
  isAborted: false,
  reason: 'answer' as const,
})

const WITH_KEY = { HOME: '/home/human', OPENROUTER_API_KEY: 'test-key' }
const NO_KEY = { HOME: '/home/human' }
const ZSHRC = "export OPENROUTER_API_KEY='zshrc-key'"

const engine = (on: On, env: Record<string, string> = WITH_KEY, zshrc = ZSHRC) => {
  const clock = mock.clock(on)
  mock.env(on, env)
  on('command.register', () => ({ value: { command: 'plain-text' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.messages', () => ({
    value: [
      { role: 'user', text: 'Why did the work stop?', toolUses: [] },
      { role: 'assistant', text: LONG_REPLY, toolUses: [] },
    ],
  }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('fs.read', (_, e) => ({
    value: e.path.endsWith('/.zshrc') ? zshrc : `rules from ${e.path}`,
  }))

  return clock
}

const jevAnswers = (on: On, stdout = JEV_STDOUT) => {
  const runs: { argv: readonly string[]; stdin: string; env?: Record<string, string> }[] = []
  on('process.run', (_, e) => {
    runs.push({ argv: e.argv, stdin: e.init?.stdin ?? '', env: e.init?.env })

    return {
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })

  return runs
}

const drawn = async ($: Engine, surface: (typeof SURFACES)[number]) => {
  const ui = await $.ui.mount({ ...PANE, surface })
  const markdown = await ui.find({ type: 'Markdown' })
  const jev = await ui.find({ type: 'Text', text: /Jev/ })
  await ui.unmount()

  return { markdown: markdown?.props.text, jev: jev?.text }
}

test('the pane shows the Haiku reply and the Jev line', async ($, on) => {
  const clock = engine(on)
  const runs = jevAnswers(on)
  const asks: { model: string; prompt: string; system?: string }[] = []
  on('model.complete', (_, e) => {
    asks.push({ model: e.model, prompt: e.prompt, system: e.system })

    return { value: { isAnswered: true, text: 'Agent stopped the work.', usage: USAGE } }
  })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.complete(turn('t1', LONG_REPLY))
  await clock.settle()

  expect(asks[0]?.model).toBe('haiku')
  expect(asks[0]?.prompt).toContain(LONG_REPLY)
  expect(asks[0]?.prompt).toContain('Why did the work stop?')
  expect(asks[0]?.system).toContain('rules from /home/human/.claude/skills/plain-english/SKILL.md')
  expect(runs[0]?.argv).toEqual([
    '/bin/bash',
    '/home/human/.claude/skills/ask-jev/scripts/ask_jev.sh',
    '/dev/stdin',
  ])
  expect(runs[0]?.env).toEqual({ OPENROUTER_API_KEY: 'test-key' })
  expect(runs[0]?.stdin).toContain('in_progress')
  for (const surface of SURFACES) {
    expect(await drawn($, surface)).toEqual({
      markdown: 'Agent stopped the work.',
      jev: 'Jev on the Agent reply: clarity=simplify, ste=non_compliant',
    })
  }
})

test('a short reply is shown as it is, with no Haiku and no Jev', async ($, on) => {
  const clock = engine(on)
  const runs = jevAnswers(on)
  let asks = 0
  on('model.complete', () => {
    asks += 1

    return { value: { isAnswered: true, text: 'unused', usage: USAGE } }
  })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.complete(turn('t1', 'Yes.'))
  await clock.settle()

  expect(asks).toBe(0)
  expect(runs.length).toBe(0)
  expect(await drawn($, 'desktop')).toEqual({ markdown: 'Yes.', jev: undefined })
})

test('late results do not replace a newer reply', async ($, on) => {
  const clock = engine(on)
  on('process.run', async () => {
    await clock.sleep(100)

    return {
      value: { exitCode: 0, stdout: JEV_STDOUT, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
  on('model.complete', async () => {
    await clock.sleep(100)

    return { value: { isAnswered: true, text: 'old rewrite', usage: USAGE } }
  })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.complete(turn('t1', LONG_REPLY))
  await clock.settle()
  await $.turn.complete(turn('t2', 'No.'))
  await clock.advance(100)

  expect(await drawn($, 'terminal')).toEqual({ markdown: 'No.', jev: undefined })
})

test('failures show their reasons and the original reply', async ($, on) => {
  const clock = engine(on)
  on('process.run', () => ({
    value: {
      exitCode: 1,
      stdout: '',
      stderr: 'OpenRouter HTTP 401; no automatic retry',
      isStdoutTruncated: false,
      isStderrTruncated: false,
    },
  }))
  on('model.complete', () => ({
    value: { isAnswered: false, reason: 'empty-reply', usage: USAGE },
  }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.complete(turn('t1', LONG_REPLY))
  await clock.settle()

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /empty-reply/ })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: /Jev/ }))?.text).toBe(
    'Jev: no answer (OpenRouter HTTP 401; no automatic retry)',
  )
  expect((await ui.find({ type: 'Markdown' }))?.props.text).toBe(LONG_REPLY)
  await ui.unmount()
})

test('Jev reads the key from .zshrc when the environment has none', async ($, on) => {
  const clock = engine(on, NO_KEY)
  const runs = jevAnswers(on)
  on('model.complete', () => ({
    value: { isAnswered: true, text: 'Agent stopped the work.', usage: USAGE },
  }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.turn.complete(turn('t1', LONG_REPLY))
  await clock.settle()

  expect(runs[0]?.env).toEqual({ OPENROUTER_API_KEY: 'zshrc-key' })
  expect((await drawn($, 'terminal')).jev).toBe(
    'Jev on the Agent reply: clarity=simplify, ste=non_compliant',
  )
})

test('Jev does not run without a key', async ($, on) => {
  const clock = engine(on, NO_KEY, '')
  const runs = jevAnswers(on)
  on('model.complete', () => ({
    value: { isAnswered: true, text: 'Agent stopped the work.', usage: USAGE },
  }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.turn.complete(turn('t1', LONG_REPLY))
  await clock.settle()

  expect(runs.length).toBe(0)
  expect((await drawn($, 'terminal')).jev).toBe('Jev: no answer (OPENROUTER_API_KEY is not set)')
})
