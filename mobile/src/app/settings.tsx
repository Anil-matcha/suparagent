import { useState } from 'react';
import { Alert, Linking, Modal, Pressable, ScrollView, StyleSheet, Switch, View } from 'react-native';

import { useRouter } from 'expo-router';

import { Text, TextInput } from '@/components/text';
import { canManageAgents } from '@/lib/agents';
import { useAuth } from '@/lib/auth-context';
import { describeHostedError, HOSTED_ORIGIN, HostedError } from '@/lib/hosted';
import { isPlainHttp, NOT_ENCRYPTED_NOTICE, serverLabel } from '@/lib/server-url';
import { radius, touch, useStyles, useTheme, type Palette } from '@/lib/theme';
import { BUBBLE_TINTS, useUserProfile } from '@/lib/user-profile';

export default function Settings() {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const router = useRouter();
  const { state, signOut, hostedSetFiles } = useAuth();
  const { displayName, email: userEmail, bubbleTint, setDisplayName, setBubbleTint, getUserBubbleColor } = useUserProfile();
  const [busy, setBusy] = useState(false);
  const [filesBusy, setFilesBusy] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  if (state.status !== 'paired') return null;

  const hosted = state.hosted;
  const confirmSignOut = () => {
    Alert.alert(
      hosted ? 'Sign out of your hosted workspace?' : 'Sign out and forget this server?',
      hosted ? 'This phone will be signed out of your workspace. Your agents and conversations stay in the cloud; sign in again to use them.' : 'This phone will be signed out on your server and forget its address. You can pair again with a new code.',
      [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign out',
        style: 'destructive',
        onPress: async () => {
          setBusy(true);
          await signOut();
        },
      },
    ]);
  };

  const toggleFiles = async (enabled: boolean) => {
    setFilesBusy(true);
    setFilesError(null);
    try {
      await hostedSetFiles(enabled);
    } catch (error) {
      setFilesError(error instanceof HostedError ? describeHostedError(error) : 'Something went wrong. Try again.');
    } finally {
      setFilesBusy(false);
    }
  };

  const currentDisplayName = displayName || userEmail || (hosted?.email ? hosted.email.split('@')[0] : 'User');

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      {/* Profile & Appearance Section */}
      <Text style={styles.sectionHeader}>Profile & Appearance</Text>
      <View style={styles.profileSection}>
        <View style={styles.row}>
          <View style={styles.rowBody}>
            <Text style={styles.label}>Display name</Text>
            <Text style={styles.value}>{currentDisplayName}</Text>
            <Text style={styles.hint}>Shown in conversations and the sidebar profile.</Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Edit display name"
            style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
            onPress={() => {
              setNameInput(displayName || '');
              setEditingName(true);
            }}>
            <Text style={styles.smallButtonText}>Edit</Text>
          </Pressable>
        </View>

        {userEmail ? (
          <View style={styles.row}>
            <View style={styles.rowBody}>
              <Text style={styles.label}>Account email</Text>
              <Text style={styles.value}>{userEmail}</Text>
            </View>
          </View>
        ) : null}

        <View style={styles.tintSection}>
          <Text style={styles.label}>Chat bubble color</Text>
          <Text style={styles.hint}>Personalize your message tint across all conversations.</Text>
          <View style={styles.tintRow}>
            {BUBBLE_TINTS.map((tint) => {
              const isSelected = bubbleTint === tint.id;
              return (
                <Pressable
                  key={tint.id}
                  accessibilityRole="button"
                  accessibilityLabel={`${tint.label} bubble color`}
                  accessibilityState={{ selected: isSelected }}
                  hitSlop={6}
                  style={[
                    styles.tintSwatch,
                    { backgroundColor: tint.preview },
                    isSelected && styles.tintSwatchSelected,
                  ]}
                  onPress={() => void setBubbleTint(tint.id)}>
                  {isSelected ? <Text style={styles.tintCheckmark}>✓</Text> : null}
                </Pressable>
              );
            })}
          </View>
          <View style={[styles.bubblePreview, { backgroundColor: getUserBubbleColor() }]}>
            <Text style={styles.bubblePreviewText}>Hello! This is how your messages look in chat.</Text>
          </View>
        </View>
      </View>

      {/* Edit Name Modal */}
      <Modal visible={editingName} transparent animationType="fade" onRequestClose={() => setEditingName(false)}>
        <Pressable style={styles.modalScrim} accessibilityLabel="Close modal" onPress={() => setEditingName(false)} />
        <View style={styles.modalContent} accessibilityViewIsModal>
          <Text style={styles.modalTitle}>Edit display name</Text>
          <TextInput
            style={styles.nameInput}
            value={nameInput}
            onChangeText={setNameInput}
            placeholder="Your name"
            placeholderTextColor={theme.muted}
            autoFocus
            maxLength={50}
          />
          <View style={styles.modalActions}>
            {displayName ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Reset to default name"
                style={({ pressed }) => [styles.resetBtn, pressed && styles.pressed]}
                onPress={async () => {
                  await setDisplayName(null);
                  setEditingName(false);
                }}>
                <Text style={styles.resetBtnText}>Reset</Text>
              </Pressable>
            ) : null}
            <View style={styles.flexSpacer} />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Cancel editing name"
              style={({ pressed }) => [styles.cancelBtn, pressed && styles.pressed]}
              onPress={() => setEditingName(false)}>
              <Text style={styles.cancelBtnText}>Cancel</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Save display name"
              style={({ pressed }) => [styles.saveBtn, pressed && styles.pressed]}
              onPress={async () => {
                await setDisplayName(nameInput.trim() || null);
                setEditingName(false);
              }}>
              <Text style={styles.saveBtnText}>Save</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      {/* Workspace & Connection Section */}
      <Text style={styles.sectionHeader}>Workspace & Connection</Text>
      {hosted ? (
        <>
          <Text style={styles.label}>Workspace</Text>
          <Text style={styles.value}>Hosted ({serverLabel(state.server)})</Text>
          <Text style={styles.label}>Account</Text>
          <Text style={styles.value}>{hosted.email}</Text>
        </>
      ) : (
        <>
          <Text style={styles.label}>Server address</Text>
          <Text style={styles.value}>{serverLabel(state.server)}</Text>
          {isPlainHttp(state.server) ? <Text style={styles.notice}>{NOT_ENCRYPTED_NOTICE}</Text> : null}
        </>
      )}
      <Text style={styles.label}>Device name</Text>
      <Text style={styles.value}>{state.label || 'Phone'}</Text>
      <Text style={styles.label}>Access</Text>
      <Text style={styles.value}>{accessText(state.scopes)}</Text>
      {canManageAgents(state.scopes) ? (
        <Pressable accessibilityRole="button" style={({ pressed }) => [styles.row, pressed && styles.pressed]} onPress={() => router.push('/agents')}>
          <Text style={styles.rowText}>Agents</Text>
          <Text style={styles.value}>Create and edit</Text>
        </Pressable>
      ) : null}
      {hosted ? (
        <View style={styles.row}>
          <View style={styles.rowBody}>
            <Text style={styles.rowText}>File access</Text>
            <Text style={styles.hint}>Let this phone browse and send files in your workspace.</Text>
          </View>
          <Switch accessibilityLabel="File access" value={state.scopes.includes('files')} disabled={filesBusy || busy} onValueChange={(value) => void toggleFiles(value)} />
        </View>
      ) : null}
      {hosted && filesError ? <Text style={styles.notice}>{filesError}</Text> : null}
      {hosted ? (
        <Pressable accessibilityRole="link" style={({ pressed }) => [styles.row, pressed && styles.pressed]} onPress={() => void Linking.openURL(`${HOSTED_ORIGIN}/account/phones`)}>
          <Text style={styles.rowText}>Connected phones</Text>
          <Text style={styles.value}>Manage on suparagent.ai</Text>
        </Pressable>
      ) : null}
      <Pressable accessibilityRole="button" style={[styles.danger, busy && styles.disabled]} disabled={busy} onPress={confirmSignOut}>
        <Text style={styles.dangerText}>{hosted ? 'Sign out of the hosted workspace' : 'Sign out and forget this server'}</Text>
      </Pressable>
    </ScrollView>
  );
}

