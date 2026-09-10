//! T011 — the boot service: observe -> plan -> apply -> reobserve, with
//! backoff between retries and cancel support honored only before the point
//! of no return. `BootService` itself has no `tauri` import — only the
//! "Tauri glue" section at the bottom does, and `main.rs`'s one
//! `boot::start(app.handle().clone())` call is its only caller.

use std::sync::Arc;
use std::time::Duration;

use crate::domain::{
    AttemptCount, BootstrapTicket, DesiredState, DomainEvent, EngineLifecycle, EnginePhase,
    FailOutcome, FailureCause, FailureCode, RepairAction, SemVer, Stage, VersionSet,
};
use crate::ports::{
    ApplyOutcome, CancelSignal, Clock, EngineDriver, EngineError, EngineProbe, Notifier,
};
use crate::reconcile;

/// Where `BootService::run` landed.
#[derive(Debug)]
pub enum LoopOutcome {
    /// The product is reachable — `ticket` authenticates the window's ONE
    /// navigation to it (contract app-engine.md §5).
    Ready {
        ticket: BootstrapTicket,
        lifecycle: EngineLifecycle,
    },
    /// `HostFacts::another_instance_running` — nothing here was touched.
    FocusExisting,
    /// The owner cancelled before the point of no return.
    Cancelled { lifecycle: EngineLifecycle },
    /// No progress twice in a row (data-model.md `EngineLifecycle` invariant
    /// 5) — one screen, one "Reintentar"; `lifecycle.last_failure()` has why.
    Degraded { lifecycle: EngineLifecycle },
}

/// Bootstrap-scoped: true from the `container` CLI stage onward, matching
/// the point past which the owner's "Cancelar" is disabled in the UI. This
/// is NOT a universal property of `Stage` (the update flow, `src-tauri/src/
/// update/`, declares `backup` as ITS point of no return instead) — it lives
/// here because only the bootstrap context's policy is this loop's to define.
fn bootstrap_point_of_no_return(stage: Stage) -> bool {
    matches!(
        stage,
        Stage::Container
            | Stage::Health
            | Stage::CompanionScaffold
            | Stage::CompanionUp
            | Stage::CompanionReload
            | Stage::Backup
            | Stage::Restore
            | Stage::Cleanup
    )
}

/// Same rule, expressed in `EnginePhase` terms for the loop's own cancel
/// gate (it knows the phase it is about to enter before any CLI stage event
/// arrives).
fn phase_past_point_of_no_return(phase: EnginePhase) -> bool {
    matches!(
        phase,
        EnginePhase::EngineStarting
            | EnginePhase::EngineReady
            | EnginePhase::CompanionProvisioning
            | EnginePhase::CompanionReady
    )
}

fn phase_for(action: &RepairAction) -> Option<EnginePhase> {
    match action {
        RepairAction::StageRuntime => Some(EnginePhase::RuntimeStaging),
        RepairAction::AdoptMachine(_)
        | RepairAction::CreateMachine
        | RepairAction::StartMachine(_)
        | RepairAction::InstallPrivilegedHelper => Some(EnginePhase::EngineProvisioning),
        RepairAction::PullEngine(_) | RepairAction::PullCompanion(_) => {
            Some(EnginePhase::EnginePulling)
        }
        RepairAction::ChoosePort
        | RepairAction::CreateContainer
        | RepairAction::StartContainer
        | RepairAction::RecreateEngine => Some(EnginePhase::EngineStarting),
        RepairAction::EnsureCompanionScaffold
        | RepairAction::ComposeCompanionUp(_)
        | RepairAction::ReloadCompanionPresence => Some(EnginePhase::CompanionProvisioning),
        RepairAction::FocusExistingWindow => None,
    }
}

/// 1s, 2s, 4s, 8s, 16s, capped at 30s — the backoff between repeated
/// attempts of the SAME repair episode (`EngineLifecycle::attempt`).
fn backoff_for(attempt: AttemptCount) -> Duration {
    let exponent = attempt.0.min(5); // 1<<5 = 32, comfortably within u64 — no overflow to guard
    Duration::from_secs((1u64 << exponent).min(30))
}

