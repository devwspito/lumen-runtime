/**
 * Human-readable labels for agent tool names.
 * Used in ChatView (live activity, tool steps) and anywhere tool names are displayed.
 *
 * Rule: no internal tool names exposed to the user. Fallback truncates to 28 chars.
 */

const TOOL_LABEL_MAP: Record<string, string> = {
  browser_navigate:      'Navegando por la web',
  browser_click:         'Haciendo clic en la web',
  browser_type:          'Escribiendo en la web',
  browser_scroll:        'Desplazándose por la web',
  browser_screenshot:    'Capturando la pantalla',
  web_search:            'Buscando en la web',
  write_file:            'Guardando archivo',
  read_file:             'Leyendo archivo',
  execute_code:          'Ejecutando comando',
  run_command:           'Ejecutando comando',
  send_message:          'Enviando mensaje',
  delegate_task:         'Delegando tarea',
  mixture_of_agents:     'Colaboración entre agentes',
  skill_manage:          'Instalando capacidad',
  cronjob:               'Programando tarea',
  set_policy:            'Cambiando permisos del agente',
  disable_mfa:           'Cambiando permisos del agente',
  list_files:            'Leyendo carpeta',
  move_file:             'Moviendo archivo',
  delete_file:           'Eliminando archivo',
  download_file:         'Descargando archivo',
}

/** Tools that are internal state markers — hide them from the UI. */
const HIDDEN_TOOLS = new Set([
  'chat_responding',
  'chat_start',
  'chat_end',
  'chat_status',
])

const MAX_LABEL_CHARS = 28

export function toolLabel(toolName: string): string | null {
  if (HIDDEN_TOOLS.has(toolName)) return null

  // Exact match
  if (TOOL_LABEL_MAP[toolName]) return TOOL_LABEL_MAP[toolName]!

  // Prefix match (e.g. install_skill_xyz → "Instalando")
  if (toolName.startsWith('install_')) return 'Instalando'
  if (toolName.startsWith('browser_')) return 'Navegando por la web'

  // Humanized fallback: replace underscores, capitalize, truncate
  const humanized = toolName.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())
  return humanized.length > MAX_LABEL_CHARS ? humanized.slice(0, MAX_LABEL_CHARS - 1) + '…' : humanized
}
