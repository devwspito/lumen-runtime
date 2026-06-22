// Domain types matching the shapes returned by /api/v1/* endpoints.
// Source of truth: src/hermes/shell_server/cowork/agents_api.py (AgentDraft)
// and the vanilla js/api.js call shapes.

export interface Agent {
  id: string
  name: string
  role: string
  primary_mission: string
  instructions: string
  language: string
  color: string
  golden_rules: string[]
  autonomy_level: string
  is_default: boolean
}

export interface ActiveAgentResponse {
  active_agent_id: string
}

export interface RuntimeStatus {
  state: string
  active_task_count: number
}
