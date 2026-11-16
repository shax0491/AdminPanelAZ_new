// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Node } from '@/types'
import NodeDualStackControls from './NodeDualStackControls'
import { getNodeIpv4Egress, setNodeIpv4Egress, rotateNodeIpv6, setNodeDualStackEnabled, setNodeEgressMode, setNodeTransportMode } from '@/api/client'
import { getNodesIpv6Summary } from '@/api/ipv6'

vi.mock('@/api/client', () => ({
  auditNodeDualStack: vi.fn(),
  setNodeTransportMode: vi.fn(),
  setNodeEgressMode: vi.fn(),
  setNodeDualStackEnabled: vi.fn(),
  getNodeIpv4Egress: vi.fn(),
  setNodeIpv4Egress: vi.fn(),
  rotateNodeIpv6: vi.fn(),
}))

vi.mock('@/api/ipv6', () => ({ getNodesIpv6Summary: vi.fn() }))

function live(over: Record<string, unknown> = {}) {
  vi.mocked(getNodesIpv6Summary).mockResolvedValue({ '1': { enabled: true, egress: 'native', active_egress: 'native', effective_mode: 'c', ...over } })
}

function node(overrides: Partial<Node> = {}): Node {
  return {
    id: 1,
    name: 'DE1',
    host: '203.0.113.10',
    port: 9100,
    status: 'online',
    is_local: false,
    mtls_enabled: false,
    node_kind: 'vpn',
    metadata: {},
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    has_ipv4: true,
    has_ipv6: true,
    ipv6_address: '2001:db8::1',
    ipv6_subnet_prefix: '64',
    ipv6_rotation_supported: true,
    ipv4_probe_status: 'offline',
    ipv6_probe_status: 'online',
    ipv6_latency_ms: 21.2,
    transport_mode: 'auto',
    egress_mode: 'native',
    ...overrides,
  }
}

