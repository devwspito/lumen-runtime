//! The ONLY implementation of `EngineProbe`/`EngineDriver` that talks to a
//! real `safent` binary (contracts/app-engine.md). Spawns it directly as an
//! argv array (never a shell string — see `spawn`), streams NDJSON off
//! stdout with a stall timeout + a hard timeout + an output cap, and hands
//! the bootstrap ticket to the caller through a dedicated pipe that never
//! touches stdout, argv, the environment, or a log line.
//!
//! T004 (a sibling lane, `RT` repo) is landing `--porcelain`/`facts --json`/
//! `SAFENT_PODMAN` on the CLI concurrently with this file. Until it ships,
//! every real invocation here fails CLOSED: the first line that is not valid
//! NDJSON is reported as `EngineError::UnexpectedOutput`, which
//! `to_failure_cause()` maps to `FailureCode::CliPorcelainUnsupported` —
//! never parsed as human text, never silently ignored, never hung. That is
//! an ordinary, retryable `EngineError` like any other, so `boot.rs`'s
//! degrade-after-no-progress rule applies to it unchanged.

use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::time::{Duration, Instant};

use serde::Deserialize;

use crate::domain::{
    Arch, BootstrapTicket, Bytes, CompanionContainers, CompanionHealth, ContainerFact,
    DaemonHealth, DomainEvent, FailureCause, FailureCode, HostFacts, HostOs, ImageRef,
    LocalStateFact, MachineFact, MachineName, MachineProvider, Port, ProgressUnit, RepairAction,
    SemVer, Stage,
};
use crate::ports::{ApplyOutcome, EngineDriver, EngineError, EngineProbe, Notifier};

/// Everything the adapter needs to talk to ONE bundled CLI. Built once at
/// boot time from the bundle layout + the runtime manifest (T010, another
/// lane) and handed to `EmbeddedCliDriver::new`.
#[derive(Debug, Clone)]
pub struct EmbeddedCliConfig {
    pub cli_path: PathBuf,
    pub podman_path: PathBuf,
    pub state_home: PathBuf,
    pub engine_image: ImageRef,
    pub companion_image: Option<ImageRef>,
    pub stall_timeout: Duration,
    pub hard_timeout: Duration,
    pub max_output_bytes: usize,
}

impl EmbeddedCliConfig {
    /// Conservative production defaults: 15 s without a single NDJSON line is
    /// well past "stalled" (contract app-engine.md §3.2 requires `progress`
    /// at least every 5 s while a stage is alive); 20 minutes covers the
    /// documented p95 for a first-run pull (spec NFR-001); 8 MiB of stdout is
    /// orders of magnitude more than the busiest real invocation ever needs.
    pub fn with_defaults(
        cli_path: PathBuf,
        podman_path: PathBuf,
        state_home: PathBuf,
        engine_image: ImageRef,
        companion_image: Option<ImageRef>,
    ) -> Self {
        Self {
            cli_path,
            podman_path,
            state_home,
            engine_image,
            companion_image,
            stall_timeout: Duration::from_secs(15),
            hard_timeout: Duration::from_secs(20 * 60),
            max_output_bytes: 8 * 1024 * 1024,
        }
    }
}

pub struct EmbeddedCliDriver {
    config: EmbeddedCliConfig,
}

impl EmbeddedCliDriver {
    pub fn new(config: EmbeddedCliConfig) -> Self {
        Self { config }
    }

    fn spawn(
        &self,
        verb: &str,
        args: &[String],
        secret: Option<&SecretPipe>,
    ) -> Result<Child, EngineError> {
        let mut cmd = Command::new(&self.config.cli_path);
        cmd.arg(verb);
        cmd.args(args);
        cmd.arg("--porcelain");
        cmd.env("PATH", augmented_path());
        cmd.env("SAFENT_PODMAN", &self.config.podman_path);
        cmd.env("SAFENT_NO_BROWSER", "1");
        cmd.env("SAFENT_NO_SELF_UPDATE", "1");
        cmd.env("SAFENT_IMAGE", self.config.engine_image.reference());
        if let Some(companion) = &self.config.companion_image {
            cmd.env("SAFENT_ADS_IMAGE", companion.reference());
        }
        cmd.env("SAFENT_STATE_HOME", &self.config.state_home);
        cmd.stdin(Stdio::null());
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        if let Some(secret) = secret {
            secret.install(&mut cmd);
        }
        cmd.spawn()
            .map_err(|e| EngineError::Io(format!("no pude ejecutar '{verb}': {e}")))
    }
}