pub struct BootService {
    probe: Arc<dyn EngineProbe>,
    driver: Arc<dyn EngineDriver>,
    clock: Arc<dyn Clock>,
    desired: DesiredState,
    app_version: SemVer,
}

impl BootService {
    pub fn new(
        probe: Arc<dyn EngineProbe>,
        driver: Arc<dyn EngineDriver>,
        clock: Arc<dyn Clock>,
        desired: DesiredState,
        app_version: SemVer,
    ) -> Self {
        Self {
            probe,
            driver,
            clock,
            desired,
            app_version,
        }
    }

    /// Cancellation is deliberately NOT checked proactively at the top of
    /// this loop: at that point the next action (and therefore its target
    /// phase) is not known yet, so an early check here could reject a
    /// perfectly fine cancel request one action too early, or accept one
    /// that is about to land past the gate. The one place that decides is
    /// `apply_gated`, which knows the SPECIFIC action about to run — a
    /// cancel is honored the moment `apply_gated` hands the driver a live
    /// signal and the driver reports back `EngineError::Cancelled`.
    pub fn run(&self, notifier: &dyn Notifier, cancel: &CancelSignal) -> LoopOutcome {
        let mut lifecycle = EngineLifecycle::fresh();
        lifecycle
            .enter(EnginePhase::Preflight)
            .expect("Fresh -> Preflight is always legal");

        loop {
            let facts = match self.probe.observe() {
                Ok(facts) => facts,
                Err(error) => {
                    match self.handle_failure(
                        &mut lifecycle,
                        None,
                        error.to_failure_cause(),
                        notifier,
                    ) {
                        Some(outcome) => return outcome,
                        None => continue,
                    }
                }
            };

            if facts.another_instance_running {
                return LoopOutcome::FocusExisting;
            }

            if let Some(cause) = reconcile::preflight_violation(&facts, &self.desired) {
                match self.handle_failure(&mut lifecycle, None, cause, notifier) {
                    Some(outcome) => return outcome,
                    None => continue,
                }
            }

            let Some(action) = reconcile::reconcile(&facts, &self.desired)
                .into_iter()
                .next()
            else {
                return self.confirm_ready(&mut lifecycle, notifier, cancel);
            };

            if matches!(action, RepairAction::FocusExistingWindow) {
                return LoopOutcome::FocusExisting;
            }

            match self.apply_gated(&mut lifecycle, &action, notifier, cancel) {
                Ok(ApplyOutcome::Progressed) => {
                    notifier.notify(&DomainEvent::RepairApplied {
                        action: action.clone(),
                    });
                    self.advance_to(&mut lifecycle, &action);
                }
                Ok(ApplyOutcome::Ready(ticket)) => {
                    self.advance_to(&mut lifecycle, &action);
                    let _ = lifecycle.enter(EnginePhase::EngineReady);
                    notifier.notify(&DomainEvent::EngineReady {
                        version_set: self.version_set(),
                    });
                    return LoopOutcome::Ready { ticket, lifecycle };
                }
                Err(EngineError::Cancelled) => return LoopOutcome::Cancelled { lifecycle },
                Err(error) => {
                    if let Some(outcome) = self.handle_failure(
                        &mut lifecycle,
                        Some(&action),
                        error.to_failure_cause(),
                        notifier,
                    ) {
                        return outcome;
                    }
                }
            }
        }
    }

    /// `apply`, but with a cancel signal that reads as permanently UNSET once
    /// `action`'s phase is past the point of no return — contract
    /// app-engine.md §6: "la cancelación se rechaza [...] lo declara antes,
    /// nunca después". The real signal is left untouched; a request made too
    /// late is simply never honored, not silently lost or errored.
    fn apply_gated(
        &self,
        lifecycle: &mut EngineLifecycle,
        action: &RepairAction,
        notifier: &dyn Notifier,
        cancel: &CancelSignal,
    ) -> Result<ApplyOutcome, EngineError> {
        let target_phase = phase_for(action).unwrap_or_else(|| lifecycle.phase());
        if phase_past_point_of_no_return(target_phase) {
            self.driver.apply(action, notifier, &CancelSignal::new())
        } else {
            self.driver.apply(action, notifier, cancel)
        }
    }

