import { Navigate, useSearchParams } from 'react-router-dom'
import { AwgStatusPage } from '@/tg-mini/pages/Awg2'
import { useTgAuth } from '@/tg-mini/context/TgAuthContext'

/** One AmneziaWG tab with a 2 / 3 switch instead of two separate tabs. */
export default function Awg() {
  const { features, featuresReady } = useTgAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  if (!featuresReady) return null
  const versions = ([2, 3] as const).filter((v) => features[`awg${v}`])
  if (versions.length === 0) return <Navigate to="/" replace />

  const requested = Number(searchParams.get('v'))
  const version = versions.find((v) => v === requested) ?? versions[0]

  return (
    <div className="space-y-3">
      {versions.length > 1 && (
        <div className="flex w-fit overflow-hidden rounded-md border text-sm" role="tablist">
          {versions.map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={version === v}
              onClick={() => setSearchParams({ v: String(v) }, { replace: true })}
              className={`px-3 py-1.5 ${version === v ? 'bg-primary text-primary-foreground' : 'bg-card'}`}
            >
              AWG {v}
            </button>
          ))}
        </div>
      )}
      <AwgStatusPage key={version} version={version} />
    </div>
  )
}
