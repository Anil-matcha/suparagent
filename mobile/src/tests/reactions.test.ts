import {
  formatReactionReply,
  isLatestAssistantMessage,
  loadThreadReactions,
  saveThreadReaction,
} from '@/lib/reactions';

describe('reactions', () => {
  it('formats affirmative and corrective reaction replies', () => {
    expect(formatReactionReply('thumbs_up')).toBe('👍 Looks good, please proceed.');
    expect(formatReactionReply('thumbs_down')).toBe('👎 No, please stop or revise this approach.');
  });

  it('saves and clears reaction state per thread', async () => {
    let map = saveThreadReaction(1, 'item-1', 'thumbs_up');
    expect(map['item-1']).toBe('thumbs_up');

    // Toggling the same reaction clears it
    map = saveThreadReaction(1, 'item-1', 'thumbs_up');
    expect(map['item-1']).toBeUndefined();

    // Setting thumbs_down
    map = saveThreadReaction(1, 'item-1', 'thumbs_down');
    expect(map['item-1']).toBe('thumbs_down');

    // Loading thread reactions retrieves saved state
    const loaded = await loadThreadReactions(1);
    expect(loaded['item-1']).toBe('thumbs_down');
  });

  it('determines if item is latest assistant message', () => {
    const items = [
      { kind: 'user', key: 'u1' },
      { kind: 'assistant', key: 'a1' },
      { kind: 'user', key: 'u2' },
      { kind: 'assistant', key: 'a2' },
    ];
    expect(isLatestAssistantMessage('a2', items)).toBe(true);
    expect(isLatestAssistantMessage('a1', items)).toBe(false);
    expect(isLatestAssistantMessage('u2', items)).toBe(false);
  });
});
