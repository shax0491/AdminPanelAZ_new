import type { WireGuardPeer } from '@/types'

/** Подписи протоколов AmneziaWG в мониторинге: одна точка правды для таблиц, карточек и графиков. */
export const AWG2_LABEL = 'AWG 2'
export const AWG3_LABEL = 'AWG 3'

type PeerLists = {
  amneziawg2?: WireGuardPeer[]
  amneziawg3?: WireGuardPeer[]
}

export function countOnline(peers: readonly WireGuardPeer[], isOnline: (peer: WireGuardPeer) => boolean): number {
  return peers.filter(isOnline).length
}

/** Подпись «Online OVPN / WG / AWG 2 / AWG 3» по включённым протоколам. */
export function onlineMetricLabel(awg2Enabled: boolean, awg3Enabled: boolean): string {
  const parts = ['OVPN', 'WG']
  if (awg2Enabled) parts.push(AWG2_LABEL)
  if (awg3Enabled) parts.push(AWG3_LABEL)
  return `Online ${parts.join(' / ')}`
}

/** Описание страницы: перечисляет только включённые протоколы. */
export function liveConnectionsDescription(awg2Enabled: boolean, awg3Enabled: boolean): string {
  const names = ['OpenVPN', 'WireGuard']
  if (awg2Enabled) names.push(AWG2_LABEL)
  if (awg3Enabled) names.push(AWG3_LABEL)
  const head = names.slice(0, -1).join(', ')
  return `Активные VPN-подключения ${head} и ${names[names.length - 1]} в реальном времени`
}

/** Значения для строки «OVPN a / WG b / AWG 2 c / AWG 3 d» в сводке узла. */
export function nodeOnlineSummary(
  node: { connected_openvpn: number; connected_wireguard: number; connected_amneziawg2?: number; connected_amneziawg3?: number },
  awg2Enabled: boolean,
  awg3Enabled: boolean,
): string {
  const values = [node.connected_openvpn, node.connected_wireguard]
  if (awg2Enabled) values.push(node.connected_amneziawg2 ?? 0)
  if (awg3Enabled) values.push(node.connected_amneziawg3 ?? 0)
  return values.join(' / ')
}

/** Сумма RX или TX по спискам пиров AmneziaWG (только включённые протоколы). */
export function awgTransferTotal(lists: PeerLists, key: 'transfer_rx' | 'transfer_tx'): number {
  const sum = (peers?: WireGuardPeer[]) => (peers ?? []).reduce((total, peer) => total + peer[key], 0)
  return sum(lists.amneziawg2) + sum(lists.amneziawg3)
}

/** Число колонок сетки карточки узла: OVPN, WG, службы, CPU/RAM плюс по одной на включённый AWG. */
export function nodeCardColumns(awg2Enabled: boolean, awg3Enabled: boolean): 'sm:grid-cols-4' | 'sm:grid-cols-5' | 'sm:grid-cols-6' {
  const extra = Number(awg2Enabled) + Number(awg3Enabled)
  if (extra === 2) return 'sm:grid-cols-6'
  if (extra === 1) return 'sm:grid-cols-5'
  return 'sm:grid-cols-4'
}

export function nodeCardSpan(awg2Enabled: boolean, awg3Enabled: boolean): 'sm:col-span-4' | 'sm:col-span-5' | 'sm:col-span-6' {
  const extra = Number(awg2Enabled) + Number(awg3Enabled)
  if (extra === 2) return 'sm:col-span-6'
  if (extra === 1) return 'sm:col-span-5'
  return 'sm:col-span-4'
}
