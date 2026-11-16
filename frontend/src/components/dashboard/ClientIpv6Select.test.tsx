// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ClientIpv6Select from './ClientIpv6Select'

const status = vi.hoisted(() => ({ value: null as Record<string, unknown> | null }))
vi.mock('@/components/dashboard/Ipv6Badge', () => ({
  default: () => null,
  useIpv6Status: () => status.value,
}))

const dualOption = () => screen.getByRole('option', { name: /Dual-Stack/ }) as HTMLOptionElement

describe('ClientIpv6Select', () => {
  afterEach(cleanup)

  it('allows Dual-Stack only when the server confirmed it is on', () => {
    status.value = { enabled: true, profile_default: 'dual' }
    render(<ClientIpv6Select id="x" value="default" onChange={() => undefined} />)
    expect(dualOption().disabled).toBe(false)
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('dual')
  })

  it.each([
    ['off', { enabled: false, profile_default: 'dual' }],
    ['unknown', null],
  ])('blocks Dual-Stack in the UI when server state is %s and falls back to IPv4', (_n, st) => {
    status.value = st
    render(<ClientIpv6Select id="x" value="default" onChange={() => undefined} />)
    expect(dualOption().disabled).toBe(true)
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('ipv4')
    expect(screen.getByText(/Dual-Stack недоступен/)).not.toBeNull()
  })

  it('keeps an already saved dual profile visible and selectable state unchanged', () => {
    status.value = { enabled: false }
    render(<ClientIpv6Select id="x" value="dual" onChange={() => undefined} />)
    expect(dualOption().disabled).toBe(false)
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('dual')
  })
})