impl EngineProbe for EmbeddedCliDriver {
    fn observe(&self) -> Result<HostFacts, EngineError> {
        let mut facts: Option<HostFacts> = None;
        let outcome = self.run_porcelain("facts", &["--json".to_string()], false, |event| {
            if let WireEvent::Facts { facts: wire } = event {
                facts = Some(map_host_facts(wire)?);
            }
            Ok(())
        })?;
        // A successful observation wins even over a nonzero trailing exit
        // (facts --json is documented pure — "no modifica nada"); only when
        // NO facts ever arrived does the exit code decide which error to
        // surface.
        match (facts, outcome.exit_ok) {
            (Some(facts), _) => Ok(facts),
            (None, false) => Err(EngineError::ProcessExited {
                code: outcome.exit_code,
                stderr_tail: outcome.stderr_tail,
            }),
            (None, true) => Err(EngineError::Protocol(
                "facts --json exited without emitting `facts`".to_string(),
            )),
        }
    }
}

impl EngineDriver for EmbeddedCliDriver {
    fn apply(
        &self,
        action: &RepairAction,
        notifier: &dyn Notifier,
    ) -> Result<ApplyOutcome, EngineError> {
        let (verb, args) = cli_invocation_for(action)?;
        let want_secret = verb == "up";
        let mut failure: Option<FailureCause> = None;
        let mut ready = false;

        let outcome = self.run_porcelain(verb, &args, want_secret, |event| {
            match event {
                WireEvent::Stage {
                    id,
                    label,
                    total_bytes,
                } => {
                    let stage = map_stage(&id)?;
                    notifier.notify(&DomainEvent::StageEntered {
                        stage,
                        label,
                        total_bytes,
                    });
                }
                WireEvent::Progress {
                    id,
                    done,
                    total,
                    unit,
                } => {
                    let stage = map_stage(&id)?;
                    let unit = map_progress_unit(&unit)?;
                    notifier.notify(&DomainEvent::StageProgressed {
                        stage,
                        done,
                        total,
                        unit,
                    });
                }
                WireEvent::Done { id, ms } => {
                    let stage = map_stage(&id)?;
                    notifier.notify(&DomainEvent::StageCompleted {
                        stage,
                        duration_ms: ms,
                    });
                }
                WireEvent::Failed {
                    id: _,
                    code,
                    detail,
                    retryable,
                } => {
                    let code = map_failure_code(&code)?;
                    failure = Some(FailureCause {
                        code,
                        message: detail,
                        retryable,
                    });
                }
                WireEvent::Ready { .. } => ready = true,
                WireEvent::Facts { .. } => {
                    return Err(EngineError::Protocol(
                        "unexpected `facts` event from a non-probe verb".to_string(),
                    ));
                }
            }
            Ok(())
        })?;

        // A `failed` event is authoritative over the raw exit code (contract
        // app-engine.md §2: 10..39 IS that event, reported previously) — it
        // must win even though the process also exited non-zero.
        if let Some(cause) = failure {
            return Err(EngineError::Reported(cause));
        }
        if ready {
            let line = outcome.secret_line.ok_or_else(|| {
                EngineError::Protocol("`ready` without a line on the secret fd".to_string())
            })?;
            return Ok(ApplyOutcome::Ready(BootstrapTicket::new(line)));
        }
        if !outcome.exit_ok {
            return Err(EngineError::ProcessExited {
                code: outcome.exit_code,
                stderr_tail: outcome.stderr_tail,
            });
        }
        Ok(ApplyOutcome::Progressed)
    }

    fn stop(&self) -> Result<(), EngineError> {
        // `stop` is a plain, fast, non-progress-bearing operation — it is not
        // part of the porcelain contract's verb table (app-engine.md §4), so
        // this bypasses run_porcelain entirely: no NDJSON expected, just a
        // bounded wait on the exit code.
        let mut cmd = Command::new(&self.config.cli_path);
        cmd.arg("stop");
        cmd.env("PATH", augmented_path());
        cmd.env("SAFENT_PODMAN", &self.config.podman_path);
        cmd.env("SAFENT_STATE_HOME", &self.config.state_home);
        cmd.stdin(Stdio::null());
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| EngineError::Io(format!("no pude ejecutar 'stop': {e}")))?;
        let stderr = child.stderr.take();
        wait_bounded(&mut child, self.config.hard_timeout, stderr)
    }
}

