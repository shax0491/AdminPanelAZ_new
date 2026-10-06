import { describe, expect, it } from 'vitest'

import { buildMonitoringConnectionRows } from '@/components/monitoring/MonitoringConnectionsList'
import { collectMonitoringGeoConnections } from '@/components/monitoring/ConnectionAddress'
import { getProtocolBarColor, MONITORING_PROTOCOL_COLORS } from '@/components/monitoring/monitoringChartTheme'
import type { MonitoringOverview, WireGuardPeer } from '@/types'
import {
  awgTransferTotal,
  countOnline,
  liveConnectionsDescription,
  nodeCardColumns,
  nodeCardSpan,
  nodeOnlineSummary,
  onlineMetricLabel,
} from './awgMonitoring'
import { totalTraffic } from './trafficFormat'

function peer(name: string, over: Partial<WireGuardPeer> = {}): WireGuardPeer {
  return {
    interface: 'antizapret3',
    public_key: `pk-${name}`,
    client_name: name,
    transfer_rx: 100,
    transfer_tx: 200,
    ...over,
  }
}

const online = (p: WireGuardPeer) => p.latest_handshake === 'fresh'

describe('AWG 3.1 rows in the monitoring table', () => {
  const base = { showOpenVpn: false, showWireGuard: false, isWireGuardOnline: online }

  it('adds one row per AWG 3.1 peer with the AWG 3.1 protocol and its own online state', () => {
    const rows = buildMonitoringConnectionRows([], [], {
      ...base,
      amneziawg3Peers: [peer('alice', { latest_handshake: 'fresh' }), peer('bob')],
      showAmneziaWg3: true,
    })
    expect(rows.map((r) => [r.protocol, r.clientName, r.online])).toEqual([
      ['amneziawg3', 'alice', true],
      ['amneziawg3', 'bob', false],
    ])
    expect(rows[0].key.startsWith('awg3-')).toBe(true)
  })

  it('keeps AWG 2.0 and 3.1 rows of the same client name apart (different keys and protocols)', () => {
    const rows = buildMonitoringConnectionRows([], [], {
      ...base,
      amneziawg2Peers: [peer('alice', { interface: 'antizapret2' })],
      showAmneziaWg2: true,
      amneziawg3Peers: [peer('alice')],
      showAmneziaWg3: true,
    })
    expect(rows.map((r) => r.protocol).sort()).toEqual(['amneziawg2', 'amneziawg3'])
    expect(new Set(rows.map((r) => r.key)).size).toBe(2)
  })

  it('shows nothing for 3.1 when the protocol is hidden by the filter or the module is off', () => {
    const peers = [peer('alice', { latest_handshake: 'fresh' })]
    expect(buildMonitoringConnectionRows([], [], { ...base, amneziawg3Peers: peers, showAmneziaWg3: false })).toEqual([])
    expect(buildMonitoringConnectionRows([], [], { ...base, amneziawg3Peers: undefined, showAmneziaWg3: true })).toEqual([])
  })

  it('honours a dedicated online predicate for 3.1', () => {
    const rows = buildMonitoringConnectionRows([], [], {
      ...base,
      amneziawg3Peers: [peer('alice')],
      showAmneziaWg3: true,
      isAwg3Online: () => true,
    })
    expect(rows[0].online).toBe(true)
  })
})

