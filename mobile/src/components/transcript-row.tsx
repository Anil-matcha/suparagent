import Feather from '@expo/vector-icons/Feather';
import * as Clipboard from 'expo-clipboard';
import { useState } from 'react';
import { Image, type ImageSourcePropType, Pressable, StyleSheet, View } from 'react-native';

import { Text } from '@/components/text';
import { Markdown } from '@/components/markdown';
import type { Attachment } from '@/lib/attachments';
import type { ReactionKind } from '@/lib/reactions';
import { formatMessageTime } from '@/lib/relative-time';
import type { TranscriptItem } from '@/lib/transcript';
import { radius, useStyles, useTheme, type Palette } from '@/lib/theme';
import { useUserProfile } from '@/lib/user-profile';

export type Row = TranscriptItem | { kind: 'typing'; key: string; text: string };

const APPROVAL_WORDS: Record<string, string> = {
  pending: 'Waiting for your approval',
  allowed: 'You allowed',
  denied: 'You denied',
  expired: 'Nobody answered',
  cancelled: 'Withdrawn',
};

const TOOL_MARKS: Record<string, string> = { running: '…', done: '✓', error: '✗', stopped: '–' };

type Props = {
  row: Row;
  /** Where an attached image is loaded from (it needs the bearer token); left out, images show as file chips. */
  attachmentSource?: (attachmentId: string) => ImageSourcePropType | undefined;
  reaction?: ReactionKind | null;
  onReact?: (reaction: ReactionKind) => void;
  disabled?: boolean;
};

export function DateDivider({ label }: { label: string }) {
  const styles = useStyles(createStyles);
  return (
    <View style={styles.dateDivider} accessibilityRole="header" accessibilityLabel={label}>
      <View style={styles.dateDividerLine} />
      <View style={styles.dateDividerBadge}>
        <Text style={styles.dateDividerText}>{label}</Text>
      </View>
    </View>
  );
}

function Attachments({ files, source }: { files: Attachment[]; source: Props['attachmentSource'] }) {
  const styles = useStyles(createStyles);
  return (
    <View style={styles.files}>
      {files.map((file) => {
        const image = file.kind === 'image' ? source?.(file.id) : undefined;
        return image ? (
          <Image key={file.id} source={image} style={styles.thumb} accessibilityLabel={file.name} />
        ) : (
          <Text key={file.id} style={styles.fileChip} numberOfLines={1}>
            {file.name}
          </Text>
        );
      })}
    </View>
  );
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await Clipboard.setStringAsync(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={copied ? 'Copied' : label}
      hitSlop={8}
      onPress={() => void copy()}
      style={[styles.actionButton, copied && styles.actionButtonActiveSuccess]}>
      <Feather name={copied ? 'check' : 'copy'} size={13} color={copied ? theme.success : theme.muted} />
    </Pressable>
  );
}

