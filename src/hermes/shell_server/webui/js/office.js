/**
 * office.js — "Office" view: agent command center hub.
 *
 * Three departments:
 *   Cerebro      — the default agent (is_default:true), shown prominently.
 *   Mis agentes  — custom (non-default) agents + "Crear agente" card.
 *   Swarm ruflo  — shown only when a ruflo MCP server is connected.
 *
 * Polling: listAgents + getActiveAgent + getRuntimeStatus every 3s.
 * No new dependencies — vanilla JS, ES modules, Sereno tokens only.
 *
 * Endpoints: GET /agents, /agents/active, /runtime/status, /mcp;
 *            POST /agents/{id}/activate, /agents.
 */

import {
  listAgents, getActiveAgent, getRuntimeStatus,
  setActiveAgent, createAgent, listMcpServers,
} from './api.js';
import { switchView, showToast } from './shell.js';
import { startNewConversation } from './chat.js';
import { t } from './i18n.js';

// ── Helpers ────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

const _PALETTE = [
  '#6366f1', '#0A84FF', '#34D399', '#F5B945',
  '#FF6B6B', '#5BC8E0', '#C084FC', '#FB923C',
];

function _colorFor(agent, idx) {
  if (agent.color && /^#?[0-9a-fA-F]{3,8}$/.test(agent.color)) {
    return agent.color.startsWith('#') ? agent.color : `#${agent.color}`;
  }
  // Derive a stable hue from the agent id so the colour never jumps between renders.
  const id = agent.agent_id ?? agent.id ?? '';
  if (id) {
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) & 0xffff;
    return _PALETTE[h % _PALETTE.length];
  }
  return _PALETTE[idx % _PALETTE.length];
}

function _agentId(agent) {
  return agent.agent_id ?? agent.id ?? '';
}

// ── Polling ────────────────────────────────────────────────────────────────────

let _pollTimer = null;

function _stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

// ── Autonomy badge label map (mirrors agents.js AUTONOMY list) ─────────────────

function _autonomyLabel(level) {
  const map = {
    ask_always:  () => t('agents.autonomyAsk'),
    balanced:    () => t('agents.autonomyBalanced'),
    autonomous:  () => t('agents.autonomyAutonomous'),
  };
  return (map[level] ?? (() => level))();
}

// ── Live state ─────────────────────────────────────────────────────────────────

/**
 * Apply active + working state to every rendered agent card.
 *
 * Cards expose data-agent-id. The active agent is determined by activeId
 * (from /agents/active). Working state is driven by runtime.active_task_count > 0
 * (real daemon count, never faked). Only the active agent gets the working
 * animation — non-active agents are never animated regardless of count.
 *
 * Animation classes live in style.css under @media (prefers-reduced-motion: no-preference)
 * so the keyframes are never applied when the user prefers reduced motion.
 */
function _applyLiveState(activeId, runtime) {
  const working = runtime != null && (runtime.active_task_count || 0) > 0;

  document.querySelectorAll('.oc-agent-card').forEach((card) => {
    const isActive = card.dataset.agentId === activeId;
    const dot    = card.querySelector('.oc-agent-card__dot');
    const avatar = card.querySelector('.oc-agent-card__avatar');
    const status = card.querySelector('.oc-agent-card__status-text');
    const color  = avatar ? avatar.dataset.color || 'var(--accent)' : 'var(--accent)';

    if (isActive && working) {
      if (dot) {
        dot.style.background = 'var(--ok)';
        dot.title = t('office.working');
        dot.classList.add('oc-agent-card__dot--working');
      }
      if (avatar) {
        avatar.style.setProperty('--oc-pulse-color', color);
        avatar.style.boxShadow = `0 0 0 4px ${color}33`;
        avatar.style.animation = 'oc-pulse 1.4s ease-in-out infinite';
      }
      if (status) { status.textContent = t('office.working'); status.style.color = 'var(--ok)'; }
      card.style.borderColor = color;
    } else if (isActive) {
      if (dot) {
        dot.style.background = 'var(--accent)';
        dot.title = t('office.activeIdle');
        dot.classList.remove('oc-agent-card__dot--working');
      }
      if (avatar) { avatar.style.boxShadow = `0 0 0 3px ${color}22`; avatar.style.animation = ''; }
      if (status) { status.textContent = t('office.activeIdle'); status.style.color = 'var(--ink3)'; }
      card.style.borderColor = color;
    } else {
      if (dot) {
        dot.style.background = 'var(--ink4)';
        dot.title = '';
        dot.classList.remove('oc-agent-card__dot--working');
      }
      if (avatar) { avatar.style.boxShadow = 'none'; avatar.style.animation = ''; }
      if (status) { status.textContent = ''; }
      card.style.borderColor = 'var(--line)';
    }
  });
}

