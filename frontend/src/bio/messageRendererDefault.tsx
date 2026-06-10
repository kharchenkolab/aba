/**
 * Default bio chat-message renderer — registers ./Message with the
 * platform's neutral message-renderer slot (lib/messageRenderer).
 * ChatPane reads the slot via `message_renderer()` and never
 * imports Message directly.
 */
import { register_message_renderer } from '../lib/messageRenderer'
import Message from './Message'

register_message_renderer(Message)
