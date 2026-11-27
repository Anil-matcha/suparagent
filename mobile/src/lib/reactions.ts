import * as SecureStore from 'expo-secure-store';

export type ReactionKind = 'thumbs_up' | 'thumbs_down';

export type ReactionsMap = Record<string, ReactionKind>;

const cache: Record<string, ReactionsMap> = {};

function storageKey(threadId: number | null | undefined): string {
  return `suparagent_reactions_${threadId ?? 'global'}`;
}

/**
 * Format the short turn prompt sent to the agent when a thumbs reaction acts as a reply.
 */
export function formatReactionReply(reaction: ReactionKind): string {
  if (reaction === 'thumbs_up') {
    return '👍 Looks good, please proceed.';
  }
  return '👎 No, please stop or revise this approach.';
}

/**
 * Check whether an item is the latest completed assistant message in the conversation transcript.
 */
export function isLatestAssistantMessage(itemKey: string, items: readonly { kind: string; key: string }[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'assistant') {
      return items[i].key === itemKey;
    }
  }
  return false;
}

export function getCachedThreadReactions(threadId: number | null | undefined): ReactionsMap {
  const key = storageKey(threadId);
  return cache[key] ? { ...cache[key] } : {};
}

export async function loadThreadReactions(threadId: number | null | undefined): Promise<ReactionsMap> {
  const key = storageKey(threadId);
  try {
    const raw = await SecureStore.getItemAsync(key);
    if (!raw) return cache[key] ? { ...cache[key] } : {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const result: ReactionsMap = {};
      for (const [k, val] of Object.entries(parsed)) {
        if (val === 'thumbs_up' || val === 'thumbs_down') {
          result[k] = val;
        }
      }
      cache[key] = result;
      return result;
    }
  } catch {
    // Fall back to memory cache
  }
  return cache[key] ? { ...cache[key] } : {};
}

export function saveThreadReaction(
  threadId: number | null | undefined,
  messageKey: string,
  reaction: ReactionKind | null,
): ReactionsMap {
  const key = storageKey(threadId);
  const current = cache[key] || {};
  const next: ReactionsMap = { ...current };

  if (reaction === null || next[messageKey] === reaction) {
    delete next[messageKey];
  } else {
    next[messageKey] = reaction;
  }

  cache[key] = next;

  try {
    if (Object.keys(next).length === 0) {
      void SecureStore.deleteItemAsync(key).catch(() => {});
    } else {
      void SecureStore.setItemAsync(key, JSON.stringify(next)).catch(() => {});
    }
  } catch {
    // ignore
  }

  return next;
}