// ── Agent card ─────────────────────────────────────────────────────────────────

/**
 * Build a single agent card (button, keyboard-accessible).
 * @param {object} agent
 * @param {number} idx   — palette index for fallback colour
 */
function _buildAgentCard(agent, idx) {
  const id      = _agentId(agent);
  const name    = agent.name || id;
  const role    = agent.role || agent.primary_mission || '';
  const color   = _colorFor(agent, idx);
  const initial = (name || 'A')[0].toUpperCase();
  const isBrain = agent.is_default === true;
  const auto    = agent.autonomy_level ? _autonomyLabel(agent.autonomy_level) : '';

  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'oc-agent-card';
  card.dataset.agentId = id;
  card.setAttribute('aria-label', `${name}${role ? ' — ' + role : ''}`);

  const cerebroChip = isBrain
    ? `<span class="oc-badge oc-badge--cerebro">${esc(t('office.orchestrator'))}</span>`
    : '';
  const autoBadge = auto
    ? `<span class="oc-badge oc-badge--auto">${esc(auto)}</span>`
    : '';

  card.innerHTML = `
    <span class="oc-agent-card__dot" aria-hidden="true"></span>
    <span class="oc-agent-card__avatar" aria-hidden="true"
          data-color="${esc(color)}"
          style="background:${esc(color)}">${esc(initial)}</span>
    <span class="oc-agent-card__name">${esc(name)}</span>
    <span class="oc-agent-card__role">${esc(role)}</span>
    <span class="oc-agent-card__badges">${cerebroChip}${autoBadge}</span>
    <span class="oc-agent-card__status-text" aria-live="polite"></span>`;

  card.addEventListener('mouseenter', () => {
    card.style.transform = 'translateY(-2px)';
    card.style.boxShadow = `0 6px 20px ${color}28`;
  });
  card.addEventListener('mouseleave', () => {
    card.style.transform = '';
    card.style.boxShadow = '';
  });
  card.addEventListener('click', () => _openAgentDrawer(agent, color));
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openAgentDrawer(agent, color); }
  });

  return card;
}

// ── "Create agent" card ────────────────────────────────────────────────────────

function _buildCreateCard(onCreated) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'oc-create-card';
  card.setAttribute('aria-label', t('office.createAgent'));
  card.innerHTML = `
    <span class="oc-create-card__icon" aria-hidden="true">+</span>
    <span class="oc-create-card__label">${esc(t('office.createAgent'))}</span>`;
  card.addEventListener('click', () => _openCreateModal(onCreated));
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openCreateModal(onCreated); }
  });
  return card;
}

// ── Swarm card ─────────────────────────────────────────────────────────────────

function _buildSwarmCard(rufloServer) {
  const toolCount = rufloServer.tool_count ?? 0;
  const isHealthy = rufloServer.health === 'ok' || rufloServer.health === 'healthy';

  const card = document.createElement('div');
  card.className = 'oc-swarm-card';
  card.setAttribute('role', 'status');
  card.setAttribute('aria-label', t('office.deptSwarm'));
  card.innerHTML = `
    <span class="oc-swarm-card__icon" aria-hidden="true">⚡</span>
    <span class="oc-swarm-card__name">${esc(t('office.deptSwarm'))}</span>
    <span class="oc-swarm-card__meta">
      <span class="oc-badge ${isHealthy ? 'oc-badge--ok' : 'oc-badge--warn'}">
        ${isHealthy ? esc(t('office.swarmOnline')) : esc(t('office.swarmOffline'))}
      </span>
      ${toolCount > 0 ? `<span class="oc-badge oc-badge--tools">${toolCount} ${esc(t('office.swarmTools'))}</span>` : ''}
    </span>
    <p class="oc-swarm-card__hint">${esc(t('office.swarmHint'))}</p>`;
  return card;
}

