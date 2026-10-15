import type { Node } from '@/types'

export const PROXY_DEFAULT_PORT = 9101
export const VPN_DEFAULT_PORT = 9100

/** True when node_kind is proxy (defaults to vpn if unset). */
export function isProxyNode(node: Node): boolean {
  return (node.node_kind || 'vpn') === 'proxy'
}

/** Узел «только MTProxy»: proxy_agent + MTProxyL, AntiZapret нет. */
export function isMtproxyNode(node: Node): boolean {
  return (node.node_kind || 'vpn') === 'mtproxy'
}

/** Полноценный VPN-узел (node_agent + AntiZapret). */
export function isVpnNode(node: Node): boolean {
  return (node.node_kind || 'vpn') === 'vpn'
}

/** Панель говорит с узлом через proxy_agent (прокси или «только MTProxy»). */
export function usesProxyAgent(node: Node): boolean {
  return isProxyNode(node) || isMtproxyNode(node)
}
