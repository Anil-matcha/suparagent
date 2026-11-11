import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useColorScheme } from 'react-native';
import * as SecureStore from 'expo-secure-store';

import { useAuth } from './auth-context';
import { createApi, type UserProfileData } from './api';
import { paletteFor } from './theme';

export type BubbleTint = 'default' | 'blue' | 'emerald' | 'purple' | 'amber';

export type UserProfile = {
  displayName: string | null;
  bubbleTint: BubbleTint;
};

export type TintConfig = {
  id: BubbleTint;
  label: string;
  lightBg: string;
  darkBg: string;
  preview: string;
};

export const BUBBLE_TINTS: TintConfig[] = [
  { id: 'default', label: 'Classic', lightBg: '#ebe7dd', darkBg: '#2a2a30', preview: '#ebe7dd' },
  { id: 'blue', label: 'Ocean', lightBg: '#dbeafe', darkBg: '#1e293b', preview: '#3b82f6' },
  { id: 'emerald', label: 'Emerald', lightBg: '#d1fae5', darkBg: '#064e3b', preview: '#10b981' },
  { id: 'purple', label: 'Violet', lightBg: '#ede9fe', darkBg: '#2e1065', preview: '#8b5cf6' },
  { id: 'amber', label: 'Warm Sand', lightBg: '#fef3c7', darkBg: '#382305', preview: '#f59e0b' },
];

export const DEFAULT_PROFILE: UserProfile = {
  displayName: null,
  bubbleTint: 'default',
};

const STORAGE_KEY = 'open_duet.user_profile';
const OPTIONS: SecureStore.SecureStoreOptions = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

async function loadStoredProfile(): Promise<UserProfile> {
  try {
    const raw = await SecureStore.getItemAsync(STORAGE_KEY, OPTIONS);
    if (!raw) return DEFAULT_PROFILE;
    const parsed = JSON.parse(raw) as Partial<UserProfile> | null;
    if (!parsed || typeof parsed !== 'object') return DEFAULT_PROFILE;
    const tint = BUBBLE_TINTS.some((t) => t.id === parsed.bubbleTint) ? (parsed.bubbleTint as BubbleTint) : 'default';
    const name = typeof parsed.displayName === 'string' && parsed.displayName.trim() ? parsed.displayName.trim() : null;
    return { displayName: name, bubbleTint: tint };
  } catch {
    return DEFAULT_PROFILE;
  }
}

async function saveStoredProfile(profile: UserProfile): Promise<void> {
  try {
    await SecureStore.setItemAsync(STORAGE_KEY, JSON.stringify(profile), OPTIONS);
  } catch {
    // Ignore storage errors in restricted environments
  }
}

export type UserProfileContextValue = {
  profile: UserProfile;
  serverProfile: UserProfileData | null;
  displayName: string | null;
  bubbleTint: BubbleTint;
  email: string | null;
  avatarUrl: string | null;
  isAdmin: boolean;
  setDisplayName: (name: string | null) => Promise<void>;
  setBubbleTint: (tint: BubbleTint) => Promise<void>;
  refreshProfile: () => Promise<void>;
  getUserBubbleColor: (colorScheme?: 'light' | 'dark' | null) => string;
};

const UserProfileContext = createContext<UserProfileContextValue | null>(null);

function useSafeAuth() {
  try {
    return useAuth();
  } catch {
    return null;
  }
}

export function UserProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<UserProfile>(DEFAULT_PROFILE);
  const [serverProfile, setServerProfile] = useState<UserProfileData | null>(null);
  const scheme = useColorScheme();
  const auth = useSafeAuth();

  const server = auth?.state?.status === 'paired' ? auth.state.server : null;
  const token = auth?.state?.status === 'paired' ? auth.state.token : null;

  const api = useMemo(() => (server && token ? createApi({ server, token }) : null), [server, token]);

  const fetchServerProfile = useCallback(async () => {
    if (!api || typeof api.getProfile !== 'function') return;
    try {
      const data = await api.getProfile();
      setServerProfile(data);
    } catch {
      // Ignore network errors or unauthenticated states gracefully
    }
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    loadStoredProfile().then((loaded) => {
      if (!cancelled) setProfile(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!api || typeof api.getProfile !== 'function') return;
    let cancelled = false;
    api
      .getProfile()
      .then((data) => {
        if (!cancelled) setServerProfile(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [api]);

  const setDisplayName = useCallback(
    async (name: string | null) => {
      const trimmed = name?.trim() || null;
      setProfile((prev) => {
        const next: UserProfile = { ...prev, displayName: trimmed };
        void saveStoredProfile(next);
        return next;
      });
      if (api && typeof api.updateProfile === 'function') {
        try {
          const updated = await api.updateProfile({ name: trimmed });
          setServerProfile(updated);
        } catch {
          // Local override is already persisted
        }
      }
    },
    [api],
  );

  const setBubbleTint = useCallback(async (tint: BubbleTint) => {
    setProfile((prev) => {
      const next: UserProfile = { ...prev, bubbleTint: tint };
      void saveStoredProfile(next);
      return next;
    });
  }, []);

  const getUserBubbleColor = useCallback(
    (colorScheme?: 'light' | 'dark' | null): string => {
      const currentScheme = colorScheme ?? scheme;
      if (profile.bubbleTint === 'default') {
        return paletteFor(currentScheme).bubbleUser;
      }
      const match = BUBBLE_TINTS.find((t) => t.id === profile.bubbleTint);
      if (!match) return paletteFor(currentScheme).bubbleUser;
      return currentScheme === 'dark' ? match.darkBg : match.lightBg;
    },
    [profile.bubbleTint, scheme],
  );

  const effectiveDisplayName = useMemo(() => {
    if (profile.displayName) return profile.displayName;
    if (serverProfile?.name) return serverProfile.name;
    if (serverProfile?.email) {
      const idx = serverProfile.email.indexOf('@');
      return idx > 0 ? serverProfile.email.slice(0, idx) : serverProfile.email;
    }
    return null;
  }, [profile.displayName, serverProfile]);

  const value = useMemo(
    () => ({
      profile,
      serverProfile,
      displayName: effectiveDisplayName,
      bubbleTint: profile.bubbleTint,
      email: serverProfile?.email ?? null,
      avatarUrl: serverProfile?.avatar_url ?? null,
      isAdmin: serverProfile?.is_admin ?? false,
      setDisplayName,
      setBubbleTint,
      refreshProfile: fetchServerProfile,
      getUserBubbleColor,
    }),
    [profile, serverProfile, effectiveDisplayName, setDisplayName, setBubbleTint, fetchServerProfile, getUserBubbleColor],
  );

  return <UserProfileContext.Provider value={value}>{children}</UserProfileContext.Provider>;
}

export function useUserProfile(): UserProfileContextValue {
  const ctx = useContext(UserProfileContext);
  if (!ctx) {
    // Fallback if rendered outside provider (e.g. in some isolated component unit tests)
    return {
      profile: DEFAULT_PROFILE,
      serverProfile: null,
      displayName: null,
      bubbleTint: 'default',
      email: null,
      avatarUrl: null,
      isAdmin: false,
      setDisplayName: async () => {},
      setBubbleTint: async () => {},
      refreshProfile: async () => {},
      getUserBubbleColor: (scheme) => paletteFor(scheme).bubbleUser,
    };
  }
  return ctx;
}