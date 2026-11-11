import Feather from '@expo/vector-icons/Feather';
import { Pressable, StyleSheet, View } from 'react-native';

import { Avatar } from '@/components/avatar';
import { Text } from '@/components/text';
import { useStyles, type Palette } from '@/lib/theme';

/** The header of a conversation, with optional model/effort subtitle and tap-to-switch support. */
export function ConversationTitle({
  title,
  agentId,
  agentName,
  subtitle,
  onPress,
}: {
  title: string;
  agentId: number | null;
  agentName: string | null;
  subtitle?: string | null;
  onPress?: () => void;
}) {
  const styles = useStyles(createStyles);
  const detail = [agentName, subtitle].filter(Boolean).join(' · ');

  const content = (
    <View style={styles.row}>
      <Avatar agentId={agentId} name={agentName} size="sm" />
      <View style={styles.text}>
        <View style={styles.titleRow}>
          <Text style={styles.title} numberOfLines={1} maxFontSizeMultiplier={1.3}>
            {title}
          </Text>
          {onPress ? <Feather name="chevron-down" size={13} color={styles.chevron.color} style={styles.chevron} /> : null}
        </View>
        {detail ? (
          <Text style={styles.agent} numberOfLines={1} maxFontSizeMultiplier={1.3}>
            {detail}
          </Text>
        ) : null}
      </View>
    </View>
  );

  if (onPress) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={detail ? `${title}, ${detail}. Tap to change model.` : `${title}. Tap to change model.`}
        testID="header-title-pressable"
        hitSlop={8}
        onPress={onPress}
        style={styles.pressableContainer}>
        {content}
      </Pressable>
    );
  }

  return (
    <View accessible accessibilityRole="header" accessibilityLabel={detail ? `${title}, ${detail}` : title}>
      {content}
    </View>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    pressableContainer: {
      minHeight: 44,
      justifyContent: 'center',
    },
    row: { flexDirection: 'row', alignItems: 'center', gap: 10, maxWidth: 220 },
    titleRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    chevron: { color: p.muted, marginTop: 1 },
    text: { flexShrink: 1 },
    title: { fontSize: 16, fontWeight: '600', color: p.text },
    agent: { fontSize: 12, color: p.muted },
  });