describe('NodeDualStackControls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    live()
    vi.mocked(getNodeIpv4Egress).mockResolvedValue({ ipv4_egress: 'direct' })
  })
  afterEach(cleanup)

  it('shows independent family health and IPv6-only active state', () => {
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)

    expect(screen.getByText('IPv4 Blocked / Active via IPv6')).not.toBeNull()
    expect(screen.getByText(/21\.2/)).not.toBeNull()
    expect(screen.getByText(/2001:db8::1/)).not.toBeNull()
  })

  it('rotates a confirmed /64 and refreshes the node', async () => {
    const onUpdated = vi.fn()
    vi.mocked(rotateNodeIpv6).mockResolvedValue({ ipv6_address: '2001:db8::2' })
    render(<NodeDualStackControls node={node()} onUpdated={onUpdated} />)

    fireEvent.click(screen.getByRole('button', { name: /Ротировать IPv6/ }))

    await waitFor(() => expect(rotateNodeIpv6).toHaveBeenCalledWith(1))
    expect(onUpdated).toHaveBeenCalled()
  })

  it('disables rotation without a confirmed public /64', () => {
    render(
      <NodeDualStackControls
        node={node({ ipv6_rotation_supported: false, ipv6_subnet_prefix: '128' })}
        onUpdated={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /Ротировать IPv6/ })).toHaveProperty('disabled', true)
  })

  it('changes transport through the panel API', async () => {
    vi.mocked(setNodeTransportMode).mockResolvedValue(node({ transport_mode: 'force_ipv6' }))
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Транспорт'), { target: { value: 'force_ipv6' } })

    await waitFor(() => expect(setNodeTransportMode).toHaveBeenCalledWith(1, 'force_ipv6'))
  })

  it('offers Reverse Tunnel only on a linked relay', () => {
    const { rerender } = render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    expect(screen.queryByRole('option', { name: /Reverse Tunnel/ })).toBeNull()

    rerender(
      <NodeDualStackControls
        node={node({ node_kind: 'proxy', linked_vpn_node_id: 1 })}
        onUpdated={vi.fn()}
      />,
    )
    expect(screen.getByRole('option', { name: /Reverse Tunnel/ })).not.toBeNull()
  })

  it('shows live Dual-Stack and IPv6 egress from the node, not the DB card value', async () => {
    live({ active_egress: 'proton', egress: 'proton' })
    render(<NodeDualStackControls node={node({ egress_mode: 'native' })} onUpdated={vi.fn()} />)

    await waitFor(() => expect((screen.getByLabelText('Выход IPv6') as HTMLSelectElement).value).toBe('proton'))
    expect((screen.getByLabelText('Dual-Stack сервера') as HTMLSelectElement).value).toBe('on')
    expect(screen.queryByRole('option', { name: /IPv4 Only|IPv6 Prefer/ })).toBeNull()
  })

  it('blocks the IPv6 egress selector when Dual-Stack is off or unknown', async () => {
    live({ enabled: false })
    const { unmount } = render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect((screen.getByLabelText('Dual-Stack сервера') as HTMLSelectElement).value).toBe('off'))
    expect(screen.getByLabelText('Выход IPv6')).toHaveProperty('disabled', true)
    unmount()

    vi.mocked(getNodesIpv6Summary).mockRejectedValue(new Error('down'))
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect((screen.getByLabelText('Dual-Stack сервера') as HTMLSelectElement).value).toBe('unknown'))
    expect(screen.getByLabelText('Выход IPv6')).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('Dual-Stack сервера')).toHaveProperty('disabled', true)
  })

  it('switches Dual-Stack and IPv6 egress through separate API calls', async () => {
    vi.mocked(setNodeDualStackEnabled).mockResolvedValue({ enabled: false, changed: true })
    vi.mocked(setNodeEgressMode).mockResolvedValue(node({ egress_mode: 'proton' }))
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect(getNodesIpv6Summary).toHaveBeenCalled())
    await screen.findByDisplayValue('Вкл')

    fireEvent.change(screen.getByLabelText('Выход IPv6'), { target: { value: 'proton' } })
    await waitFor(() => expect(setNodeEgressMode).toHaveBeenCalledWith(1, 'proton'))
    fireEvent.change(screen.getByLabelText('Dual-Stack сервера'), { target: { value: 'off' } })
    await waitFor(() => expect(setNodeDualStackEnabled).toHaveBeenCalledWith(1, false))
  })

  it('shows IPv4 exit separately and switches it through its own API after confirmation', async () => {
    vi.mocked(setNodeIpv4Egress).mockResolvedValue({ ipv4_egress: 'proton', changed: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect((screen.getByLabelText('IPv4: выход') as HTMLSelectElement).value).toBe('direct'))

    fireEvent.change(screen.getByLabelText('IPv4: выход'), { target: { value: 'proton' } })
    await waitFor(() => expect(setNodeIpv4Egress).toHaveBeenCalledWith(1, 'proton'))
    expect(setNodeEgressMode).not.toHaveBeenCalled()
    expect(setNodeDualStackEnabled).not.toHaveBeenCalled()
  })

  it('does not switch IPv4 exit when the operator declines the confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect((screen.getByLabelText('IPv4: выход') as HTMLSelectElement).value).toBe('direct'))
    fireEvent.change(screen.getByLabelText('IPv4: выход'), { target: { value: 'proton' } })
    expect(setNodeIpv4Egress).not.toHaveBeenCalled()
  })

  it('locks the IPv4 exit selector when the node state is unknown', async () => {
    vi.mocked(getNodeIpv4Egress).mockRejectedValue(new Error('down'))
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)
    await waitFor(() => expect((screen.getByLabelText('IPv4: выход') as HTMLSelectElement).value).toBe('unknown'))
    expect(screen.getByLabelText('IPv4: выход')).toHaveProperty('disabled', true)
  })
})
