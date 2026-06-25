export function buildDiffStyles(isDark: boolean) {
  const fontFamily =
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'
  const base = {
    diffContainer: {
      fontSize: 12,
      lineHeight: 1.55,
    },
    contentText: {
      fontFamily,
      fontSize: 12,
    },
    gutter: {
      fontFamily,
      fontSize: 11,
      minWidth: 36,
      padding: '0 6px',
    },
    line: {
      padding: '0 6px',
    },
    titleBlock: {
      fontFamily:
        'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
      fontSize: 11,
      padding: '6px 12px',
      textTransform: 'none' as const,
      letterSpacing: 0,
    },
    splitView: {},
  }
  if (isDark) {
    return {
      ...base,
      variables: {
        dark: {
          diffViewerBackground: 'transparent',
          diffViewerColor: '#d4d4d8',
          addedBackground: 'rgba(34, 197, 94, 0.10)',
          addedColor: '#bbf7d0',
          removedBackground: 'rgba(244, 63, 94, 0.12)',
          removedColor: '#fecdd3',
          wordAddedBackground: 'rgba(34, 197, 94, 0.30)',
          wordRemovedBackground: 'rgba(244, 63, 94, 0.32)',
          addedGutterBackground: 'rgba(34, 197, 94, 0.14)',
          removedGutterBackground: 'rgba(244, 63, 94, 0.16)',
          addedGutterColor: '#86efac',
          removedGutterColor: '#fda4af',
          gutterBackground: 'transparent',
          gutterBackgroundDark: 'transparent',
          gutterColor: '#52525b',
          codeFoldGutterBackground: 'transparent',
          codeFoldBackground: 'rgba(255,255,255,0.03)',
          emptyLineBackground: 'rgba(255,255,255,0.02)',
          diffViewerTitleBackground: 'rgba(255,255,255,0.03)',
          diffViewerTitleColor: '#a1a1aa',
          diffViewerTitleBorderColor: 'rgba(255,255,255,0.08)',
        },
      },
    }
  }
  return {
    ...base,
    variables: {
      light: {
        diffViewerBackground: 'transparent',
        diffViewerColor: '#18181b',
        addedBackground: '#e6ffec',
        addedColor: '#14532d',
        removedBackground: '#ffeef0',
        removedColor: '#7f1d1d',
        wordAddedBackground: '#abf2bc',
        wordRemovedBackground: '#fdb8c0',
        addedGutterBackground: '#cdf5d8',
        removedGutterBackground: '#ffd7dc',
        addedGutterColor: '#15803d',
        removedGutterColor: '#b91c1c',
        gutterBackground: 'transparent',
        gutterBackgroundDark: 'transparent',
        gutterColor: '#a1a1aa',
        codeFoldGutterBackground: '#f4f4f5',
        codeFoldBackground: '#fafafa',
        emptyLineBackground: '#fafafa',
        diffViewerTitleBackground: '#fafafa',
        diffViewerTitleColor: '#52525b',
        diffViewerTitleBorderColor: '#e4e4e7',
      },
    },
  }
}