// ── Agent drawer ───────────────────────────────────────────────────────────────

function _openAgentDrawer(agent, color) {
  document.getElementById('oc-drawer')?.remove();

  const id   = _agentId(agent);
  const name = agent.name || id;
  const role = agent.role || agent.primary_mission || '';

  const backdrop = document.createElement('div');
  backdrop.id = 'oc-drawer';
  backdrop.className = 'oc-drawer-backdrop';
  backdrop.setAttribute('aria-modal', 'true');
  backdrop.setAttribute('role', 'dialog');
  backdrop.setAttribute('aria-label', name);

  const panel = document.createElement('div');
  panel.className = 'oc-drawer-panel';
  panel.setAttribute('tabindex', '-1');

  const initial  = (name || 'A')[0].toUpperCase();
  const isBrain  = agent.is_default === true;
  const auto     = agent.autonomy_level ? _autonomyLabel(agent.autonomy_level) : '';
  const cerebroChip = isBrain
    ? `<span class="oc-badge oc-badge--cerebro">${esc(t('office.orchestrator'))}</span>`
    : '';

  panel.innerHTML = `
    <div class="oc-drawer-panel__head">
      <span class="oc-drawer-panel__avatar" aria-hidden="true"
            style="background:${esc(color)}">${esc(initial)}</span>
      <div class="oc-drawer-panel__head-info">
        <div class="oc-drawer-panel__name">${esc(name)}${cerebroChip}</div>
        <div class="oc-drawer-panel__role">${esc(role)}</div>
      </div>
      <button class="oc-drawer-panel__close btn btn--ghost btn--sm"
              aria-label="${esc(t('common.close'))}">✕</button>
    </div>
    ${agent.primary_mission ? `
    <div class="oc-drawer-panel__section">
      <span class="oc-drawer-panel__label">${esc(t('office.mission'))}</span>
      <p class="oc-drawer-panel__body">${esc(agent.primary_mission)}</p>
    </div>` : ''}
    ${auto ? `
    <div class="oc-drawer-panel__section">
      <span class="oc-drawer-panel__label">${esc(t('office.autonomy'))}</span>
      <p class="oc-drawer-panel__body">${esc(auto)}</p>
    </div>` : ''}
    <div class="oc-drawer-panel__actions">
      <button class="btn btn--primary" data-act="chat">${esc(t('office.chatBtn'))}</button>
      <button class="btn btn--ghost" data-act="task">${esc(t('office.taskBtn'))}</button>
      <button class="btn btn--ghost" data-act="manage">${esc(t('office.manage'))}</button>
    </div>`;

  backdrop.appendChild(panel);

  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };

  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  panel.querySelector('.oc-drawer-panel__close').addEventListener('click', close);
  panel.querySelector('[data-act="manage"]').addEventListener('click', () => { close(); switchView('agents'); });
  panel.querySelector('[data-act="task"]').addEventListener('click', () => { close(); switchView('tasks'); });
  panel.querySelector('[data-act="chat"]').addEventListener('click', async () => {
    try {
      if (!agent.is_default) await setActiveAgent(id);
      close();
      switchView('chat');
      startNewConversation();
      if (!agent.is_default) showToast(t('office.nowActive').replace('{name}', name), 'ok');
    } catch (e) {
      showToast(`${t('office.activateFail')}: ${e.message || e}`, 'error');
    }
  });

  document.addEventListener('keydown', onKey);
  document.body.appendChild(backdrop);
  // Move focus into the panel so screen-reader users hear the dialog immediately.
  requestAnimationFrame(() => panel.focus());
}

// ── Create-agent modal ─────────────────────────────────────────────────────────

