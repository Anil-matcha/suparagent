import Feather from '@expo/vector-icons/Feather';
import { Stack, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, View } from 'react-native';

import { ApprovalCard } from '@/components/approval-card';
import { Composer } from '@/components/composer';
import { ConversationTitle } from '@/components/conversation-title';
import { JumpToLatest } from '@/components/jump-to-latest';
import { KeyboardScreen } from '@/components/keyboard-screen';
import { MobileSidebar } from '@/components/mobile-sidebar';
import { Text } from '@/components/text';
import { DateDivider, TranscriptRow, type Row } from '@/components/transcript-row';
import { WelcomeCard } from '@/components/welcome-card';
import { agentName, type Agent } from '@/lib/agents';
import type { Attachment } from '@/lib/attachments';
import {
  formatReactionReply,
  getCachedThreadReactions,
  isLatestAssistantMessage,
  loadThreadReactions,
  saveThreadReaction,
  type ReactionKind,
  type ReactionsMap,
} from '@/lib/reactions';
import { findDateDividerItemKeys, formatDateDivider } from '@/lib/relative-time';
import { rowKeyForSeq } from '@/lib/search';
import { pendingApprovals } from '@/lib/transcript';
import { useApi } from '@/lib/use-api';
import { useThread } from '@/lib/use-thread';
import { radius, touch, useStyles, useTheme, type Palette } from '@/lib/theme';

const BANNER: Record<string, string> = { connecting: 'Connecting…', reconnecting: 'Reconnecting…' };

type Props = {
  threadId?: number | null;
  seq?: string;
};