describe('AWG 3.1 in geo, counters and traffic', () => {
  it('geo summary counts 3.1 peers and respects online-only', () => {
    const fresh = peer('a', { latest_handshake: 'fresh', city: 'Riga', isp: 'X' })
    const stale = peer('b', { city: 'Riga', isp: 'X' })
    const opts = { showOpenVpn: false, showWireGuard: false, isWireGuardOnline: online, amneziawg3Peers: [fresh, stale], showAmneziaWg3: true }
    expect(collectMonitoringGeoConnections([], [], { ...opts, onlineOnly: true })).toHaveLength(1)
    expect(collectMonitoringGeoConnections([], [], { ...opts, onlineOnly: false })).toHaveLength(2)
    expect(collectMonitoringGeoConnections([], [], { ...opts, showAmneziaWg3: false })).toHaveLength(0)
  })

  it('counts online peers and sums transfer of both AmneziaWG protocols', () => {
    expect(countOnline([peer('a', { latest_handshake: 'fresh' }), peer('b')], online)).toBe(1)
    const lists = { amneziawg2: [peer('x', { transfer_rx: 1, transfer_tx: 2 })], amneziawg3: [peer('y', { transfer_rx: 10, transfer_tx: 20 })] }
    expect(awgTransferTotal(lists, 'transfer_rx')).toBe(11)
    expect(awgTransferTotal(lists, 'transfer_tx')).toBe(22)
    expect(awgTransferTotal({}, 'transfer_rx')).toBe(0)
  })

  it('total session traffic includes AWG 3.1', () => {
    const data = {
      openvpn_clients: [],
      wireguard_peers: [],
      amneziawg2_peers: [peer('x', { transfer_rx: 1, transfer_tx: 1 })],
      amneziawg3_peers: [peer('y', { transfer_rx: 5, transfer_tx: 6 })],
    } as unknown as MonitoringOverview
    expect(totalTraffic(data)).toBe(13)
    expect(totalTraffic({ ...data, amneziawg3_peers: undefined })).toBe(2)
  })

  it('has a bar color for AWG 3.1 distinct from AWG 2.0 and the default', () => {
    expect(getProtocolBarColor('AWG 3.1')).toBe(MONITORING_PROTOCOL_COLORS.amneziawg3)
    expect(getProtocolBarColor('AWG 3.1')).not.toBe(getProtocolBarColor('AWG 2.0'))
    expect(getProtocolBarColor('AWG 3.1')).not.toBe(MONITORING_PROTOCOL_COLORS.total)
  })
})

describe('labels that list only the enabled protocols', () => {
  it('names the online metric by enabled protocols', () => {
    expect(onlineMetricLabel(false, false)).toBe('Online OVPN / WG')
    expect(onlineMetricLabel(true, false)).toBe('Online OVPN / WG / AWG 2.0')
    expect(onlineMetricLabel(false, true)).toBe('Online OVPN / WG / AWG 3.1')
    expect(onlineMetricLabel(true, true)).toBe('Online OVPN / WG / AWG 2.0 / AWG 3.1')
  })

  it('keeps the old page description when AWG is off and extends it otherwise', () => {
    expect(liveConnectionsDescription(false, false)).toBe(
      'Активные VPN-подключения OpenVPN и WireGuard в реальном времени',
    )
    expect(liveConnectionsDescription(true, false)).toBe(
      'Активные VPN-подключения OpenVPN, WireGuard и AWG 2.0 в реальном времени',
    )
    expect(liveConnectionsDescription(true, true)).toBe(
      'Активные VPN-подключения OpenVPN, WireGuard, AWG 2.0 и AWG 3.1 в реальном времени',
    )
  })

  it('formats the per-node online line only with enabled protocols', () => {
    const node = { connected_openvpn: 1, connected_wireguard: 2, connected_amneziawg2: 3, connected_amneziawg3: 4 }
    expect(nodeOnlineSummary(node, false, false)).toBe('1 / 2')
    expect(nodeOnlineSummary(node, true, false)).toBe('1 / 2 / 3')
    expect(nodeOnlineSummary(node, false, true)).toBe('1 / 2 / 4')
    expect(nodeOnlineSummary(node, true, true)).toBe('1 / 2 / 3 / 4')
    expect(nodeOnlineSummary({ connected_openvpn: 1, connected_wireguard: 2 }, true, true)).toBe('1 / 2 / 0 / 0')
  })

  it('grows the node card grid by one column per enabled AmneziaWG protocol', () => {
    expect([nodeCardColumns(false, false), nodeCardColumns(true, false), nodeCardColumns(false, true), nodeCardColumns(true, true)]).toEqual([
      'sm:grid-cols-4',
      'sm:grid-cols-5',
      'sm:grid-cols-5',
      'sm:grid-cols-6',
    ])
    expect(nodeCardSpan(true, true)).toBe('sm:col-span-6')
    expect(nodeCardSpan(false, false)).toBe('sm:col-span-4')
  })
})