/// Waits for `child` to exit, killing it (and failing) if `timeout` elapses
/// first. Drains `stderr` on a side thread so a chatty exit never blocks the
/// wait itself.
fn wait_bounded(
    child: &mut Child,
    timeout: Duration,
    stderr: Option<impl Read + Send + 'static>,
) -> Result<(), EngineError> {
    let tail = stderr.map(|s| {
        std::thread::spawn(move || {
            let mut buf = String::new();
            let _ = BufReader::new(s).take(4096).read_to_string(&mut buf);
            buf
        })
    });
    let deadline = Instant::now() + timeout;
    loop {
        match child
            .try_wait()
            .map_err(|e| EngineError::Io(e.to_string()))?
        {
            Some(status) if status.success() => return Ok(()),
            Some(status) => {
                let stderr_tail = tail.and_then(|h| h.join().ok()).unwrap_or_default();
                return Err(EngineError::ProcessExited {
                    code: status.code(),
                    stderr_tail,
                });
            }
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(EngineError::Timeout { after: timeout });
            }
            None => std::thread::sleep(Duration::from_millis(50)),
        }
    }
}

/// A GUI-launched app inherits a minimal PATH (main.rs's `augmented_path`
/// carries the same note for the legacy install flow) — hand the child the
/// common install locations too.
fn augmented_path() -> String {
    let mut parts = Vec::new();
    if let Ok(p) = std::env::var("PATH") {
        if !p.is_empty() {
            parts.push(p);
        }
    }
    for extra in [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/podman/bin",
        "/usr/bin",
        "/bin",
    ] {
        parts.push(extra.to_string());
    }
    parts.join(":")
}

/// Maps the closed `RepairAction` vocabulary onto the CLI's verb table
/// (contract app-engine.md §4). Several distinct actions collapse onto the
/// SAME idempotent verb on purpose — the CLI decides adopt-vs-create-vs-start
/// internally too (defense in depth); the Rust-side distinction exists so
/// `reconcile`'s tests can prove the RIGHT SITUATION was recognized, not to
/// steer the CLI with flags the contract does not define.
fn cli_invocation_for(action: &RepairAction) -> Result<(&'static str, Vec<String>), EngineError> {
    match action {
        RepairAction::StageRuntime => Ok(("stage-runtime", vec![])),
        RepairAction::AdoptMachine(_)
        | RepairAction::CreateMachine
        | RepairAction::StartMachine(_)
        | RepairAction::InstallPrivilegedHelper => Ok(("ensure-machine", vec![])),
        RepairAction::PullEngine(_) | RepairAction::PullCompanion(_) => {
            Ok(("ensure-images", vec![]))
        }
        RepairAction::ChoosePort
        | RepairAction::CreateContainer
        | RepairAction::StartContainer
        | RepairAction::RecreateEngine => Ok(("up", vec![])),
        RepairAction::EnsureCompanionScaffold => Ok(("companion", vec!["repair".to_string()])),
        RepairAction::ComposeCompanionUp(_) => Ok(("companion", vec!["install".to_string()])),
        // Not part of this adapter's contract at all: ReloadCompanionPresence is a
        // daemon dbus verb (T017, a different bounded context); FocusExistingWindow
        // is a pure window action boot.rs must special-case and never hand here.
        RepairAction::ReloadCompanionPresence | RepairAction::FocusExistingWindow => {
            Err(EngineError::UnsupportedByAdapter {
                action: action.clone(),
            })
        }
    }
}

// ---------------------------------------------------------------------------
// NDJSON streaming: spawn, drain stdout/stderr/secret-fd concurrently, honor
// a stall timeout + a hard timeout + an output cap. Never a shell string —
// `Command::new(cli_path)` execve's the target directly; argv are discrete
// OS-level arguments, never concatenated into anything a shell parses.
// ---------------------------------------------------------------------------

struct RunOutcome {
    secret_line: Option<String>,
    exit_ok: bool,
    exit_code: Option<i32>,
    stderr_tail: String,
}

enum ReaderMsg {
    StdoutLine(String),
    StderrLine(String),
    SecretLine(String),
    ReaderClosed,
}

