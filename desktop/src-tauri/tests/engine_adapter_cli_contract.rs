//! Black-box tests for `engine_adapter.rs` against a FAKE `safent` binary —
//! a POSIX shell script that emits recorded NDJSON, exactly as
//! contracts/app-engine.md documents. No container, no network, no real
//! podman (Constitution Principle V). `#[path]` pulls the real source files
//! into this standalone test binary — the crate has no `[lib]` target, and
//! adding one is out of this lane's file ownership.

#[allow(dead_code)]
#[path = "../src/domain.rs"]
mod domain;
#[allow(dead_code)]
#[path = "../src/engine_adapter.rs"]
mod engine_adapter;
// This binary only exercises the REAL adapter — `ports::fakes` (ScriptedDriver,
// FakeClock, ScriptedProbe) exist for reconcile/boot tests, not this file.
#[allow(dead_code)]
#[path = "../src/ports.rs"]
mod ports;

use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use domain::{Arch, DomainEvent, FailureCode, HostOs, ImageRef, Port, RepairAction, Stage};
use engine_adapter::{EmbeddedCliConfig, EmbeddedCliDriver};
use ports::fakes::RecordingNotifier;
use ports::{ApplyOutcome, EngineDriver, EngineError, EngineProbe};

static SCRIPT_COUNTER: AtomicU32 = AtomicU32::new(0);

/// Writes `body` as an executable POSIX shell script and returns its path —
/// the fake CLI for one test. PID + counter alone is not enough: the OS
/// reuses PIDs across SEPARATE `cargo test` invocations, and a leftover
/// script from a previous run's still-unwinding child process (e.g. one this
/// suite just `kill()`ed for a stall/timeout test) can leave the path
/// genuinely busy — `execve` then fails with ETXTBSY ("Text file busy") on
/// the next run that happens to compute the same name. A nanosecond
/// timestamp added to PID + counter makes that collision astronomically
/// unlikely instead of merely unlikely.
fn fake_cli(body: &str) -> PathBuf {
    let n = SCRIPT_COUNTER.fetch_add(1, Ordering::SeqCst);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("system clock before UNIX_EPOCH")
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "safent-fake-cli-{}-{nanos}-{n}.sh",
        std::process::id()
    ));
    let mut file = std::fs::File::create(&path).expect("create fake cli script");
    writeln!(file, "#!/bin/sh").unwrap();
    write!(file, "{body}").unwrap();
    drop(file);
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
    path
}

fn config(cli_path: PathBuf) -> EmbeddedCliConfig {
    let mut cfg = EmbeddedCliConfig::with_defaults(
        cli_path,
        PathBuf::from("/usr/bin/true"), // podman_path — unused by these fakes, just needs to be set
        std::env::temp_dir(),
        ImageRef::new("ghcr.io/devwspito/safent", "sha256:engine-good").unwrap(),
        None,
    );
    cfg.stall_timeout = Duration::from_secs(2);
    cfg.hard_timeout = Duration::from_secs(10);
    cfg
}