function accessText(scopes: readonly string[]): string {
  const files = scopes.includes('files');
  if (canManageAgents(scopes)) return 'Chat, files and agents';
  return files ? 'Chat and files' : 'Chat only';
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    scroll: { flex: 1 },
    container: { padding: 20, paddingBottom: 40, gap: 4 },
    sectionHeader: { fontSize: 13, fontWeight: '700', letterSpacing: 0.4, color: p.muted, textTransform: 'uppercase', marginTop: 16, marginBottom: 4 },
    profileSection: { marginBottom: 16 },
    tintSection: { marginTop: 14, gap: 6 },
    tintRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 6, marginBottom: 8 },
    tintSwatch: {
      width: 38,
      height: 38,
      borderRadius: radius.round,
      alignItems: 'center',
      justifyContent: 'center',
      borderWidth: 2,
      borderColor: 'transparent',
    },
    tintSwatchSelected: {
      borderColor: p.accent,
      transform: [{ scale: 1.08 }],
    },
    tintCheckmark: {
      color: '#ffffff',
      fontSize: 16,
      fontWeight: '700',
      textShadowColor: 'rgba(0, 0, 0, 0.4)',
      textShadowOffset: { width: 0, height: 1 },
      textShadowRadius: 2,
    },
    bubblePreview: {
      alignSelf: 'flex-start',
      borderRadius: radius.card,
      borderBottomLeftRadius: 6,
      paddingHorizontal: 14,
      paddingVertical: 10,
      marginTop: 6,
      maxWidth: '90%',
    },
    bubblePreviewText: {
      color: p.text,
      fontSize: 14,
      lineHeight: 20,
    },
    smallButton: {
      minHeight: touch.min,
      paddingHorizontal: 14,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: radius.field,
      borderWidth: 1,
      borderColor: p.border,
      backgroundColor: p.surface,
    },
    smallButtonText: {
      fontSize: 14,
      fontWeight: '600',
      color: p.text,
    },
    modalScrim: {
      ...StyleSheet.absoluteFill,
      backgroundColor: 'rgba(0, 0, 0, 0.45)',
    },
    modalContent: {
      marginHorizontal: 24,
      marginTop: 'auto',
      marginBottom: 'auto',
      backgroundColor: p.background,
      borderRadius: radius.card,
      padding: 20,
      gap: 12,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: p.border,
    },
    modalTitle: {
      fontSize: 17,
      fontWeight: '600',
      color: p.text,
    },
    nameInput: {
      minHeight: touch.min,
      backgroundColor: p.surface,
      borderWidth: 1,
      borderColor: p.inputBorder,
      borderRadius: radius.field,
      paddingHorizontal: 12,
      fontSize: 16,
      color: p.text,
    },
    modalActions: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      marginTop: 8,
    },
    flexSpacer: {
      flex: 1,
    },
    resetBtn: {
      minHeight: touch.min,
      justifyContent: 'center',
      paddingHorizontal: 10,
    },
    resetBtnText: {
      fontSize: 14,
      color: p.danger,
      fontWeight: '500',
    },
    cancelBtn: {
      minHeight: touch.min,
      justifyContent: 'center',
      paddingHorizontal: 12,
    },
    cancelBtnText: {
      fontSize: 15,
      color: p.muted,
    },
    saveBtn: {
      minHeight: touch.min,
      justifyContent: 'center',
      paddingHorizontal: 18,
      backgroundColor: p.accent,
      borderRadius: radius.field,
    },
    saveBtnText: {
      fontSize: 15,
      fontWeight: '600',
      color: p.onAccent,
    },
    label: { fontSize: 13, fontWeight: '600', marginTop: 12, color: p.muted },
    value: { fontSize: 16, color: p.text },
    notice: { fontSize: 14, color: p.notice },
    row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: touch.min, marginTop: 8, paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: p.border },
    rowText: { fontSize: 16, fontWeight: '600', color: p.text },
    rowBody: { flex: 1, paddingRight: 12 },
    hint: { fontSize: 13, color: p.muted },
    pressed: { opacity: 0.7 },
    danger: { marginTop: 32, borderWidth: 1, borderColor: p.danger, borderRadius: radius.field, paddingVertical: 14, alignItems: 'center' },
    dangerText: { color: p.danger, fontSize: 16, fontWeight: '600' },
    disabled: { opacity: 0.5 },
  });
