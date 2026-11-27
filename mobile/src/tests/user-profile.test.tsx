import { act, renderHook } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { UserProfileProvider, useUserProfile, BUBBLE_TINTS } from '@/lib/user-profile';

const wrapper = ({ children }: { children: ReactNode }) => <UserProfileProvider>{children}</UserProfileProvider>;

describe('UserProfile', () => {
  it('provides default profile values when unconfigured', async () => {
    const { result } = await renderHook(() => useUserProfile(), { wrapper });
    expect(result.current.displayName).toBeNull();
    expect(result.current.bubbleTint).toBe('default');
  });

  it('updates display name and trims whitespace', async () => {
    const { result } = await renderHook(() => useUserProfile(), { wrapper });
    await act(async () => {
      await result.current.setDisplayName('  Dev Alex  ');
    });
    expect(result.current.displayName).toBe('Dev Alex');

    await act(async () => {
      await result.current.setDisplayName('   ');
    });
    expect(result.current.displayName).toBeNull();
  });

  it('updates bubble tint to ocean blue and calculates correct colors', async () => {
    const { result } = await renderHook(() => useUserProfile(), { wrapper });
    await act(async () => {
      await result.current.setBubbleTint('blue');
    });
    expect(result.current.bubbleTint).toBe('blue');

    const blueConfig = BUBBLE_TINTS.find((t) => t.id === 'blue')!;
    expect(result.current.getUserBubbleColor('light')).toBe(blueConfig.lightBg);
    expect(result.current.getUserBubbleColor('dark')).toBe(blueConfig.darkBg);
  });

  it('falls back gracefully when used outside provider', async () => {
    const { result } = await renderHook(() => useUserProfile());
    expect(result.current.displayName).toBeNull();
    expect(result.current.bubbleTint).toBe('default');
    expect(typeof result.current.getUserBubbleColor('light')).toBe('string');
  });
});