#[test]
fn observe_maps_a_valid_facts_event() {
    // Field names/casing here are the REAL `cmd_facts` shape (camelCase,
    // verified against the actual `safent` script — see
    // tests/engine_adapter_real_cli_contract.rs), not a guess: this test
    // would have stayed green with the old snake_case assumption while the
    // real CLI silently failed every `observe()` call.
    let script = fake_cli(
        r#"
if [ "$1" = "facts" ]; then
  cat <<'JSON'
{"t":"facts","facts":{"os":"linux","arch":"arm64","freeDiskBytes":21474836480,"totalMemoryBytes":17179869184,"runtimeStaged":true,"runtimeHashOk":true,"machines":[],"engineContainer":{"exists":false,"running":false,"imageDigest":null},"localEngineImageDigest":"sha256:engine-good","localCompanionImageDigest":null,"publishedPort":37013,"dataVolume":true,"companionScaffold":false,"companionContainers":{"running":0,"total":0},"companionHealth":"unknown","daemonHealth":"healthy","appVersion":"0.2.0","userNsAllowed":true,"helperInstalled":true}}
JSON
  exit 0
fi
echo "unexpected invocation: $*" >&2
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let facts = driver.observe().expect("observe should succeed");
    assert_eq!(facts.os, HostOs::Linux);
    assert_eq!(facts.arch, Arch::Arm64);
    assert!(facts.runtime_staged);
    assert!(facts.runtime_hash_ok);
    assert_eq!(facts.published_port, Some(Port(37013)));
    assert_eq!(
        facts.local_engine_image_digest.as_deref(),
        Some("sha256:engine-good")
    );
    assert!(!facts.another_instance_running);
}

#[test]
fn apply_streams_stage_progress_done_to_notifier_in_order() {
    let script = fake_cli(
        r#"
if [ "$1" = "stage-runtime" ]; then
  echo '{"t":"stage","id":"runtime_staging","label":"Preparando la base de ejecucion","total_bytes":1000}'
  echo '{"t":"progress","id":"runtime_staging","done":500,"total":1000,"unit":"bytes"}'
  echo '{"t":"done","id":"runtime_staging","ms":42}'
  exit 0
fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let notifier = RecordingNotifier::new();
    let outcome = driver
        .apply(
            &RepairAction::StageRuntime,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .expect("apply should succeed");
    assert!(matches!(outcome, ApplyOutcome::Progressed));

    let events = notifier.events();
    assert_eq!(events.len(), 3, "{events:?}");
    assert!(matches!(
        &events[0],
        DomainEvent::StageEntered {
            stage: Stage::RuntimeStaging,
            total_bytes: Some(1000),
            ..
        }
    ));
    assert!(matches!(
        &events[1],
        DomainEvent::StageProgressed {
            stage: Stage::RuntimeStaging,
            done: 500,
            total: Some(1000),
            ..
        }
    ));
    assert!(matches!(
        &events[2],
        DomainEvent::StageCompleted {
            stage: Stage::RuntimeStaging,
            duration_ms: 42
        }
    ));
}

#[test]
fn apply_maps_a_failed_event_to_a_reported_failure_cause() {
    let script = fake_cli(
        r#"
if [ "$1" = "ensure-images" ]; then
  echo '{"t":"stage","id":"pull_engine","label":"Descargando Safent","total_bytes":2500000000}'
  echo '{"t":"failed","id":"pull_engine","code":"registry_unreachable","detail":"no hay red","retryable":true}'
  exit 12
fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let notifier = RecordingNotifier::new();
    let image = ImageRef::new("ghcr.io/devwspito/safent", "sha256:engine-good").unwrap();
    let err = driver
        .apply(
            &RepairAction::PullEngine(image),
            &notifier,
            &ports::CancelSignal::new(),
        )
        .unwrap_err();
    match err {
        EngineError::Reported(cause) => {
            assert_eq!(cause.code, FailureCode::RegistryUnreachable);
            assert_eq!(cause.message, "no hay red");
            assert!(cause.retryable);
        }
        other => panic!("expected Reported, got {other:?}"),
    }
}

/// The exact scenario my task brief calls out by name: the bundled CLI does
/// not understand `--porcelain` yet (pre-T004) and prints its ordinary human
/// text instead of NDJSON. The adapter must fail CLOSED — promptly, with a
/// classified, retryable-as-a-normal-failure error — never hang, never
/// mis-parse the human text as data.
#[test]
fn pre_porcelain_cli_fails_closed_instead_of_hanging_or_misparsing() {
    let script = fake_cli(
        r#"
echo "[*] Comprobando el sistema..."
echo "[*] Todo listo."
exit 0
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let err = driver.observe().unwrap_err();
    assert!(
        matches!(err, EngineError::UnexpectedOutput { .. }),
        "{err:?}"
    );
    let cause = err.to_failure_cause();
    assert_eq!(cause.code, FailureCode::CliPorcelainUnsupported);
    assert!(
        !cause.retryable,
        "auto-repair must not hammer a CLI that cannot speak the protocol"
    );
}

#[test]
fn stalled_cli_times_out_instead_of_hanging_forever() {
    let script = fake_cli(
        r#"
echo '{"t":"stage","id":"runtime_staging","label":"Preparando","total_bytes":100}'
sleep 5
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script)); // stall_timeout = 2s (config())
    let notifier = RecordingNotifier::new();
    let started = Instant::now();
    let err = driver
        .apply(
            &RepairAction::StageRuntime,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .unwrap_err();
    assert!(matches!(err, EngineError::Timeout { .. }), "{err:?}");
    assert!(
        started.elapsed() < Duration::from_secs(8),
        "must not wait anywhere near the scripted sleep 5"
    );
}

#[test]
fn up_delivers_the_bootstrap_ticket_over_the_secret_fd_never_on_stdout() {
    let script = fake_cli(
        r#"
if [ "$1" = "up" ]; then
  echo '{"t":"stage","id":"container","label":"Arrancando"}'
  echo '{"t":"done","id":"container","ms":10}'
  echo '{"t":"ready","endpoint_ref":"stdout-secret"}'
  echo "http://127.0.0.1:37013/?k=super-secret-ticket" >&3
  exit 0
fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let notifier = RecordingNotifier::new();
    let outcome = driver
        .apply(
            &RepairAction::CreateContainer,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .expect("apply should succeed");
    match outcome {
        ApplyOutcome::Ready(ticket) => {
            assert_eq!(
                ticket.expose(),
                "http://127.0.0.1:37013/?k=super-secret-ticket"
            );
        }
        ApplyOutcome::Progressed => panic!("expected Ready"),
    }
    // Double-check the redaction invariant end to end, not just at the type
    // level: the ticket must never surface through ANY notified event.
    for event in notifier.events() {
        assert!(!format!("{event:?}").contains("super-secret-ticket"));
    }
}

#[test]
fn ready_without_a_secret_line_is_a_protocol_error_not_a_hang() {
    let script = fake_cli(
        r#"
if [ "$1" = "up" ]; then
  echo '{"t":"ready","endpoint_ref":"stdout-secret"}'
  exit 0
fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let notifier = RecordingNotifier::new();
    let started = Instant::now();
    let err = driver
        .apply(
            &RepairAction::CreateContainer,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .unwrap_err();
    assert!(matches!(err, EngineError::ReadyWithoutTicket), "{err:?}");
    assert!(started.elapsed() < Duration::from_secs(8));
}

#[test]
fn stop_succeeds_when_the_cli_says_so() {
    let script = fake_cli(
        r#"
if [ "$1" = "stop" ]; then exit 0; fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    driver.stop().expect("stop should succeed");
}

#[test]
fn stop_reports_a_nonzero_exit_instead_of_pretending_it_worked() {
    let script = fake_cli(
        r#"
if [ "$1" = "stop" ]; then echo "no pude parar el motor" >&2; exit 1; fi
exit 1
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let err = driver.stop().unwrap_err();
    assert!(matches!(err, EngineError::ProcessExited { .. }), "{err:?}");
}

#[test]
fn actions_with_no_cli_mapping_never_spawn_a_process() {
    // A path that does not exist: if the adapter ever tried to spawn it,
    // this would fail with Io, not UnsupportedByAdapter.
    let driver = EmbeddedCliDriver::new(config(PathBuf::from("/nonexistent/safent")));
    let notifier = RecordingNotifier::new();

    let err = driver
        .apply(
            &RepairAction::FocusExistingWindow,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .unwrap_err();
    assert!(
        matches!(err, EngineError::UnsupportedByAdapter { .. }),
        "{err:?}"
    );

    let err = driver
        .apply(
            &RepairAction::ReloadCompanionPresence,
            &notifier,
            &ports::CancelSignal::new(),
        )
        .unwrap_err();
    assert!(
        matches!(err, EngineError::UnsupportedByAdapter { .. }),
        "{err:?}"
    );
}

#[test]
fn output_past_the_cap_is_treated_as_a_protocol_failure_not_unbounded_growth() {
    let script = fake_cli(
        r#"
i=0
while [ $i -lt 500 ]; do
  echo '{"t":"progress","id":"runtime_staging","done":1,"total":2,"unit":"bytes"}'
  i=$((i + 1))
done
echo '{"t":"done","id":"runtime_staging","ms":1}'
"#,
    );
    let mut cfg = config(script);
    cfg.max_output_bytes = 256; // far below the ~30KB this script would print
    let driver = EmbeddedCliDriver::new(cfg);
    let notifier = RecordingNotifier::new();
    let started = Instant::now();
    let result = driver.apply(
        &RepairAction::StageRuntime,
        &notifier,
        &ports::CancelSignal::new(),
    );
    assert!(
        result.is_err(),
        "an output flood must not be accepted as success"
    );
    assert!(
        started.elapsed() < Duration::from_secs(8),
        "must not buffer the whole flood before giving up"
    );
}

#[test]
fn a_preset_cancel_signal_is_honored_before_the_first_line_and_kills_the_child() {
    let script = fake_cli(
        r#"
echo '{"t":"stage","id":"runtime_staging","label":"Preparando","total_bytes":100}'
sleep 5
"#,
    );
    let driver = EmbeddedCliDriver::new(config(script));
    let notifier = RecordingNotifier::new();
    let cancel = ports::CancelSignal::new();
    cancel.set();
    let started = Instant::now();
    let err = driver
        .apply(&RepairAction::StageRuntime, &notifier, &cancel)
        .unwrap_err();
    assert!(matches!(err, EngineError::Cancelled), "{err:?}");
    assert_eq!(err.to_failure_cause().code, FailureCode::CancelledByOwner);
    assert!(!err.to_failure_cause().retryable);
    assert!(
        started.elapsed() < Duration::from_secs(1),
        "cancel must be noticed immediately, not after a stall wait"
    );
}