export function TranscriptRow({ row, attachmentSource, reaction = null, onReact, disabled = false }: Props) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const { getUserBubbleColor } = useUserProfile();

  switch (row.kind) {
    case 'user': {
      const timeStr = formatMessageTime(row.ts);
      return (
        <View style={styles.userContainer}>
          <View style={[styles.bubble, styles.user, { backgroundColor: getUserBubbleColor() }]}>
            {row.attachments.length > 0 ? <Attachments files={row.attachments} source={attachmentSource} /> : null}
            <Text selectable style={styles.userText}>
              {row.text}
            </Text>
          </View>
          <View style={styles.userFooter}>
            {row.text ? <CopyButton text={row.text} label="Copy message" /> : null}
            {timeStr ? <Text style={styles.timeText}>{timeStr}</Text> : null}
          </View>
        </View>
      );
    }
    case 'assistant': {
      const timeStr = formatMessageTime(row.ts);
      const showReactions = Boolean(onReact || reaction);
      return (
        <View style={[styles.bubble, styles.assistant]}>
          <Markdown text={row.text} />
          {row.incomplete ? <Text style={styles.muted}>(stopped before it finished)</Text> : null}
          {row.text ? (
            <View style={styles.assistantFooter}>
              <CopyButton text={row.text} label="Copy response" />
              {showReactions ? (
                <View style={styles.reactionsRow}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Reply: Looks good, proceed"
                    accessibilityState={{ selected: reaction === 'thumbs_up', disabled: disabled || !onReact }}
                    disabled={disabled || !onReact}
                    hitSlop={8}
                    onPress={() => onReact?.('thumbs_up')}
                    style={[
                      styles.actionButton,
                      reaction === 'thumbs_up' && styles.actionButtonActiveSuccess,
                      (disabled || !onReact) && styles.actionButtonDisabled,
                    ]}>
                    <Feather
                      name="thumbs-up"
                      size={13}
                      color={reaction === 'thumbs_up' ? theme.success : disabled || !onReact ? theme.muted + '70' : theme.muted}
                    />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Reply: No, please revise"
                    accessibilityState={{ selected: reaction === 'thumbs_down', disabled: disabled || !onReact }}
                    disabled={disabled || !onReact}
                    hitSlop={8}
                    onPress={() => onReact?.('thumbs_down')}
                    style={[
                      styles.actionButton,
                      reaction === 'thumbs_down' && styles.actionButtonActiveDanger,
                      (disabled || !onReact) && styles.actionButtonDisabled,
                    ]}>
                    <Feather
                      name="thumbs-down"
                      size={13}
                      color={reaction === 'thumbs_down' ? theme.danger : disabled || !onReact ? theme.muted + '70' : theme.muted}
                    />
                  </Pressable>
                </View>
              ) : null}
              {timeStr ? <Text style={styles.timeText}>{timeStr}</Text> : null}
            </View>
          ) : null}
        </View>
      );
    }
    case 'typing':
      return (
        <View style={[styles.bubble, styles.assistant]}>
          <Text style={styles.assistantText}>{row.text}</Text>
        </View>
      );
    case 'tool':
      return (
        <Text style={styles.line} numberOfLines={2}>
          {TOOL_MARKS[row.state]} {row.summary || row.name}
        </Text>
      );
    case 'approval':
      return (
        <View style={styles.approval}>
          <Text style={styles.approvalTitle}>{APPROVAL_WORDS[row.status] ?? row.status}</Text>
          <Text style={styles.assistantText} numberOfLines={3}>
            {row.summary || row.tool}
          </Text>
        </View>
      );
    case 'turn_end':
      return (
        <Text style={styles.line}>
          {row.status === 'failed' ? `The answer failed${row.error ? `: ${row.error}` : '.'}` : row.status === 'cancelled' ? 'Stopped.' : 'The answer was interrupted.'}
        </Text>
      );
    case 'note':
      return <Text style={styles.line}>{row.text}</Text>;
    default:
      return <Text style={styles.line}>An update this version of the app cannot show ({row.type}).</Text>;
  }
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    bubble: { marginVertical: 4 },
    userContainer: {
      alignSelf: 'flex-end',
      maxWidth: '88%',
      marginVertical: 4,
      alignItems: 'flex-end',
    },
    user: {
      backgroundColor: p.bubbleUser,
      borderRadius: radius.card,
      borderBottomRightRadius: 6,
      paddingHorizontal: 14,
      paddingVertical: 10,
    },
    userFooter: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'flex-end',
      gap: 6,
      marginTop: 4,
      marginRight: 2,
    },
    assistant: { alignSelf: 'stretch', paddingHorizontal: 4, paddingVertical: 4 },
    userText: { color: p.text, fontSize: 16, lineHeight: 22 },
    assistantText: { color: p.text, fontSize: 16, lineHeight: 22 },
    files: { gap: 6, marginBottom: 6 },
    thumb: { width: 180, height: 180, borderRadius: radius.field, backgroundColor: p.surface },
    fileChip: { color: p.text, fontSize: 13, backgroundColor: p.surface, borderRadius: radius.field, paddingHorizontal: 8, paddingVertical: 4, overflow: 'hidden' },
    muted: { color: p.muted, fontSize: 12, marginTop: 4 },
    line: { color: p.muted, fontSize: 13, marginVertical: 3, paddingHorizontal: 4 },
    approval: { alignSelf: 'stretch', borderWidth: 1, borderColor: p.warnBorder, backgroundColor: p.warnBackground, borderRadius: radius.field, padding: 10, marginVertical: 6 },
    approvalTitle: { color: p.warnText, fontSize: 13, fontWeight: '700', marginBottom: 2 },
    assistantFooter: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginTop: 6,
      paddingLeft: 2,
    },
    reactionsRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
    },
    actionButton: {
      width: 28,
      height: 28,
      borderRadius: radius.field,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: p.surface,
    },
    actionButtonActiveSuccess: {
      backgroundColor: p.success + '25',
    },
    actionButtonActiveDanger: {
      backgroundColor: p.danger + '25',
    },
    actionButtonDisabled: {
      opacity: 0.5,
    },
    timeText: {
      fontSize: 11,
      color: p.muted,
      marginLeft: 2,
    },
    dateDivider: {
      position: 'relative',
      alignItems: 'center',
      justifyContent: 'center',
      marginVertical: 14,
    },
    dateDividerLine: {
      position: 'absolute',
      left: 0,
      right: 0,
      height: StyleSheet.hairlineWidth,
      backgroundColor: p.border,
    },
    dateDividerBadge: {
      paddingHorizontal: 10,
      paddingVertical: 2.5,
      borderRadius: 12,
      backgroundColor: p.surface,
      borderWidth: 1,
      borderColor: p.border,
    },
    dateDividerText: {
      fontSize: 11,
      fontWeight: '500',
      color: p.muted,
    },
  });
