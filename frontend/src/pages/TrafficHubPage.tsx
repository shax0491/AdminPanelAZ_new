import { useSearchParams } from 'react-router-dom'
import AwgMonitorView from '@/components/awg2/AwgMonitorView'
import { useAuth } from '@/context/AuthContext'
import { useFeatureModules } from '@/context/FeatureModulesContext'
import TrafficPage from '@/pages/TrafficPage'

type View = 'traffic' | 'awg2' | 'awg3'

const LABELS: Record<View, string> = {
  traffic: 'Трафик',
  awg2: 'AmneziaWG 2',
  awg3: 'AmneziaWG 3',
}

/** One "Мониторинг трафика" tab: accumulated traffic plus live AmneziaWG 2/3 peers (former separate pages). */
export default function TrafficHubPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { isEnabled } = useFeatureModules()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const views: View[] = ['traffic']
  if (isAdmin && isEnabled('awg2')) views.push('awg2')
  if (isAdmin && isEnabled('awg3')) views.push('awg3')

  const requested = searchParams.get('view') as View | null
  const view: View = requested && views.includes(requested) ? requested : 'traffic'

  const select = (next: View) => {
    const params = new URLSearchParams(searchParams)
    if (next === 'traffic') params.delete('view')
    else {
      params.set('view', next)
      params.delete('client')
    }
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="space-y-4">
      {views.length > 1 && (
        <div className="flex w-fit overflow-hidden rounded-md border text-sm" role="tablist">
          {views.map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => select(v)}
              className={`px-3 py-1.5 transition-colors ${
                view === v ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
              }`}
            >
              {LABELS[v]}
            </button>
          ))}
        </div>
      )}
      {view === 'traffic' ? <TrafficPage /> : <AwgMonitorView version={view === 'awg3' ? 3 : 2} />}
    </div>
  )
}
