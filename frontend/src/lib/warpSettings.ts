/** Ключи setup, которые узел читает только при запуске up.sh: после сохранения предлагаем применить режим WARP. */
export const WARP_SETTING_KEYS = ['ANTIZAPRET_WARP', 'VPN_WARP', 'WARP_PROTECTION', 'ANTIZAPRET_WARP_DNS', 'WARP_MTU'] as const

export function touchesWarpSettings(changedKeys: readonly string[]): boolean {
  return changedKeys.some((key) => (WARP_SETTING_KEYS as readonly string[]).includes(key))
}
