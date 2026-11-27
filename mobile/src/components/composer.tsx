import Feather from '@expo/vector-icons/Feather';
import { useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, View } from 'react-native';

import { Text, TextInput } from '@/components/text';
import { MAX_ATTACHMENTS, type Attachment } from '@/lib/attachments';
import { radius, touch, useStyles, useTheme, type Palette } from '@/lib/theme';

type Props = {
  running: boolean;
  onSend: (text: string, attachmentIds: string[]) => Promise<boolean>;
  onStop: () => Promise<void>;
  /** Opens a picker and uploads the choice; left out, the composer has no attach button. */
  onAttach?: (kind: 'photo' | 'file', room: number) => Promise<Attachment[]>;
  /** Names that can be tagged with `@`; shown as buttons that add the name to the message. */
  mentions?: string[];
  /** A room's messages are at most 8,000 characters; a conversation's turn allows far more. */
  maxLength?: number;
};

export function Composer({ running, onSend, onStop, onAttach, mentions = [], maxLength = 20_000 }: Props) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [focused, setFocused] = useState(false);
  const [attached, setAttached] = useState<Attachment[]>([]);
  const [multilineExpand, setMultilineExpand] = useState(false);
  const canSend = text.trim().length > 0 && !running && !sending && !uploading;

  const isExpanded = multilineExpand || text.includes('\n') || text.length > 32 || attached.length > 0;

  const handleTextChange = (next: string) => {
    setText(next);
    if (next.length < 20 && !next.includes('\n') && multilineExpand) {
      setMultilineExpand(false);
    }
  };

  const handleContentSizeChange = (e: { nativeEvent: { contentSize: { height: number } } }) => {
    const h = e.nativeEvent.contentSize.height;
    if (h > 52) {
      setMultilineExpand(true);
    }
  };

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    // The message and its files are cleared only once the server accepted them, so a failure never loses them.
    if (await onSend(text.trim(), attached.map((file) => file.id))) {
      setText('');
      setAttached([]);
      setMultilineExpand(false);
    }
    setSending(false);
  };

  const pick = async (kind: 'photo' | 'file') => {
    if (!onAttach) return;
    setUploading(true);
    try {
      const added = await onAttach(kind, MAX_ATTACHMENTS - attached.length);
      setAttached((current) => [...current, ...added].slice(0, MAX_ATTACHMENTS));
    } finally {
      setUploading(false);
    }
  };

  // A name is added after a space, so it is never glued to the word before it.
  const mention = (name: string) => setText((current) => `${current}${current && !/\s$/.test(current) ? ' ' : ''}@${name} `);

  const choose = () =>
    Alert.alert('Attach', undefined, [
      { text: 'Photo', onPress: () => void pick('photo') },
      { text: 'File', onPress: () => void pick('file') },
      { text: 'Cancel', style: 'cancel' },
    ]);

  const renderAttachButton = () =>
    onAttach ? (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Attach"
        style={[styles.attach, (uploading || attached.length >= MAX_ATTACHMENTS) && styles.disabled]}
        disabled={uploading || attached.length >= MAX_ATTACHMENTS}
        onPress={choose}
      >
        <Feather name="plus" size={20} color={theme.muted} />
      </Pressable>
    ) : null;

  const renderActionButton = () =>
    running ? (
      <Pressable accessibilityRole="button" accessibilityLabel="Stop" style={[styles.actionButton, styles.stopButton]} onPress={() => void onStop()}>
        <Feather name="square" size={14} color={theme.danger} />
      </Pressable>
    ) : (
      <Pressable accessibilityRole="button" accessibilityLabel="Send" style={[styles.actionButton, styles.sendButton, !canSend && styles.sendDisabled]} disabled={!canSend} onPress={send}>
        <Feather name="arrow-up" size={20} color={theme.onAccent} />
      </Pressable>
    );

  return (
    <View style={styles.wrap}>
      <View style={[styles.card, isExpanded && styles.cardExpanded, focused && styles.cardFocused]}>
        {attached.length > 0 || uploading ? (
          <View style={styles.chips}>
            {attached.map((file) => (
              <View key={file.id} style={styles.chip}>
                <Text style={styles.chipText} numberOfLines={1}>
                  {file.name}
                </Text>
                <Pressable accessibilityRole="button" accessibilityLabel={`Remove ${file.name}`} hitSlop={16} onPress={() => setAttached((current) => current.filter((other) => other.id !== file.id))}>
                  <Text style={styles.chipRemove}>✕</Text>
                </Pressable>
              </View>
            ))}
            {uploading ? <ActivityIndicator accessibilityLabel="Uploading" /> : null}
          </View>
        ) : null}
        {mentions.length > 0 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={styles.mentions}>
            {mentions.map((name) => (
              <Pressable key={name} accessibilityRole="button" accessibilityLabel={`Mention ${name}`} hitSlop={{ top: 10, bottom: 10 }} style={styles.chip} onPress={() => mention(name)}>
                <Text style={styles.chipText}>@{name}</Text>
              </Pressable>
            ))}
          </ScrollView>
        ) : null}
        {isExpanded ? (
          <View style={styles.expandedCol}>
            <TextInput
              style={styles.expandedInput}
              value={text}
              onChangeText={handleTextChange}
              onContentSizeChange={handleContentSizeChange}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder="Ask SuparAgent"
              placeholderTextColor={theme.muted}
              multiline
              maxLength={maxLength}
              accessibilityLabel="Message"
              underlineColorAndroid="transparent"
            />
            <View style={styles.actionsBar}>
              <View style={styles.actionsLeft}>{renderAttachButton()}</View>
              <View style={styles.actionsRight}>{renderActionButton()}</View>
            </View>
          </View>
        ) : (
          <View style={styles.singleRow}>
            {renderAttachButton()}
            <TextInput
              style={styles.singleInput}
              value={text}
              onChangeText={handleTextChange}
              onContentSizeChange={handleContentSizeChange}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder="Ask SuparAgent"
              placeholderTextColor={theme.muted}
              multiline
              maxLength={maxLength}
              accessibilityLabel="Message"
              underlineColorAndroid="transparent"
            />
            {renderActionButton()}
          </View>
        )}
      </View>
    </View>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    wrap: {
      paddingHorizontal: 6,
      paddingTop: 4,
      paddingBottom: 2,
      backgroundColor: p.background,
    },
    card: {
      backgroundColor: p.surface,
      borderWidth: 1,
      borderColor: p.border,
      borderRadius: 24,
      paddingHorizontal: 4,
      paddingVertical: 3,
      shadowColor: p.text,
      shadowOpacity: 0.06,
      shadowRadius: 3,
      shadowOffset: { width: 0, height: 1 },
      elevation: 1,
    },
    cardExpanded: {
      borderRadius: 18,
      paddingHorizontal: 6,
      paddingVertical: 6,
    },
    cardFocused: {
      borderColor: p.accent,
    },
    chips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6, paddingHorizontal: 6, paddingBottom: 6 },
    chip: { flexDirection: 'row', alignItems: 'center', gap: 6, maxWidth: '60%', backgroundColor: p.surfaceStrong, borderRadius: radius.card, paddingHorizontal: 10, paddingVertical: 4 },
    chipText: { fontSize: 13, color: p.text, flexShrink: 1 },
    mentions: { gap: 6, paddingHorizontal: 6, paddingBottom: 6 },
    chipRemove: { fontSize: 13, color: p.muted },
    singleRow: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    singleInput: {
      flex: 1,
      minHeight: touch.min,
      fontSize: 16,
      color: p.text,
      borderColor: p.inputBorder,
      borderWidth: 0,
      paddingHorizontal: 8,
      paddingTop: 10,
      paddingBottom: 10,
      textAlignVertical: 'center',
    },
    expandedCol: {
      width: '100%',
    },
    expandedInput: {
      width: '100%',
      minHeight: 52,
      maxHeight: 120,
      fontSize: 16,
      color: p.text,
      borderColor: p.inputBorder,
      borderWidth: 0,
      paddingHorizontal: 8,
      paddingTop: 6,
      paddingBottom: 8,
      textAlignVertical: 'top',
    },
    actionsBar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingTop: 2,
    },
    actionsLeft: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    actionsRight: {
      flexDirection: 'row',
      alignItems: 'center',
    },
    attach: {
      width: touch.min,
      height: touch.min,
      borderRadius: radius.round,
      alignItems: 'center',
      justifyContent: 'center',
    },
    actionButton: {
      width: touch.min,
      height: touch.min,
      borderRadius: radius.round,
      alignItems: 'center',
      justifyContent: 'center',
    },
    sendButton: {
      backgroundColor: p.accent,
    },
    sendDisabled: {
      opacity: 0.35,
    },
    stopButton: {
      borderWidth: 1.5,
      borderColor: p.danger,
      backgroundColor: p.surface,
    },
    disabled: { opacity: 0.5 },
  });
