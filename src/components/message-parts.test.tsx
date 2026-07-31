// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { PartList } from '@/components/message-parts'
import type { MessagePart } from '@/shared/types'

describe('TextPart streaming fallback', () => {
  it('renders <pre> fallback when isStreaming=true', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Hello\n\nThis is **markdown** content' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, content should be in a <pre> element (plain text fallback)
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('# Hello')
    expect(pre).toHaveTextContent('This is **markdown** content')

    // The pre should use font-sans (not font-mono) to match markdown body text
    expect(pre?.className).toContain('font-sans')
  })

  it('renders <Markdown> when isStreaming=false (complete)', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Hello\n\nThis is **markdown** content' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // When complete, Markdown should render the heading as an <h1> element
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent('Hello')

    // The bold text should be rendered as <strong>
    const strong = document.querySelector('strong')
    expect(strong).toBeInTheDocument()
    expect(strong).toHaveTextContent('markdown')
  })

  it('does not mount Markdown during streaming (no heading elements)', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Heading\n\n- list item' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, markdown should NOT be parsed — no <h1> heading
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
    // No list elements either
    expect(document.querySelector('ul')).not.toBeInTheDocument()
  })

  it('switches from <pre> to <Markdown> when status transitions streaming→complete', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Title' },
    ]

    const { rerender } = render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // Initially: <pre> fallback
    expect(document.querySelector('pre')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()

    // After complete: <Markdown> renders
    rerender(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Title')
  })
})

describe('TextPart bubble rendering', () => {
  it('renders agent text without a bubble (plain text, no bg-card)', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: 'Agent reply' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
        messageRole="agent"
      />,
    )

    const wrapper = screen.getByText('Agent reply').closest('div')
    expect(wrapper?.className).not.toContain('bg-card')
    expect(wrapper?.className).not.toContain('border')
  })

  it('renders user text in a filled primary bubble', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: 'User message' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
        messageRole="user"
      />,
    )

    const bubble = screen.getByText('User message').closest('div[class*="bg-primary"]')
    expect(bubble).toBeInTheDocument()
    expect(bubble?.className).toContain('rounded-2xl')
    expect(bubble?.className).toContain('text-primary-foreground')
  })
})

describe('ProcessSegment clustering', () => {
  it('groups consecutive process-type parts into a single ProcessSegment', () => {
    const parts: MessagePart[] = [
      { type: 'tool_use', callId: 'c1', toolName: 'fs_read', args: {} },
      { type: 'tool_use', callId: 'c2', toolName: 'fs_write', args: {} },
      { type: 'tool_use', callId: 'c3', toolName: 'bash', args: {} },
      { type: 'text', content: 'Done!' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // Complete: ProcessSegment should be collapsed with a summary
    // Summary should mention 3 tools
    expect(screen.getByText(/3 个工具/)).toBeInTheDocument()

    // Text part should render as bubble
    expect(screen.getByText('Done!')).toBeInTheDocument()
  })

  it('preserves alternating process→conclusion→process order', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Let me think...' },
      { type: 'tool_use', callId: 'c1', toolName: 'fs_read', args: {} },
      { type: 'text', content: 'First result' },
      { type: 'tool_use', callId: 'c2', toolName: 'fs_write', args: {} },
      { type: 'text', content: 'Second result' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // Two ProcessSegments should be collapsed (two summaries)
    // First: thinking + 1 tool
    // Second: 1 tool
    const summaries = screen.getAllByText(/▸/)
    expect(summaries).toHaveLength(2)

    // Both text parts should render as bubbles
    expect(screen.getByText('First result')).toBeInTheDocument()
    expect(screen.getByText('Second result')).toBeInTheDocument()
  })

  it('renders pure conclusion parts without any ProcessSegment', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: 'Just text' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // No process segment summary
    expect(screen.queryByText(/▸/)).not.toBeInTheDocument()
    expect(screen.getByText('Just text')).toBeInTheDocument()
  })

  it('renders pure process parts as a single ProcessSegment', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Thinking only' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // One ProcessSegment, collapsed, summary should mention thinking
    expect(screen.getByText(/已深度思考/)).toBeInTheDocument()
  })
})