impl EmbeddedCliDriver {
    fn run_porcelain(
        &self,
        verb: &str,
        extra_args: &[String],
        want_secret: bool,
        mut on_event: impl FnMut(WireEvent) -> Result<(), EngineError>,
    ) -> Result<RunOutcome, EngineError> {
        let mut args = extra_args.to_vec();
        let secret = if want_secret {
            args.push("--secret-fd".to_string());
            args.push("3".to_string());
            Some(SecretPipe::new()?)
        } else {
            None
        };

        let mut child = self.spawn(verb, &args, secret.as_ref())?;
        let stdout = child.stdout.take().expect("stdout was piped");
        let stderr = child.stderr.take().expect("stderr was piped");

        let (tx, rx) = mpsc::channel();
        let mut open_readers = 2u8;
        spawn_stream_reader(
            stdout,
            tx.clone(),
            ReaderMsg::StdoutLine,
            self.config.max_output_bytes,
        );
        spawn_stream_reader(
            stderr,
            tx.clone(),
            ReaderMsg::StderrLine,
            self.config.max_output_bytes,
        );
        if let Some(secret) = secret {
            open_readers += 1;
            spawn_stream_reader(secret.into_reader(), tx, ReaderMsg::SecretLine, 4096);
        }

        let mut stderr_tail = String::new();
        let mut secret_line = None;
        let deadline = Instant::now() + self.config.hard_timeout;
        let mut last_activity = Instant::now();

        while open_readers > 0 {
            let time_left = deadline.saturating_duration_since(Instant::now());
            if time_left.is_zero() {
                return Err(kill_and_timeout(&mut child, self.config.hard_timeout));
            }
            let budget = self.config.stall_timeout.min(time_left);
            match rx.recv_timeout(budget) {
                Ok(ReaderMsg::StdoutLine(line)) => {
                    last_activity = Instant::now();
                    handle_stdout_line(&mut child, &line, &mut on_event)?;
                }
                Ok(ReaderMsg::StderrLine(line)) => {
                    last_activity = Instant::now();
                    push_capped(&mut stderr_tail, &line);
                }
                Ok(ReaderMsg::SecretLine(line)) => {
                    last_activity = Instant::now();
                    secret_line = Some(line);
                }
                Ok(ReaderMsg::ReaderClosed) => open_readers -= 1,
                Err(RecvTimeoutError::Timeout) => {
                    if last_activity.elapsed() >= self.config.stall_timeout {
                        return Err(kill_and_timeout(&mut child, self.config.stall_timeout));
                    }
                }
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }

        // The exit code alone is NOT the verdict here: contract app-engine.md
        // §2 says codes 10..39 correspond to a `failed` event already emitted
        // (the caller's `on_event` already saw it) and only code 1 means
        // "unclassified". Deciding which applies needs what `on_event`
        // learned, which this function does not see — so it hands back the
        // raw exit outcome and lets `observe`/`apply` decide, instead of
        // guessing here and shadowing a real classification.
        let status = child.wait().map_err(|e| EngineError::Io(e.to_string()))?;
        let stderr_tail = if stderr_tail.is_empty() {
            "(sin salida)".to_string()
        } else {
            stderr_tail
        };
        Ok(RunOutcome {
            secret_line,
            exit_ok: status.success(),
            exit_code: status.code(),
            stderr_tail,
        })
    }
}

fn handle_stdout_line(
    child: &mut Child,
    line: &str,
    on_event: &mut impl FnMut(WireEvent) -> Result<(), EngineError>,
) -> Result<(), EngineError> {
    let event = match serde_json::from_str::<WireEvent>(line) {
        Ok(event) => event,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(EngineError::UnexpectedOutput {
                line: line.to_string(),
            });
        }
    };
    if let Err(e) = on_event(event) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e);
    }
    Ok(())
}

fn kill_and_timeout(child: &mut Child, after: Duration) -> EngineError {
    let _ = child.kill();
    let _ = child.wait();
    EngineError::Timeout { after }
}

fn push_capped(buffer: &mut String, line: &str) {
    const CAP: usize = 4096;
    if buffer.len() >= CAP {
        return;
    }
    if !buffer.is_empty() {
        buffer.push('\n');
    }
    buffer.push_str(line);
    buffer.truncate(CAP.min(buffer.len()));
}