    /// Reconcile found nothing left to do. If a ticket had been minted THIS
    /// run we would already have returned via `ApplyOutcome::Ready` — landing
    /// here on the very first observe means the engine was already fully up
    /// from a PREVIOUS session (adopted, not recreated). `up` is documented
    /// idempotent (contract app-engine.md §4) and is the only place a ticket
    /// is minted (FR-011: renewed on every engine start), so re-invoking it
    /// is exactly what "reopened against an already-running engine" means.
    fn confirm_ready(
        &self,
        lifecycle: &mut EngineLifecycle,
        notifier: &dyn Notifier,
        cancel: &CancelSignal,
    ) -> LoopOutcome {
        self.advance_to(lifecycle, &RepairAction::StartContainer);
        match self.apply_gated(lifecycle, &RepairAction::StartContainer, notifier, cancel) {
            Ok(ApplyOutcome::Ready(ticket)) => {
                let _ = lifecycle.enter(EnginePhase::EngineReady);
                notifier.notify(&DomainEvent::EngineReady {
                    version_set: self.version_set(),
                });
                LoopOutcome::Ready {
                    ticket,
                    lifecycle: lifecycle.clone(),
                }
            }
            Ok(ApplyOutcome::Progressed) => {
                let cause = FailureCause {
                    code: FailureCode::CliPorcelainUnsupported,
                    message: "up no entregó un vale de arranque".to_string(),
                    retryable: false,
                };
                notifier.notify(&DomainEvent::EngineDegraded { cause });
                LoopOutcome::Degraded {
                    lifecycle: lifecycle.clone(),
                }
            }
            Err(EngineError::Cancelled) => LoopOutcome::Cancelled {
                lifecycle: lifecycle.clone(),
            },
            Err(error) => {
                notifier.notify(&DomainEvent::EngineDegraded {
                    cause: error.to_failure_cause(),
                });
                LoopOutcome::Degraded {
                    lifecycle: lifecycle.clone(),
                }
            }
        }
    }

    fn advance_to(&self, lifecycle: &mut EngineLifecycle, action: &RepairAction) {
        if let Some(phase) = phase_for(action) {
            if lifecycle.phase() != phase {
                let _ = lifecycle.enter(phase);
            }
        }
    }

    /// Records a failure and either resolves to `Degraded` (returned to the
    /// caller) or sleeps out the backoff and signals "keep looping" (`None`).
    fn handle_failure(
        &self,
        lifecycle: &mut EngineLifecycle,
        action: Option<&RepairAction>,
        cause: FailureCause,
        notifier: &dyn Notifier,
    ) -> Option<LoopOutcome> {
        match lifecycle.fail(action, cause.clone()) {
            Ok(FailOutcome::Repairing) => {
                self.clock.sleep(backoff_for(lifecycle.attempt()));
                None
            }
            Ok(FailOutcome::Degraded) => {
                notifier.notify(&DomainEvent::NoProgressDetected {
                    action: action.cloned(),
                    code: cause.code,
                });
                notifier.notify(&DomainEvent::EngineDegraded { cause });
                Some(LoopOutcome::Degraded {
                    lifecycle: lifecycle.clone(),
                })
            }
            // A rejected transition here is a domain-modeling bug, not a
            // runtime condition (e.g. two failure sources disagreeing about
            // the current episode) — fail loudly into Degraded rather than
            // loop forever or panic the boot thread.
            Err(_illegal) => {
                notifier.notify(&DomainEvent::EngineDegraded { cause });
                Some(LoopOutcome::Degraded {
                    lifecycle: lifecycle.clone(),
                })
            }
        }
    }