describe('ProcessSegment collapse/expand', () => {
  it('expands process segment during streaming', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Live thinking...', startedAt: 1000 },
      { type: 'tool_use', callId: 'c1', toolName: 'fs_read', args: {}, startedAt: 2000 },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // Streaming: segment expanded, thinking content visible
    expect(screen.getByText('Live thinking...')).toBeInTheDocument()
  })

  it('collapses process segment when message is complete', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Hidden thinking', startedAt: 1000, endedAt: 2000 },
      { type: 'tool_use', callId: 'c1', toolName: 'fs_read', args: {}, startedAt: 2000 },
      {
        type: 'tool_result',
        callId: 'c1',
        result: 'ok',
        isError: false,
        endedAt: 3000,
      },
      { type: 'text', content: 'Visible conclusion' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // Complete: segment collapsed, thinking content NOT visible
    expect(screen.queryByText('Hidden thinking')).not.toBeInTheDocument()

    // Summary should be visible
    expect(screen.getByText(/▸/)).toBeInTheDocument()

    // Text conclusion should be visible
    expect(screen.getByText('Visible conclusion')).toBeInTheDocument()
  })

  it('allows user to manually expand a collapsed segment', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Expandable thinking', startedAt: 1000, endedAt: 2000 },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // Initially collapsed
    expect(screen.queryByText('Expandable thinking')).not.toBeInTheDocument()

    // Click summary to expand
    const summaryButton = screen.getByRole('button', { name: /已深度思考/ })
    fireEvent.click(summaryButton)

    // Now thinking content should be visible
    expect(screen.getByText('Expandable thinking')).toBeInTheDocument()
  })

  it('allows user to manually collapse an expanded segment', () => {
    const parts: MessagePart[] = [
      { type: 'thinking', content: 'Collapsible thinking', startedAt: 1000, endedAt: 2000 },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // Expand first
    fireEvent.click(screen.getByRole('button', { name: /已深度思考/ }))
    expect(screen.getByText('Collapsible thinking')).toBeInTheDocument()

    // Re-query button (DOM element changes between collapsed/expanded states)
    fireEvent.click(screen.getByRole('button', { name: /已深度思考/ }))
    expect(screen.queryByText('Collapsible thinking')).not.toBeInTheDocument()
  })
})

describe('FileWritePreviewPart streaming fallback', () => {
  it('renders <pre> fallback when status=streaming (no Shiki/CodeBlock)', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: 'def hello():\n    print("world")',
        callId: 'call-1',
        status: 'streaming',
        language: 'python',
      },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, content should be in a <pre> element (plain text fallback)
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('def hello():')
    expect(pre).toHaveTextContent('print("world")')

    // The pre should use font-mono to match CodeBlock styling
    expect(pre?.className).toContain('font-mono')

    // The file name and "生成中" indicator should be visible (ProcessSegment expanded during streaming)
    expect(screen.getByText('test.py')).toBeInTheDocument()
    expect(screen.getByText('生成中')).toBeInTheDocument()
  })

  it('renders CodeBlock when status=complete with newContent', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: '',
        callId: 'call-1',
        status: 'complete',
        language: 'python',
        oldContent: null,
        newContent: 'def hello():\n    print("world")',
      },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // messageStatus=streaming keeps ProcessSegment expanded so we can test the part rendering.
    // The file name and "已创建" should be visible (part status=complete)
    expect(screen.getByText('test.py')).toBeInTheDocument()
    expect(screen.getByText('已创建')).toBeInTheDocument()

    // Content should be present (CodeBlock renders code in a <code> element)
    expect(screen.getByText(/def hello/)).toBeInTheDocument()
  })

  it('switches from <pre> to CodeBlock when status transitions streaming→complete', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: 'def hello():\n    print("world")',
        callId: 'call-1',
        status: 'streaming',
        language: 'python',
      },
    ]

    const { rerender } = render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // Initially: <pre> fallback with "生成中"
    expect(screen.getByText('生成中')).toBeInTheDocument()

    // After complete with newContent: switch to CodeBlock, show "已创建"
    const completedParts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: '',
        callId: 'call-1',
        status: 'complete',
        language: 'python',
        oldContent: null,
        newContent: 'def hello():\n    print("world")',
      },
    ]

    rerender(
      <PartList
        parts={completedParts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    expect(screen.getByText('已创建')).toBeInTheDocument()
    expect(screen.queryByText('生成中')).not.toBeInTheDocument()
  })
})