function _openCreateModal(onCreated) {
  document.getElementById('oc-create-modal')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'oc-create-modal';
  overlay.className = 'modal-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'oc-create-title');

  // Field IDs must be unique even if the modal is opened multiple times.
  const uid = Date.now();
  const fName  = `oc-name-${uid}`;
  const fRole  = `oc-role-${uid}`;
  const fMiss  = `oc-miss-${uid}`;
  const fInstr = `oc-instr-${uid}`;
  const fAuto  = `oc-auto-${uid}`;

  overlay.innerHTML = `
    <div class="modal-card" role="document">
      <div class="modal-card__head">
        <h3 class="modal-card__title" id="oc-create-title">${esc(t('agents.newFormTitle'))}</h3>
        <button class="btn btn--ghost btn--sm" data-act="close"
                aria-label="${esc(t('common.close'))}">✕</button>
      </div>
      <div class="modal-card__body oc-create-modal__body">
        <label class="cv-label" for="${fName}">${esc(t('agents.nameLabel'))} *</label>
        <input id="${fName}" class="cv-input" type="text"
               placeholder="${esc(t('agents.namePlaceholder'))}" autocomplete="off">
        <label class="cv-label" for="${fRole}">${esc(t('agents.roleLabel'))}</label>
        <input id="${fRole}" class="cv-input" type="text"
               placeholder="${esc(t('agents.rolePlaceholder'))}">
        <label class="cv-label" for="${fMiss}">${esc(t('agents.missionLabel'))}</label>
        <input id="${fMiss}" class="cv-input" type="text"
               placeholder="${esc(t('agents.missionPlaceholder'))}">
        <label class="cv-label" for="${fInstr}">${esc(t('agents.instructionsLabel'))}</label>
        <textarea id="${fInstr}" class="cv-textarea" rows="3"
                  placeholder="${esc(t('agents.instructionsPlaceholder'))}"></textarea>
        <label class="cv-label" for="${fAuto}">${esc(t('agents.autonomyLabel'))}</label>
        <select id="${fAuto}" class="cv-input">
          <option value="ask_always">${esc(t('agents.autonomyAsk'))}</option>
          <option value="balanced" selected>${esc(t('agents.autonomyBalanced'))}</option>
          <option value="autonomous">${esc(t('agents.autonomyAutonomous'))}</option>
        </select>
        <p class="oc-create-modal__error" id="oc-name-error" role="alert" hidden></p>
      </div>
      <div class="modal-card__actions">
        <button class="btn btn--ghost btn--sm" data-act="close">${esc(t('common.cancel'))}</button>
        <button class="btn btn--primary btn--sm" data-act="submit">${esc(t('agents.createBtn'))}</button>
      </div>
    </div>`;

  const close = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };

  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelectorAll('[data-act="close"]').forEach(b => b.addEventListener('click', close));

  const submitBtn = overlay.querySelector('[data-act="submit"]');
  const errorEl   = overlay.querySelector('#oc-name-error');
  const nameInput = overlay.querySelector(`#${fName}`);

  const submit = async () => {
    const name = nameInput.value.trim();
    if (!name) {
      errorEl.textContent = t('agents.nameRequired');
      errorEl.hidden = false;
      nameInput.setAttribute('aria-describedby', 'oc-name-error');
      nameInput.focus();
      return;
    }
    errorEl.hidden = true;
    submitBtn.disabled = true;
    submitBtn.textContent = t('common.loading');
    try {
      await createAgent({
        name,
        role:           overlay.querySelector(`#${fRole}`).value.trim(),
        primary_mission: overlay.querySelector(`#${fMiss}`).value.trim(),
        instructions:   overlay.querySelector(`#${fInstr}`).value.trim(),
        autonomy_level: overlay.querySelector(`#${fAuto}`).value,
      });
      showToast(t('agents.created'), 'ok');
      close();
      onCreated();
    } catch (e) {
      showToast(e.message || t('error.generic'), 'error');
      submitBtn.disabled = false;
      submitBtn.textContent = t('agents.createBtn');
    }
  };

  submitBtn.addEventListener('click', submit);
  overlay.querySelector(`#${fInstr}`).addEventListener('keydown', (e) => {
    // Textarea: Ctrl+Enter submits to avoid trapping users.
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit();
  });

  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  requestAnimationFrame(() => nameInput.focus());
}

// ── Section builder ────────────────────────────────────────────────────────────

function _buildSection(labelKey, grid) {
  const section = document.createElement('div');
  section.className = 'oc-section cv-section';

  const label = document.createElement('div');
  label.className = 'cv-section-label';
  label.textContent = t(labelKey);
  section.appendChild(label);
  section.appendChild(grid);
  return section;
}

// ── Main export ────────────────────────────────────────────────────────────────

