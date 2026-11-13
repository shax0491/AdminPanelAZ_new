// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Node } from '@/types'
import NodeDualStackControls from './NodeDualStackControls'
import { rotateNodeIpv6, setNodeTransportMode } from '@/api/client'

vi.mock('@/api/client', () => ({
  auditNodeDualStack: vi.fn(),
  setNodeTransportMode: vi.fn(),
  setNodeEgressMode: vi.fn(),
  rotateNodeIpv6: vi.fn(),
}))

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
  beforeEach(() => vi.clearAllMocks())
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

  it('does not claim Proton/WARP IPv6 without measured tunnel capability', () => {
    render(<NodeDualStackControls node={node()} onUpdated={vi.fn()} />)

    expect(screen.getByRole('option', { name: 'Proton VPN' })).not.toBeNull()
    expect(screen.getByRole('option', { name: 'Cloudflare WARP' })).not.toBeNull()
    expect(screen.queryByText(/Proton VPN \(v4\+v6\)/)).toBeNull()
    expect(screen.getByText(/IPv6 на интерфейсе выходного туннеля/)).not.toBeNull()
  })
})
