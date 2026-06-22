/**
 * Markdown → sanitised HTML for chat messages.
 *
 * Uses `marked` (GFM: tables, strikethrough, task lists, fenced code)
 * and DOMPurify for sanitisation — mirrors the vanilla markdown.js exactly.
 * External links get target="_blank" rel="noopener noreferrer".
 */

import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  gfm: true,
  breaks: true,
})

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    const href = node.getAttribute('href') ?? ''
    const external = /^(https?:)?\/\//i.test(href) || /^https?:/i.test(href)
    if (external) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  }
  if (node.tagName === 'INPUT') {
    node.setAttribute('disabled', '')
  }
})

// Cast required: the @types/dompurify bundled PARSER_MEDIA_TYPE constraint is
// stricter than the library's runtime type; cast avoids a false type error.
const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr', 'blockquote', 'pre', 'code', 'span',
    'strong', 'em', 'del', 's', 'b', 'i', 'a',
    'ul', 'ol', 'li', 'input',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
  ],
  ALLOWED_ATTR: ['href', 'title', 'class', 'target', 'rel', 'type', 'checked', 'disabled', 'align'],
  ALLOW_DATA_ATTR: false,
} satisfies DOMPurify.Config

export function renderMarkdown(md: string): string {
  const raw = marked.parse(md ?? '', { async: false }) as string
  return DOMPurify.sanitize(raw, PURIFY_CONFIG) as string
}