fn spawn_stream_reader<R: Read + Send + 'static>(
    reader: R,
    tx: mpsc::Sender<ReaderMsg>,
    wrap: fn(String) -> ReaderMsg,
    max_bytes: usize,
) {
    std::thread::spawn(move || {
        let mut buffered = BufReader::new(reader);
        let mut total = 0usize;
        loop {
            let mut raw = String::new();
            match buffered.read_line(&mut raw) {
                Ok(0) => break,
                Ok(n) => {
                    total += n;
                    let line = raw.trim_end_matches(['\n', '\r']).to_string();
                    if tx.send(wrap(line)).is_err() || total > max_bytes {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        let _ = tx.send(ReaderMsg::ReaderClosed);
    });
}

// ---------------------------------------------------------------------------
// Wire contract (contracts/app-engine.md §3). Deliberately NOT `domain`
// types: these derive `serde::Deserialize`, which the domain layer must
// never depend on. Every `map_*` function below is the one place that
// crosses from "what the CLI said" to "what the domain understands".
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
#[serde(tag = "t")]
enum WireEvent {
    #[serde(rename = "stage")]
    Stage {
        id: String,
        label: String,
        total_bytes: Option<u64>,
    },
    #[serde(rename = "progress")]
    Progress {
        id: String,
        done: u64,
        total: Option<u64>,
        unit: String,
    },
    #[serde(rename = "done")]
    Done { id: String, ms: u64 },
    #[serde(rename = "failed")]
    Failed {
        id: String,
        code: String,
        detail: String,
        retryable: bool,
    },
    #[serde(rename = "facts")]
    Facts { facts: WireHostFacts },
    #[serde(rename = "ready")]
    Ready { endpoint_ref: String },
}

#[derive(Debug, Deserialize)]
struct WireHostFacts {
    os: String,
    arch: String,
    free_disk_bytes: u64,
    total_memory_bytes: u64,
    runtime_staged: bool,
    runtime_hash_ok: bool,
    #[serde(default)]
    machines: Vec<WireMachineFact>,
    engine_container: Option<WireContainerFact>,
    local_engine_image_digest: Option<String>,
    local_companion_image_digest: Option<String>,
    published_port: Option<u16>,
    data_volume: bool,
    companion_scaffold: bool,
    #[serde(default)]
    companion_containers_running: u32,
    #[serde(default)]
    companion_containers_total: u32,
    companion_health: String,
    daemon_health: String,
    app_version: String,
    user_ns_allowed: bool,
    helper_installed: bool,
    #[serde(default)]
    another_instance_running: bool,
}

#[derive(Debug, Deserialize)]
struct WireMachineFact {
    name: String,
    provider: String,
    rootful: bool,
    running: bool,
    ours: bool,
    cpus: u32,
    memory_bytes: u64,
    os_version: String,
}

#[derive(Debug, Deserialize)]
struct WireContainerFact {
    exists: bool,
    running: bool,
    image_digest: Option<String>,
}

fn map_host_facts(wire: WireHostFacts) -> Result<HostFacts, EngineError> {
    let app_version = SemVer::parse(&wire.app_version)
        .map_err(|_| EngineError::Protocol(format!("invalid app_version: {}", wire.app_version)))?;
    Ok(HostFacts {
        os: map_os(&wire.os),
        arch: map_arch(&wire.arch),
        free_disk_bytes: Bytes(wire.free_disk_bytes),
        total_memory_bytes: Bytes(wire.total_memory_bytes),
        runtime_staged: wire.runtime_staged,
        runtime_hash_ok: wire.runtime_hash_ok,
        machines: wire.machines.into_iter().map(map_machine_fact).collect(),
        engine_container: wire.engine_container.map(map_container_fact),
        local_engine_image_digest: wire.local_engine_image_digest,
        local_companion_image_digest: wire.local_companion_image_digest,
        published_port: wire.published_port.map(Port),
        data_volume: wire.data_volume,
        companion_scaffold: wire.companion_scaffold,
        companion_containers: CompanionContainers {
            running: wire.companion_containers_running,
            total: wire.companion_containers_total,
        },
        companion_health: map_companion_health(&wire.companion_health),
        daemon_health: map_daemon_health(&wire.daemon_health),
        app_version,
        user_ns_allowed: wire.user_ns_allowed,
        helper_installed: wire.helper_installed,
        // The CLI has no notion of the APP's own state.json — that cache
        // belongs to a layer this adapter does not own (StateStore, out of
        // T009's scope). A live probe is always Trusted by construction:
        // there is nothing cached here to distrust.
        local_state: LocalStateFact::Trusted,
        another_instance_running: wire.another_instance_running,
    })
}

fn map_os(raw: &str) -> HostOs {
    match raw {
        "macos" => HostOs::MacOs,
        "linux" => HostOs::Linux,
        _ => HostOs::Unsupported,
    }
}

fn map_arch(raw: &str) -> Arch {
    match raw {
        "arm64" | "aarch64" => Arch::Arm64,
        "amd64" | "x86_64" => Arch::Amd64,
        _ => Arch::Unsupported,
    }
}

fn map_companion_health(raw: &str) -> CompanionHealth {
    match raw {
        "reachable" => CompanionHealth::Reachable,
        "unreachable" => CompanionHealth::Unreachable,
        _ => CompanionHealth::Unknown,
    }
}

fn map_daemon_health(raw: &str) -> DaemonHealth {
    match raw {
        "healthy" => DaemonHealth::Healthy,
        "unhealthy" => DaemonHealth::Unhealthy,
        _ => DaemonHealth::Unknown,
    }
}

fn map_machine_provider(raw: &str) -> MachineProvider {
    match raw {
        "applehv" => MachineProvider::AppleHv,
        "qemu" => MachineProvider::Qemu,
        "hyperv" => MachineProvider::HyperV,
        "wsl" => MachineProvider::Wsl,
        other => MachineProvider::Other(other.to_string()),
    }
}

fn map_machine_fact(wire: WireMachineFact) -> MachineFact {
    MachineFact {
        name: MachineName(wire.name),
        provider: map_machine_provider(&wire.provider),
        rootful: wire.rootful,
        running: wire.running,
        ours: wire.ours,
        cpus: wire.cpus,
        memory_bytes: Bytes(wire.memory_bytes),
        os_version: wire.os_version,
    }
}

fn map_container_fact(wire: WireContainerFact) -> ContainerFact {
    ContainerFact {
        exists: wire.exists,
        running: wire.running,
        image_digest: wire.image_digest,
    }
}

/// The CLI's `StageId` vocabulary is CLOSED (contract app-engine.md §3) — an
/// id this adapter does not recognize is a protocol violation, not a fact to
/// shrug off with a default.
fn map_stage(raw: &str) -> Result<Stage, EngineError> {
    Ok(match raw {
        "preflight" => Stage::Preflight,
        "runtime_staging" => Stage::RuntimeStaging,
        "machine" => Stage::Machine,
        "pull_engine" => Stage::PullEngine,
        "pull_companion" => Stage::PullCompanion,
        "container" => Stage::Container,
        "health" => Stage::Health,
        "companion_scaffold" => Stage::CompanionScaffold,
        "companion_up" => Stage::CompanionUp,
        "companion_reload" => Stage::CompanionReload,
        "backup" => Stage::Backup,
        "restore" => Stage::Restore,
        "cleanup" => Stage::Cleanup,
        other => return Err(EngineError::Protocol(format!("unknown stage id: {other}"))),
    })
}

fn map_progress_unit(raw: &str) -> Result<ProgressUnit, EngineError> {
    Ok(match raw {
        "bytes" => ProgressUnit::Bytes,
        "layers" => ProgressUnit::Layers,
        "steps" => ProgressUnit::Steps,
        other => {
            return Err(EngineError::Protocol(format!(
                "unknown progress unit: {other}"
            )))
        }
    })
}

/// The CLI's `FailureCode` vocabulary is CLOSED (contract app-engine.md §3,
/// 20 codes) — same fail-closed rule as `map_stage`.
fn map_failure_code(raw: &str) -> Result<FailureCode, EngineError> {
    Ok(match raw {
        "unsupported_os" => FailureCode::UnsupportedOs,
        "unsupported_arch" => FailureCode::UnsupportedArch,
        "insufficient_disk" => FailureCode::InsufficientDisk,
        "insufficient_memory" => FailureCode::InsufficientMemory,
        "runtime_hash_mismatch" => FailureCode::RuntimeHashMismatch,
        "machine_create_failed" => FailureCode::MachineCreateFailed,
        "machine_start_failed" => FailureCode::MachineStartFailed,
        "userns_blocked" => FailureCode::UsernsBlocked,
        "helper_denied" => FailureCode::HelperDenied,
        "registry_unreachable" => FailureCode::RegistryUnreachable,
        "digest_mismatch" => FailureCode::DigestMismatch,
        "pull_interrupted" => FailureCode::PullInterrupted,
        "port_exhausted" => FailureCode::PortExhausted,
        "container_start_failed" => FailureCode::ContainerStartFailed,
        "daemon_unhealthy" => FailureCode::DaemonUnhealthy,
        "companion_network_conflict" => FailureCode::CompanionNetworkConflict,
        "companion_migration_failed" => FailureCode::CompanionMigrationFailed,
        "companion_unreachable" => FailureCode::CompanionUnreachable,
        "backup_failed" => FailureCode::BackupFailed,
        "restore_failed" => FailureCode::RestoreFailed,
        "clock_skew" => FailureCode::ClockSkew,
        other => {
            return Err(EngineError::Protocol(format!(
                "unknown failure code: {other}"
            )))
        }
    })
}

// ---------------------------------------------------------------------------
// Secret-fd delivery (contract app-engine.md §5). Unix only — Windows is out
// of scope for spec 028 (research.md "Decisión: Windows").
// ---------------------------------------------------------------------------

#[cfg(unix)]
mod secret_pipe {
    use std::io::Read;
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
    use std::os::unix::process::CommandExt;
    use std::process::Command;

    use crate::ports::EngineError;

    /// A pipe whose write end lands on fd 3 in the child (contract's default
    /// `--secret-fd 3`) and whose read end the PARENT keeps — and ONLY the
    /// parent's own copy of the write end is closed by `into_reader`, which
    /// is what lets `read()` see EOF once the child is done with it.
    pub struct SecretPipe {
        read_fd: OwnedFd,
        write_fd: RawFd,
    }

    impl SecretPipe {
        pub fn new() -> Result<Self, EngineError> {
            let mut fds: [libc::c_int; 2] = [0; 2];
            // SAFETY: `fds` is a valid, correctly-sized out-param for pipe2(2).
            // O_CLOEXEC keeps both ends from leaking into any OTHER child this
            // process spawns concurrently.
            let rc = unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) };
            if rc != 0 {
                return Err(EngineError::Io(std::io::Error::last_os_error().to_string()));
            }
            // SAFETY: `fds[0]` was just returned by a successful pipe2(2) and is
            // not owned anywhere else yet.
            let read_fd = unsafe { OwnedFd::from_raw_fd(fds[0]) };
            Ok(Self {
                read_fd,
                write_fd: fds[1],
            })
        }

        /// Registers a `pre_exec` hook that lands the write end on fd 3 in the
        /// CHILD only — the parent's own fd table is untouched here.
        pub fn install(&self, cmd: &mut Command) {
            let write_fd = self.write_fd;
            let read_fd = self.read_fd.as_raw_fd();
            // SAFETY: runs in the child after fork(), before exec, single
            // threaded — only async-signal-safe calls (dup2/close), exactly
            // what `pre_exec`'s contract requires.
            unsafe {
                cmd.pre_exec(move || {
                    if libc::dup2(write_fd, 3) < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    libc::close(write_fd);
                    libc::close(read_fd);
                    Ok(())
                });
            }
        }

        /// Consumes the pipe: closes the PARENT's own copy of the write end
        /// (never held open here past this point — else `read()` below would
        /// wait for a closure that this very process is preventing) and
        /// returns the read end as a boxed reader for the generic stream loop.
        pub fn into_reader(self) -> Box<dyn Read + Send + 'static> {
            // SAFETY: `self.write_fd` is this process's own valid fd, not
            // used anywhere else after this point.
            unsafe { libc::close(self.write_fd) };
            Box::new(std::fs::File::from(self.read_fd))
        }
    }
}

#[cfg(not(unix))]
mod secret_pipe {
    use std::io::Read;
    use std::process::Command;

    use crate::ports::EngineError;

    /// Not implemented outside Unix — Windows is out of scope for spec 028
    /// (research.md "Decisión: Windows"). `new()` fails closed so a build on
    /// another target can never silently skip ticket delivery.
    pub struct SecretPipe;

    impl SecretPipe {
        pub fn new() -> Result<Self, EngineError> {
            Err(EngineError::Io(
                "secret-fd delivery is only implemented on Unix targets".to_string(),
            ))
        }

        pub fn install(&self, _cmd: &mut Command) {}

        pub fn into_reader(self) -> Box<dyn Read + Send + 'static> {
            Box::new(std::io::empty())
        }
    }
}

use secret_pipe::SecretPipe;
