/**
 * Smoke test: verify the App component renders without crashing.
 * Uses vitest + jsdom for a fast, lightweight DOM environment.
 */
import { describe, it, expect } from 'vitest'
import React from 'react'
import { render } from '@testing-library/react'
import AppShell from './App'

describe('App smoke test', () => {
  it('renders without crashing', () => {
    // AppShell is a class component - render it in jsdom
    const { container } = render(React.createElement(AppShell))
    expect(container).toBeTruthy()
    // Should have at least some DOM content
    expect(container.innerHTML.length).toBeGreaterThan(0)
  })

  it('renders the header area', () => {
    const { container } = render(React.createElement(AppShell))
    // The app should have a root structure
    const rootEl = container.firstChild
    expect(rootEl).toBeTruthy()
  })
})
