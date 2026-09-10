/**
 * AdsView — "Anuncios": the cockpit for Safent Ads campaigns, embedded
 * SAME-ORIGIN (026, contracts/sso.md). The panel loads at `/ads/` through
 * Safent's own session-bridge reverse proxy (T005) — no cross-origin
 * iframe, no `?k=`/token in the src URL: the browser already carries the
 * HttpOnly `ads_bridge` cookie the sidebar's useAdsAvailability poll keeps
 * warm (SC-002: zero-second logins across 20 opens).
 *
 * Every FR-003 availability state gets its own honest empty state with the
 * action that unblocks it — never a blank iframe or a generic error
 * (spec.md Acceptance Scenario 4). "no_accounts" is the one exception: the
 * companion's own panel guides account connection, so Ads stays visible and
 * usable either way (Assumption 7 — never hidden for lack of accounts).
 */
import type { ReactNode } from 'react'
import { Loader2, Megaphone, RefreshCw, ShieldAlert, Wrench } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useT } from '../lib/i18n'
import { useAdsAvailability } from '../hooks/useAdsAvailability'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import type { AdsAvailabilityReason } from '../api/types'
import css from './AdsView.module.css'

const ADS_IFRAME_SRC = '/ads/'

const BLOCKED_STATE_ICON: Record<AdsAvailabilityReason, ReactNode> = {
  not_installed: <Wrench size={28} aria-hidden="true" />,
  unreachable: <Loader2 size={28} aria-hidden="true" className="spin" />,
  unauthorized: <ShieldAlert size={28} aria-hidden="true" />,
  no_accounts: <Megaphone size={28} aria-hidden="true" />,
}

export default function AdsView() {
  const t = useT()
  const navigate = useNavigate()
  const availability = useAdsAvailability()

  if (availability.status === 'loading') {
    return (
      <>
        <PageHeader title={t('nav.ads')} subtitle={t('ads.subtitle')} />
        <div className="view-body">
          <EmptyState
            icon={<Loader2 size={28} aria-hidden="true" className="spin" />}
            title={t('ads.state.loading.title')}
          />
        </div>
      </>
    )
  }

  const isBlocked = availability.status === 'unavailable' && availability.reason !== 'no_accounts'

  if (isBlocked) {
    const reason = availability.reason ?? 'unreachable'
    return (
      <>
        <PageHeader title={t('nav.ads')} subtitle={t('ads.subtitle')} />
        <div className="view-body">
          <EmptyState
            icon={BLOCKED_STATE_ICON[reason]}
            title={t(`ads.state.${reason}.title`)}
            description={t(`ads.state.${reason}.desc`)}
            action={
              reason === 'unreachable' ? (
                <Button variant="secondary" size="sm" onClick={availability.refresh}>
                  <RefreshCw size={13} aria-hidden="true" />
                  {t('ads.state.retry')}
                </Button>
              ) : (
                <Button variant="primary" size="sm" onClick={() => navigate('/capacidades?tab=mcp')}>
                  {t('ads.empty.cta')}
                </Button>
              )
            }
          />
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t('nav.ads')} subtitle={t('ads.subtitle')} />
      <div className="view-body">
        {availability.reason === 'no_accounts' && (
          <p className={css.hint}>{t('ads.state.no_accounts.desc')}</p>
        )}
        <div className={css.frameWrap}>
          <iframe src={ADS_IFRAME_SRC} title={t('ads.iframe.title')} className={css.frame} />
        </div>
      </div>
    </>
  )
}