    fn version_set(&self) -> VersionSet {
        VersionSet {
            app: self.app_version.clone(),
            engine: self.desired.engine_image.clone(),
            companion: self.desired.companion_image.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{
        Arch, Bytes, CompanionContainers, CompanionHealth, ContainerFact, DaemonHealth, HostFacts,
        HostOs, ImageRef, LocalStateFact, Port,
    };
    use crate::ports::fakes::{
        EngineErrorKind, FakeClock, RecordingNotifier, ScriptedDriver, ScriptedProbe,
    };

    const GIB: u64 = 1024 * 1024 * 1024;

    fn engine_image() -> ImageRef {
        ImageRef::new("ghcr.io/devwspito/safent", "sha256:engine-good").unwrap()
    }

    fn desired() -> DesiredState {
        DesiredState {
            engine_image: engine_image(),
            companion_image: None,
            machine: None, // Linux desired state — no machine chain to satisfy
            min_free_disk_bytes: Bytes(4 * GIB),
            min_total_memory_bytes: Bytes(8 * GIB),
        }
    }

    fn converged_facts() -> HostFacts {
        HostFacts {
            os: HostOs::Linux,
            arch: Arch::Amd64,
            free_disk_bytes: Bytes(20 * GIB),
            total_memory_bytes: Bytes(16 * GIB),
            runtime_staged: true,
            runtime_hash_ok: true,
            machines: vec![],
            engine_container: Some(ContainerFact {
                exists: true,
                running: true,
                image_digest: Some("sha256:engine-good".into()),
            }),
            local_engine_image_digest: Some("sha256:engine-good".into()),
            local_companion_image_digest: None,
            published_port: Some(Port(37013)),
            data_volume: true,
            companion_scaffold: false,
            companion_containers: CompanionContainers::default(),
            companion_health: CompanionHealth::Unknown,
            daemon_health: DaemonHealth::Healthy,
            app_version: SemVer::parse("0.2.0").unwrap(),
            user_ns_allowed: true,
            helper_installed: true,
            local_state: LocalStateFact::Trusted,
            another_instance_running: false,
        }
    }

    fn service(probe: ScriptedProbe, driver: ScriptedDriver) -> (BootService, Arc<FakeClock>) {
        let clock = Arc::new(FakeClock::new());
        let svc = BootService::new(
            Arc::new(probe),
            Arc::new(driver),
            clock.clone(),
            desired(),
            SemVer::parse("0.2.0").unwrap(),
        );
        (svc, clock)
    }

    fn ticket() -> BootstrapTicket {
        BootstrapTicket::new("http://127.0.0.1:37013/?k=test-ticket".to_string())
    }

    #[test]
    fn already_converged_reissues_up_once_for_a_fresh_ticket() {
        // No cache to trust, so the loop re-observes for real — an ALREADY
        // fully running engine (adopted from a previous session) means
        // reconcile converges on the very first observation, and the only
        // way to get a ticket is re-invoking `up` (idempotent).
        let probe = ScriptedProbe::new(vec![Ok(converged_facts())]);
        let driver = ScriptedDriver::new(vec![(
            RepairAction::StartContainer,
            Ok(ApplyOutcome::Ready(ticket())),
        )]);
        let (service, _clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();
        let cancel = CancelSignal::new();

        match service.run(&notifier, &cancel) {
            LoopOutcome::Ready { ticket, lifecycle } => {
                assert_eq!(ticket.expose(), "http://127.0.0.1:37013/?k=test-ticket");
                assert_eq!(lifecycle.phase(), EnginePhase::EngineReady);
            }
            other => panic!("expected Ready, got {other:?}"),
        }
        assert!(notifier
            .events()
            .iter()
            .any(|e| matches!(e, DomainEvent::EngineReady { .. })));
    }

    #[test]
    fn converges_through_a_realistic_chain_of_actions() {
        let mut fresh = converged_facts();
        fresh.runtime_staged = false;
        fresh.runtime_hash_ok = false;
        fresh.engine_container = None;
        fresh.local_engine_image_digest = None;
        fresh.published_port = None;

        let mut runtime_staged = fresh.clone();
        runtime_staged.runtime_staged = true;
        runtime_staged.runtime_hash_ok = true;

        let mut image_pulled = runtime_staged.clone();
        image_pulled.local_engine_image_digest = Some("sha256:engine-good".into());

        let probe = ScriptedProbe::new(vec![
            Ok(fresh),             // -> StageRuntime
            Ok(runtime_staged),    // -> PullEngine
            Ok(image_pulled), // -> ChoosePort (no container, no port on record: CreateContainer)
            Ok(converged_facts()), // after `up`, fully converged
        ]);
        let driver = ScriptedDriver::new(vec![
            (RepairAction::StageRuntime, Ok(ApplyOutcome::Progressed)),
            (
                RepairAction::PullEngine(engine_image()),
                Ok(ApplyOutcome::Progressed),
            ),
            (
                RepairAction::CreateContainer,
                Ok(ApplyOutcome::Ready(ticket())),
            ),
        ]);
        let (service, _clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();

        match service.run(&notifier, &CancelSignal::new()) {
            LoopOutcome::Ready { lifecycle, .. } => {
                assert_eq!(lifecycle.phase(), EnginePhase::EngineReady)
            }
            other => panic!("expected Ready, got {other:?}"),
        }
    }

    #[test]
    fn a_second_instance_focuses_the_existing_window_without_touching_anything() {
        let mut facts = converged_facts();
        facts.another_instance_running = true;
        let probe = ScriptedProbe::new(vec![Ok(facts)]);
        let driver = ScriptedDriver::new(vec![]);
        let (service, _clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();

        assert!(matches!(
            service.run(&notifier, &CancelSignal::new()),
            LoopOutcome::FocusExisting
        ));
    }

    #[test]
    fn same_action_failing_twice_degrades_with_backoff_between_attempts() {
        let mut fresh = converged_facts();
        fresh.runtime_staged = false;
        fresh.runtime_hash_ok = false;

        let probe = ScriptedProbe::new(vec![Ok(fresh)]);
        let driver = ScriptedDriver::new(vec![(
            RepairAction::StageRuntime,
            Err(EngineErrorKind::Io("registro no disponible".to_string())),
        )]);
        // ScriptedDriver consumes its scripted response on first use; a
        // second `apply()` call for an action with no script left errors
        // with a distinct message, which still counts as "the same action,
        // a nonzero-th failure" for the purposes of this test since we only
        // assert on the FINAL outcome + that backoff was requested at least
        // once — the exact code differing on call 2 does not matter here.
        let (service, clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();

        let outcome = service.run(&notifier, &CancelSignal::new());
        assert!(
            matches!(outcome, LoopOutcome::Degraded { .. }),
            "{outcome:?}"
        );
        assert!(
            !clock.requested_sleeps().is_empty(),
            "must back off between attempts, not spin"
        );
        assert!(notifier
            .events()
            .iter()
            .any(|e| matches!(e, DomainEvent::EngineDegraded { .. })));
    }

    #[test]
    fn cancelling_before_the_point_of_no_return_stops_the_loop() {
        let mut fresh = converged_facts();
        fresh.runtime_staged = false;
        fresh.runtime_hash_ok = false;

        let probe = ScriptedProbe::new(vec![Ok(fresh)]);
        let driver = ScriptedDriver::new(vec![]);
        let (service, _clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();
        let cancel = CancelSignal::new();
        cancel.set();

        assert!(matches!(
            service.run(&notifier, &cancel),
            LoopOutcome::Cancelled { .. }
        ));
    }

    #[test]
    fn cancelling_past_the_point_of_no_return_is_rejected() {
        // Already inside `up` (EngineStarting, past the point of no return) —
        // the driver is scripted to ignore cancellation the way the real
        // adapter would past that point, and this test proves BootService
        // does not even ask it to: it hands down a fresh, unset signal.
        let mut image_pulled = converged_facts();
        image_pulled.engine_container = None;
        image_pulled.published_port = None;

        let probe = ScriptedProbe::new(vec![Ok(image_pulled)]);
        let driver = ScriptedDriver::new(vec![(
            RepairAction::CreateContainer,
            Ok(ApplyOutcome::Ready(ticket())),
        )]);
        let (service, _clock) = service(probe, driver);
        let notifier = RecordingNotifier::new();
        let cancel = CancelSignal::new();
        cancel.set(); // requested BEFORE this run — must still be rejected once past the gate

        match service.run(&notifier, &cancel) {
            LoopOutcome::Ready { .. } => {}
            other => panic!(
                "expected Ready (cancel rejected past the point of no return), got {other:?}"
            ),
        }
    }
}
