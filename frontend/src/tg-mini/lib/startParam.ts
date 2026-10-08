const START_PARAM_ROUTES: Record<string, string> = {
  awg: '/awg',
  awg2: '/awg?v=2',
  awg3: '/awg?v=3',
  mtproxy: '/mtproxy',
  warper: '/warper',
  cidr: '/cidr',
  nodes: '/nodes',
  configs: '/configs',
  settings: '/settings',
  'unlock-codes': '/unlock-codes',
}

export function mapTelegramStartParam(startParam: string | null | undefined): string | null {
  const normalized = startParam?.trim().toLowerCase()
  if (!normalized) return null

  return Object.prototype.hasOwnProperty.call(START_PARAM_ROUTES, normalized)
    ? START_PARAM_ROUTES[normalized]
    : null
}
