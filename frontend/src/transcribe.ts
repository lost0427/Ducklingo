import type { BatchOptions } from './api'

export const CONTEXT_PRESETS = ['32768', '65536', '131072', '262144', '524288', '1048576']

export const DEFAULT_TRANSCRIBE: BatchOptions = {
  quality: '1080',
  language: '',
  target_language: '简体中文',
  mode: 'standard',
  reasoning_effort: 'off',
  context_window_tokens: '131072',
  max_context_ratio: '0.8',
}

type Labels = {
  standardMode: string
  reflectMode: string
  reasoningOff: string
  reasoningLow: string
  reasoningMedium: string
  reasoningHigh: string
}

export function transcribeSummary(options: BatchOptions, t: Labels): string {
  const mode = options.mode === 'reflect' ? t.reflectMode : t.standardMode
  const reasoning =
    { off: t.reasoningOff, low: t.reasoningLow, medium: t.reasoningMedium, high: t.reasoningHigh }[options.reasoning_effort] ??
    options.reasoning_effort
  const tokens = Number(options.context_window_tokens)
  const window = Number.isFinite(tokens) && tokens > 0 ? `${Math.round(tokens / 1024)}K` : options.context_window_tokens
  const ratio = Math.round(Number(options.max_context_ratio) * 100)
  return [options.target_language, mode, reasoning, `${window}/${ratio}%`].filter(Boolean).join(' · ')
}