export function ChatView({ threadId = null, seq }: Props) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const router = useRouter();
  const api = useApi();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const targetKey = rowKeyForSeq(seq);
  const isDraft = !threadId || threadId <= 0;

  const {
    transcript,
    thread,
    connection,
    error: threadError,
    loaded,
    running,
    send,
    attach,
    attachmentSource,
    stop,
    answerApproval,
  } = useThread(isDraft ? null : threadId);

  const list = useRef<FlatList<Row>>(null);
  const stuckToEnd = useRef(targetKey === null);
  const jumped = useRef(false);
  const [away, setAway] = useState(false);

  const [agents, setAgents] = useState<Agent[]>([]);
  useEffect(() => {
    api?.listAgents().then(setAgents, () => undefined);
  }, [api]);

  const [reactions, setReactions] = useState<ReactionsMap>(() => getCachedThreadReactions(threadId));
  const [sendingReaction, setSendingReaction] = useState(false);

  useEffect(() => {
    if (threadId) {
      loadThreadReactions(threadId).then(setReactions, () => {});
    }
  }, [threadId]);

  const handleReact = async (itemKey: string, reaction: ReactionKind) => {
    if (!threadId) return;
    if (reactions[itemKey] === reaction) {
      const next = saveThreadReaction(threadId, itemKey, null);
      setReactions(next);
      return;
    }
    if (running || sendingReaction) return;
    setSendingReaction(true);
    try {
      const reply = formatReactionReply(reaction);
      const ok = await send(reply);
      if (ok) {
        const next = saveThreadReaction(threadId, itemKey, reaction);
        setReactions(next);
      }
    } finally {
      setSendingReaction(false);
    }
  };

  const handleSend = async (text: string, attachmentIds: string[] = []): Promise<boolean> => {
    if (isDraft) {
      if (!api || starting) return false;
      setStarting(true);
      setStartError(null);
      try {
        const created = await api.createThread();
        await api.sendTurn(created.id, text, attachmentIds);
        router.replace({ pathname: '/thread/[id]', params: { id: String(created.id) } });
        return true;
      } catch (err) {
        setStartError(err instanceof Error ? err.message : 'Failed to start conversation');
        return false;
      } finally {
        setStarting(false);
      }
    }
    return send(text, attachmentIds);
  };

  const handleAttach = async (kind: 'photo' | 'file', room: number): Promise<Attachment[]> => {
    if (isDraft) {
      if (!api || starting) return [];
      try {
        setStarting(true);
        const created = await api.createThread();
        router.replace({ pathname: '/thread/[id]', params: { id: String(created.id) } });
        return [];
      } catch (err) {
        setStartError(err instanceof Error ? err.message : 'Failed to start conversation');
        return [];
      } finally {
        setStarting(false);
      }
    }
    return attach(kind, room);
  };

  const waiting = isDraft ? [] : pendingApprovals(transcript.items);
  const rows: Row[] = isDraft
    ? []
    : transcript.typing
      ? [...transcript.items, { kind: 'typing', key: 'typing', text: transcript.typing }]
      : transcript.items;
  const dividerKeys = findDateDividerItemKeys(rows);

  const targetIndex = targetKey ? rows.findIndex((row) => row.key === targetKey) : -1;
  useEffect(() => {
    if (!targetKey || !loaded || jumped.current) return;
    jumped.current = true;
    if (targetIndex < 0) {
      stuckToEnd.current = true;
      list.current?.scrollToEnd({ animated: false });
      return;
    }
    list.current?.scrollToIndex({ index: targetIndex, viewPosition: 0.15, animated: false });
  }, [targetKey, targetIndex, loaded]);

  const error = startError || threadError;

  return (
    <KeyboardScreen>
      <Stack.Screen
        options={{
          headerLeft: () => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Open menu"
              testID="hamburger-menu-btn"
              hitSlop={8}
              style={styles.headerButton}
              onPress={() => setSidebarOpen(true)}>
              <Feather name="menu" size={22} color={theme.text} />
            </Pressable>
          ),
          headerTitle: () =>
            isDraft ? (
              <Text style={styles.headerTitleText}>Suparagent</Text>
            ) : (
              <ConversationTitle
                title={thread?.title || 'Conversation'}
                agentId={thread?.agent_id ?? null}
                agentName={agentName(agents, thread?.agent_id)}
              />
            ),
          headerRight: () => (
            <View style={styles.headerRightContainer}>
              {!isDraft && (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Files in this conversation"
                  hitSlop={8}
                  style={styles.headerRightItem}
                  onPress={() => router.push({ pathname: '/files/[id]', params: { id: String(threadId) } })}>
                  <Text maxFontSizeMultiplier={1.3} style={styles.headerActionText}>
                    Files
                  </Text>
                </Pressable>
              )}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="New conversation"
                testID="header-new-chat-btn"
                hitSlop={8}
                style={styles.headerRightItem}
                onPress={() => router.navigate('/')}>
                <Feather name="edit-3" size={19} color={theme.accentText} />
              </Pressable>
            </View>
          ),
        }}
      />

      <WelcomeCard />

      {!isDraft && BANNER[connection] ? <Text style={styles.banner}>{BANNER[connection]}</Text> : null}

      {!isDraft && !loaded ? (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      ) : isDraft ? (
        <View style={styles.center}>
          <View style={styles.welcomeIcon}>
            <Feather name="message-square" size={32} color={theme.accentText} />
          </View>
          <Text style={styles.welcomeTitle}>{"What's on your mind?"}</Text>
          <Text style={styles.welcomeSubtitle}>Ask a question, brainstorm, or explore code</Text>
        </View>
      ) : rows.length === 0 ? (
        <View style={styles.center}>
          <Text style={styles.empty}>Say something to start.</Text>
        </View>
      ) : (
        <View style={styles.list}>
          <FlatList
            ref={list}
            testID="conversation-list"
            style={styles.list}
            keyboardShouldPersistTaps="handled"
            onLayout={() => {
              if (stuckToEnd.current) list.current?.scrollToEnd({ animated: false });
            }}
            data={rows}
            keyExtractor={(row) => row.key}
            renderItem={({ item }) => {
              const dividerLabel = dividerKeys.has(item.key) && 'ts' in item && item.ts ? formatDateDivider(item.ts) : null;
              const canReply = item.kind === 'assistant' && !item.incomplete && isLatestAssistantMessage(item.key, transcript.items);
              return (
                <View style={item.key === targetKey ? styles.target : undefined}>
                  {dividerLabel ? <DateDivider label={dividerLabel} /> : null}
                  <TranscriptRow
                    row={item}
                    attachmentSource={attachmentSource}
                    reaction={reactions[item.key] ?? null}
                    onReact={canReply ? (r) => void handleReact(item.key, r) : undefined}
                    disabled={running || sendingReaction}
                  />
                </View>
              );
            }}
            onScrollToIndexFailed={(info) => {
              list.current?.scrollToOffset({ offset: info.averageItemLength * info.index, animated: false });
              setTimeout(() => list.current?.scrollToIndex({ index: info.index, viewPosition: 0.15, animated: false }), 100);
            }}
            contentContainerStyle={styles.content}
            onScroll={({ nativeEvent }) => {
              const { contentOffset, contentSize, layoutMeasurement } = nativeEvent;
              stuckToEnd.current = contentOffset.y + layoutMeasurement.height >= contentSize.height - 80;
              setAway(!stuckToEnd.current);
            }}
            scrollEventThrottle={100}
            onContentSizeChange={() => {
              if (stuckToEnd.current) list.current?.scrollToEnd({ animated: true });
            }}
          />
          {away ? (
            <JumpToLatest
              onPress={() => {
                stuckToEnd.current = true;
                setAway(false);
                list.current?.scrollToEnd({ animated: true });
              }}
            />
          ) : null}
        </View>
      )}

      {error ? (
        <Text style={styles.error} accessibilityRole="alert">
          {error}
        </Text>
      ) : null}

      {waiting.length > 0 ? (
        <ApprovalCard
          key={waiting[0].approvalId}
          item={waiting[0]}
          waiting={waiting.length}
          onAnswer={(decision) => answerApproval(waiting[0].approvalId, decision)}
        />
      ) : null}

      <Composer
        running={running || starting}
        onSend={handleSend}
        onStop={isDraft ? async () => {} : stop}
        onAttach={handleAttach}
      />

      <MobileSidebar
        visible={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        activeThreadId={threadId}
        onNewChat={() => router.navigate('/')}
        onSelectThread={(id) => router.push({ pathname: '/thread/[id]', params: { id: String(id) } })}
      />
    </KeyboardScreen>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    list: { flex: 1, minHeight: 0 },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24 },
    content: { padding: 12 },
    target: { borderLeftWidth: 3, borderLeftColor: p.accentText },
    empty: { fontSize: 16, color: p.muted },
    banner: { textAlign: 'center', paddingVertical: 4, backgroundColor: p.warnBackground, color: p.warnText, fontSize: 13 },
    error: { color: p.danger, paddingHorizontal: 12, paddingVertical: 6, fontSize: 14 },
    headerButton: {
      minHeight: touch.min,
      minWidth: touch.min,
      justifyContent: 'center',
      alignItems: 'flex-start',
    },
    headerTitleText: {
      fontSize: 17,
      fontWeight: '600',
      color: p.text,
    },
    headerRightContainer: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
    },
    headerRightItem: {
      minHeight: touch.min,
      minWidth: 36,
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerActionText: {
      color: p.accentText,
      fontWeight: '500',
      fontSize: 16,
    },
    welcomeIcon: {
      width: 64,
      height: 64,
      borderRadius: radius.round,
      backgroundColor: p.surface,
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: 16,
    },
    welcomeTitle: {
      fontSize: 20,
      fontWeight: '700',
      color: p.text,
      marginBottom: 8,
      textAlign: 'center',
    },
    welcomeSubtitle: {
      fontSize: 14,
      color: p.muted,
      textAlign: 'center',
      lineHeight: 20,
    },
  });