export async function renderOfficeView(container) {
  _stopPolling();

  container.innerHTML = `
    <div class="capability-view oc-view">
      <div class="cv-header oc-view__header">
        <div class="oc-view__header-text">
          <h2 class="cv-title">${esc(t('office.title'))}</h2>
          <p class="cv-subtitle">${esc(t('office.subtitle'))}</p>
        </div>
        <button id="oc-new-chat-btn" class="btn btn--primary" aria-label="${esc(t('office.newChat'))}">
          ${esc(t('office.newChat'))}
        </button>
      </div>
      <div id="oc-body"></div>
    </div>`;

  // "Nuevo chat" header button — always available.
  container.querySelector('#oc-new-chat-btn').addEventListener('click', () => {
    switchView('chat');
    startNewConversation();
  });

  const body = container.querySelector('#oc-body');

  // Skeleton while loading.
  body.innerHTML = `
    <div class="cv-section">
      <div class="cv-section-label">&nbsp;</div>
      <div class="oc-agent-grid">
        <div class="cv-skeleton oc-skeleton-card"></div>
        <div class="cv-skeleton oc-skeleton-card"></div>
        <div class="cv-skeleton oc-skeleton-card"></div>
      </div>
    </div>`;

  // Initial data load.
  let agents = [];
  let rufloServer = null;

  try {
    const [agentsRes, mcpRes] = await Promise.all([
      listAgents(),
      listMcpServers().catch(() => []),
    ]);
    agents = Array.isArray(agentsRes) ? agentsRes : (agentsRes.agents || []);
    const mcpList = Array.isArray(mcpRes) ? mcpRes : (mcpRes.servers || []);
    rufloServer = mcpList.find(s => s.slug === 'ruflo') ?? null;
  } catch (e) {
    body.innerHTML = `<div class="cv-empty">${esc(t('office.loadFail'))}: ${esc(e.message || e)}</div>`;
    return;
  }

  function renderBody() {
    body.innerHTML = '';

    // ── Dept 1: Cerebro ───────────────────────────────────────────────────────
    const cerebro = agents.find(a => a.is_default === true);
    const cerebroGrid = document.createElement('div');
    cerebroGrid.className = 'oc-agent-grid oc-agent-grid--cerebro';

    if (cerebro) {
      cerebroGrid.appendChild(_buildAgentCard(cerebro, 0));
    } else {
      const empty = document.createElement('p');
      empty.className = 'cv-empty';
      empty.textContent = t('office.empty');
      cerebroGrid.appendChild(empty);
    }
    body.appendChild(_buildSection('office.deptCerebro', cerebroGrid));

    // ── Dept 2: Custom agents ─────────────────────────────────────────────────
    const customAgents = agents.filter(a => a.is_default !== true);
    const customGrid = document.createElement('div');
    customGrid.className = 'oc-agent-grid';

    customAgents.forEach((a, i) => customGrid.appendChild(_buildAgentCard(a, i + 1)));
    customGrid.appendChild(_buildCreateCard(() => {
      // After creation, re-fetch and re-render.
      listAgents()
        .then(res => { agents = Array.isArray(res) ? res : (res.agents || []); renderBody(); })
        .catch(() => {});
    }));
    body.appendChild(_buildSection('office.deptCustom', customGrid));

    // ── Dept 3: Swarm (only when ruflo is connected) ──────────────────────────
    if (rufloServer) {
      const swarmGrid = document.createElement('div');
      swarmGrid.className = 'oc-swarm-grid';
      swarmGrid.appendChild(_buildSwarmCard(rufloServer));
      body.appendChild(_buildSection('office.deptSwarm', swarmGrid));
    }
  }

  renderBody();

  // ── Live polling ──────────────────────────────────────────────────────────────
  async function refresh() {
    // Stop if the view was unmounted.
    if (!container.querySelector('#oc-body')) { _stopPolling(); return; }

    const [act, rt] = await Promise.all([
      getActiveAgent().catch(() => ({ active_agent_id: '' })),
      getRuntimeStatus().catch(() => ({ state: 'unknown', active_task_count: 0 })),
    ]);
    const activeId = act.active_agent_id || act.agent_id || '';
    _applyLiveState(activeId, rt);
  }

  await refresh();
  _pollTimer = setInterval(refresh, 3000);
}
