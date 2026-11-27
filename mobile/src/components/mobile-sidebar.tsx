import Feather from '@expo/vector-icons/Feather';
import { useRouter } from 'expo-router';
import { useContext, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  Image,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { SafeAreaInsetsContext } from 'react-native-safe-area-context';

import { Avatar } from '@/components/avatar';
import { Text } from '@/components/text';
import { type Agent } from '@/lib/agents';
import type { Thread } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { useUserProfile } from '@/lib/user-profile';
import { identityColor, ON_IDENTITY } from '@/lib/identity';
import { serverLabel } from '@/lib/server-url';
import { radius, touch, useStyles, useTheme, type Palette } from '@/lib/theme';
import { relativeTime } from '@/lib/time';
import { useApi } from '@/lib/use-api';

const SCREEN_WIDTH = Dimensions.get('window').width;
const DRAWER_WIDTH = Math.min(320, SCREEN_WIDTH * 0.82);

type Props = {
  visible: boolean;
  onClose: () => void;
  activeThreadId?: number | null;
  onNewChat: () => void;
  onSelectThread: (threadId: number) => void;
};

export function MobileSidebar({ visible, onClose, activeThreadId, onNewChat, onSelectThread }: Props) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const router = useRouter();
  const api = useApi();
  const { state } = useAuth();
  const insets = useContext(SafeAreaInsetsContext) ?? { top: 0, bottom: 0, left: 0, right: 0 };

  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);

  const [slideAnim] = useState(() => new Animated.Value(-DRAWER_WIDTH));
  const [fadeAnim] = useState(() => new Animated.Value(0));

  // Load threads and agents whenever the sidebar becomes visible
  useEffect(() => {
    if (!visible || !api) return;
    let cancelled = false;
    void Promise.all([
      api.listThreads().catch(() => []),
      api.listAgents().catch(() => []),
    ]).then(([threadList, agentList]) => {
      if (!cancelled) {
        setThreads(threadList);
        setAgents(agentList);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [visible, api]);

  useEffect(() => {
    const isTest = process.env.NODE_ENV === 'test';
    const anim = Animated.parallel([
      Animated.timing(slideAnim, {
        toValue: visible ? 0 : -DRAWER_WIDTH,
        duration: isTest ? 0 : visible ? 220 : 180,
        useNativeDriver: true,
      }),
      Animated.timing(fadeAnim, {
        toValue: visible ? 1 : 0,
        duration: isTest ? 0 : visible ? 220 : 180,
        useNativeDriver: true,
      }),
    ]);
    anim.start();
    return () => {
      anim.stop();
    };
  }, [visible, slideAnim, fadeAnim]);

  const { displayName, email: userEmail, avatarUrl } = useUserProfile();

  if (!visible) return null;

  const paired = state.status === 'paired';
  const hosted = paired ? state.hosted : undefined;
  const profileName = displayName || hosted?.email || userEmail || (paired ? state.label || 'Connected User' : 'User');
  const profileSub =
    userEmail && displayName && displayName !== userEmail
      ? userEmail
      : hosted
        ? 'Hosted workspace'
        : paired
          ? serverLabel(state.server) || 'Local server'
          : 'Settings';
  const profileInitial = profileName.trim() ? profileName.trim().charAt(0).toUpperCase() : 'U';

  const handleNewChat = () => {
    onClose();
    onNewChat();
  };

  const handleSelect = (threadId: number) => {
    onClose();
    onSelectThread(threadId);
  };

  const handleSearch = () => {
    onClose();
    router.push('/search');
  };

  const handleRooms = () => {
    onClose();
    router.push('/rooms');
  };

  const handleSettings = () => {
    onClose();
    router.push('/settings');
  };

  return (
    <Modal visible={visible} transparent animationType="none" onRequestClose={onClose}>
      <View style={styles.overlay}>
        {/* Backdrop */}
        <Animated.View style={[styles.backdrop, { opacity: fadeAnim }]}>
          <Pressable style={styles.backdropPressable} accessibilityRole="button" accessibilityLabel="Close menu" onPress={onClose} />
        </Animated.View>

        {/* Drawer Panel */}
        <Animated.View
          testID="mobile-sidebar"
          style={[
            styles.drawer,
            {
              width: DRAWER_WIDTH,
              paddingTop: Math.max(insets.top, 12),
              paddingBottom: Math.max(insets.bottom, 12),
              transform: [{ translateX: slideAnim }],
            },
          ]}>
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.brandTitle}>Suparagent</Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close menu"
              testID="sidebar-close-btn"
              hitSlop={8}
              onPress={onClose}
              style={styles.closeButton}>
              <Feather name="x" size={20} color={theme.muted} />
            </Pressable>
          </View>

          {/* New Chat Primary Button */}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="New conversation"
            testID="sidebar-new-chat-btn"
            onPress={handleNewChat}
            style={({ pressed }) => [styles.newButton, pressed && styles.pressed]}>
            <Feather name="plus" size={16} color={styles.newButtonText.color} />
            <Text style={styles.newButtonText}>New conversation</Text>
          </Pressable>

          {/* Quick Search */}
          <Pressable
            accessibilityRole="search"
            accessibilityLabel="Search conversations"
            testID="sidebar-search-btn"
            onPress={handleSearch}
            style={({ pressed }) => [styles.searchButton, pressed && styles.pressed]}>
            <Feather name="search" size={15} color={theme.muted} />
            <Text style={styles.searchText}>Search conversations</Text>
          </Pressable>

          {/* Quick Rooms Link */}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Rooms"
            testID="sidebar-rooms-btn"
            onPress={handleRooms}
            style={({ pressed }) => [styles.navRow, pressed && styles.pressed]}>
            <Feather name="users" size={16} color={theme.muted} />
            <Text style={styles.navRowText}>Rooms</Text>
          </Pressable>

          {/* Section Divider */}
          <View style={styles.divider} />

          {/* Section Title */}
          <Text style={styles.sectionHeader}>Conversations</Text>

          {/* Conversations Scroll List */}
          <ScrollView
            testID="sidebar-threads-scroll"
            style={styles.threadsList}
            contentContainerStyle={styles.threadsListContent}
            keyboardShouldPersistTaps="handled">
            {threads === null ? (
              <View style={styles.loadingContainer}>
                <ActivityIndicator size="small" />
              </View>
            ) : threads.length === 0 ? (
              <Text style={styles.emptyText}>No conversations yet.</Text>
            ) : (
              threads.map((item) => {
                const isActive = item.id === activeThreadId;
                const agent = agents.find((a) => a.id === item.agent_id);
                return (
                  <Pressable
                    key={item.id}
                    accessibilityRole="button"
                    accessibilityLabel={item.title || 'Conversation'}
                    onPress={() => handleSelect(item.id)}
                    style={({ pressed }) => [
                      styles.threadRow,
                      isActive && styles.threadRowActive,
                      pressed && styles.pressed,
                    ]}>
                    <Avatar agentId={item.agent_id ?? null} name={agent?.name} size="sm" />
                    <View style={styles.threadMeta}>
                      <Text style={[styles.threadTitle, isActive && styles.threadTitleActive]} numberOfLines={1}>
                        {item.title || 'Conversation'}
                      </Text>
                      <Text style={styles.threadTime} numberOfLines={1}>
                        {relativeTime(item.updated_at)}
                      </Text>
                    </View>
                  </Pressable>
                );
              })
            )}
          </ScrollView>

          {/* Linear Profile Card at the Bottom */}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Open settings and profile"
            testID="sidebar-profile-card"
            onPress={handleSettings}
            style={({ pressed }) => [styles.profileCard, pressed && styles.pressed]}>
            <View style={styles.profileAvatar}>
              {avatarUrl ? (
                <Image source={{ uri: avatarUrl }} style={styles.profileAvatarImage} />
              ) : (
                <Text style={styles.profileAvatarText}>{profileInitial}</Text>
              )}
            </View>
            <View style={styles.profileInfo}>
              <Text style={styles.profileName} numberOfLines={1}>
                {profileName}
              </Text>
              <Text style={styles.profileSub} numberOfLines={1}>
                {profileSub}
              </Text>
            </View>
            <Feather name="settings" size={17} color={theme.muted} />
          </Pressable>
        </Animated.View>
      </View>
    </Modal>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    overlay: {
      flex: 1,
      flexDirection: 'row',
    },
    backdrop: {
      ...StyleSheet.absoluteFill,
      backgroundColor: 'rgba(0, 0, 0, 0.45)',
      zIndex: 1,
    },
    backdropPressable: {
      flex: 1,
    },
    drawer: {
      zIndex: 2,
      height: '100%',
      backgroundColor: p.background,
      borderRightWidth: 1,
      borderRightColor: p.border,
      paddingHorizontal: 16,
      shadowColor: '#000',
      shadowOffset: { width: 4, height: 0 },
      shadowOpacity: 0.15,
      shadowRadius: 12,
      elevation: 16,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 16,
      marginTop: 4,
    },
    brandTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: p.text,
      letterSpacing: -0.3,
    },
    closeButton: {
      minWidth: touch.min,
      minHeight: touch.min,
      alignItems: 'center',
      justifyContent: 'center',
    },
    newButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      backgroundColor: p.bubbleUser,
      borderRadius: radius.card,
      minHeight: touch.min,
      paddingHorizontal: 16,
      marginBottom: 10,
    },
    newButtonText: {
      fontSize: 15,
      fontWeight: '600',
      color: p.text,
    },
    searchButton: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: p.surface,
      borderRadius: radius.card,
      paddingHorizontal: 12,
      minHeight: 38,
      marginBottom: 8,
      borderWidth: 1,
      borderColor: p.border,
    },
    searchText: {
      fontSize: 13,
      color: p.muted,
    },
    navRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 10,
      paddingHorizontal: 10,
      borderRadius: radius.field,
    },
    navRowText: {
      fontSize: 14,
      fontWeight: '500',
      color: p.text,
    },
    divider: {
      height: 1,
      backgroundColor: p.border,
      marginVertical: 10,
    },
    sectionHeader: {
      fontSize: 11,
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: 0.8,
      color: p.muted,
      marginBottom: 8,
      paddingHorizontal: 4,
    },
    threadsList: {
      flex: 1,
      minHeight: 0,
    },
    threadsListContent: {
      paddingBottom: 12,
      gap: 4,
    },
    loadingContainer: {
      paddingVertical: 20,
      alignItems: 'center',
    },
    emptyText: {
      fontSize: 13,
      color: p.muted,
      fontStyle: 'italic',
      paddingHorizontal: 4,
      paddingVertical: 8,
    },
    threadRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 8,
      paddingHorizontal: 8,
      borderRadius: radius.field,
    },
    threadRowActive: {
      backgroundColor: p.surface,
    },
    threadMeta: {
      flex: 1,
      minWidth: 0,
      gap: 2,
    },
    threadTitle: {
      fontSize: 14,
      color: p.text,
      fontWeight: '500',
    },
    threadTitleActive: {
      fontWeight: '600',
      color: p.accentText,
    },
    threadTime: {
      fontSize: 11,
      color: p.muted,
    },
    profileCard: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 12,
      paddingHorizontal: 6,
      borderTopWidth: 1,
      borderTopColor: p.border,
      marginTop: 8,
    },
    profileAvatar: {
      width: 36,
      height: 36,
      borderRadius: radius.round,
      backgroundColor: identityColor(42),
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
    },
    profileAvatarImage: {
      width: 36,
      height: 36,
      borderRadius: radius.round,
    },
    profileAvatarText: {
      color: ON_IDENTITY,
      fontSize: 16,
      fontWeight: '700',
    },
    profileInfo: {
      flex: 1,
      minWidth: 0,
      gap: 2,
    },
    profileName: {
      fontSize: 13,
      fontWeight: '600',
      color: p.text,
    },
    profileSub: {
      fontSize: 11,
      color: p.muted,
    },
    pressed: {
      opacity: 0.7,
    },
  });
