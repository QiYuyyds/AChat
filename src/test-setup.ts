import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Auto-cleanup DOM after each test to prevent cross-test contamination
afterEach(() => {
  cleanup()
})
