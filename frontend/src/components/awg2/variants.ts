import { DOCS } from '@/lib/docsUrls'

export type AwgVariant = 'awg2' | 'awg3'

export interface AwgVariantConfig {
  title: string
  docsHref: string
  clientsHint: string
  emptyTitle: string
  emptyDescription: string
  antizapretIface: string
  antizapretSub: string
  vpnIface: string
  vpnSub: string
  nodeNote: string
}

/** One set of labels per protocol generation; the page structure is shared. */
export const AWG_VARIANTS: Record<AwgVariant, AwgVariantConfig> = {
  awg2: {
    title: 'AmneziaWG 2',
    docsHref: DOCS.awg2,
    clientsHint: 'Клиенты → AmneziaWG 2',
    emptyTitle: 'Нет клиентов AmneziaWG 2',
    emptyDescription: 'Создайте клиента на странице Клиенты (галочка «AmneziaWG 2»).',
    antizapretIface: 'antizapret',
    antizapretSub: 'antizapret2 (10.29.9.0/24)',
    vpnIface: 'vpn',
    vpnSub: 'vpn2',
    nodeNote: 'Нативный AmneziaWG 2 (client.sh/awg)',
  },
  awg3: {
    title: 'AmneziaWG 3',
    docsHref: DOCS.awg3,
    clientsHint: 'Клиенты → AmneziaWG 3',
    emptyTitle: 'Нет клиентов AmneziaWG 3',
    emptyDescription: 'Создайте клиента на странице Клиенты (галочка «AmneziaWG 3»).',
    antizapretIface: 'antizapret',
    antizapretSub: 'awg1, антизапрет (10.9.0.0/24)',
    vpnIface: 'vpn',
    vpnSub: 'awg1, полный VPN (10.9.1.0/24)',
    nodeNote: 'Нативный AmneziaWG 3 (awg1, amneziawg-go)',
  },
}
