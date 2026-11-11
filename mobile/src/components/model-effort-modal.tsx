import Feather from '@expo/vector-icons/Feather';
import { useContext, useMemo, useState } from 'react';
import {
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaInsetsContext } from 'react-native-safe-area-context';

import { Text } from '@/components/text';
import { type Engine, modelLabel } from '@/lib/engines';
import { radius, touch, useStyles, useTheme, type Palette } from '@/lib/theme';

export type ModelSelection = {
  provider?: string;
  model: string | null;
  effort: string | null;
};

type Props = {
  visible: boolean;
  onClose: () => void;
  engines: Engine[];
  currentProvider?: string;
  currentModel?: string | null;
  currentEffort?: string | null;
  isDraft?: boolean;
  onSelect: (selection: ModelSelection) => void;
};

export function ModelEffortModal({
  visible,
  onClose,
  engines,
  currentProvider,
  currentModel,
  currentEffort,
  isDraft = false,
  onSelect,
}: Props) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const insets = useContext(SafeAreaInsetsContext) ?? { top: 0, bottom: 0, left: 0, right: 0 };

  const availableEngines = useMemo(() => engines.filter((e) => e.available), [engines]);
  const defaultProvider = availableEngines[0]?.id || currentProvider || 'claude';

  const [selectedProvider, setSelectedProvider] = useState<string>(currentProvider || defaultProvider);
  const [selectedModel, setSelectedModel] = useState<string | null>(currentModel ?? null);
  const [selectedEffort, setSelectedEffort] = useState<string | null>(currentEffort ?? null);
  const [search, setSearch] = useState('');

  // Active engine object
  const activeEngine = useMemo(
    () => engines.find((e) => e.id === selectedProvider) ?? availableEngines[0],
    [engines, availableEngines, selectedProvider],
  );

  // Filtered models
  const models = useMemo(() => {
    if (!activeEngine) return [];
    const list = activeEngine.models;
    if (!search.trim()) return list;
    const q = search.trim().toLowerCase();
    return list.filter((m) => m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q));
  }, [activeEngine, search]);

  // Selected model object
  const activeModelObj = useMemo(() => {
    if (!activeEngine) return undefined;
    if (selectedModel === null) {
      return activeEngine.models.find((m) => m.id === activeEngine.defaultModel) ?? activeEngine.models.find((m) => m.isDefault);
    }
    return activeEngine.models.find((m) => m.id === selectedModel);
  }, [activeEngine, selectedModel]);

  const supportedEfforts = activeModelObj?.supportedReasoningEfforts ?? [];
  const hasEfforts = supportedEfforts.length > 0;

  const handleApply = () => {
    onSelect({
      provider: isDraft ? selectedProvider : undefined,
      model: selectedModel,
      effort: selectedEffort,
    });
    onClose();
  };

  if (!visible) return null;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} accessibilityRole="button" accessibilityLabel="Close model picker" />
        <View
          testID="model-effort-sheet"
          style={[
            styles.sheet,
            {
              paddingBottom: Math.max(insets.bottom, 16),
            },
          ]}>
          {/* Sheet Header */}
          <View style={styles.sheetHeader}>
            <View style={styles.sheetHandle} />
            <View style={styles.sheetHeaderRow}>
              <Text style={styles.sheetTitle}>Model & Reasoning</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close"
                testID="close-model-effort-btn"
                hitSlop={8}
                style={styles.closeBtn}
                onPress={onClose}>
                <Feather name="x" size={20} color={theme.text} />
              </Pressable>
            </View>
          </View>

          {/* If draft mode and multiple available engines: show engine tabs */}
          {isDraft && availableEngines.length > 1 && (
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Engine</Text>
              <View style={styles.engineChips}>
                {availableEngines.map((eng) => {
                  const active = eng.id === selectedProvider;
                  return (
                    <Pressable
                      key={eng.id}
                      accessibilityRole="button"
                      accessibilityLabel={`Engine ${eng.label}`}
                      testID={`engine-chip-${eng.id}`}
                      onPress={() => {
                        setSelectedProvider(eng.id);
                        setSelectedModel(null);
                        setSelectedEffort(null);
                      }}
                      style={[styles.engineChip, active && styles.engineChipActive]}>
                      <Text style={[styles.engineChipText, active && styles.engineChipTextActive]}>
                        {eng.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          )}

          {/* Search box if active engine has more than 4 models */}
          {activeEngine && activeEngine.models.length > 4 && (
            <View style={styles.searchContainer}>
              <Feather name="search" size={16} color={theme.muted} style={styles.searchIcon} />
              <TextInput
                value={search}
                onChangeText={setSearch}
                placeholder="Search models..."
                placeholderTextColor={theme.muted}
                style={styles.searchInput}
                autoCorrect={false}
                autoCapitalize="none"
                clearButtonMode="while-editing"
              />
            </View>
          )}

          {/* Models list */}
          <Text style={styles.sectionLabel}>Model</Text>
          <FlatList
            data={models}
            keyExtractor={(m) => m.id}
            style={styles.modelList}
            keyboardShouldPersistTaps="handled"
            ListHeaderComponent={
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Engine default model"
                testID="model-option-default"
                style={[styles.optionRow, selectedModel === null && styles.optionRowActive]}
                onPress={() => setSelectedModel(null)}>
                <View style={styles.optionInfo}>
                  <Text style={[styles.optionTitle, selectedModel === null && styles.optionTitleActive]}>
                    Engine default
                  </Text>
                  {activeEngine && (
                    <Text style={styles.optionSubtitle}>
                      {modelLabel(activeEngine, null)}
                    </Text>
                  )}
                </View>
                {selectedModel === null && <Feather name="check" size={18} color={theme.accentText} />}
              </Pressable>
            }
            renderItem={({ item }) => {
              const active = selectedModel === item.id;
              return (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Model ${item.label}`}
                  testID={`model-option-${item.id}`}
                  style={[styles.optionRow, active && styles.optionRowActive]}
                  onPress={() => {
                    setSelectedModel(item.id);
                    if (item.defaultReasoningEffort && selectedEffort === null) {
                      setSelectedEffort(item.defaultReasoningEffort);
                    }
                  }}>
                  <View style={styles.optionInfo}>
                    <View style={styles.labelRow}>
                      <Text style={[styles.optionTitle, active && styles.optionTitleActive]}>
                        {item.label}
                      </Text>
                      {item.isDefault && <Text style={styles.badge}>Default</Text>}
                    </View>
                    <Text style={styles.optionSubtitle}>{item.id}</Text>
                  </View>
                  {active && <Feather name="check" size={18} color={theme.accentText} />}
                </Pressable>
              );
            }}
          />

          {/* Reasoning Effort Section */}
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Reasoning Effort</Text>
            {hasEfforts ? (
              <View style={styles.effortChips}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Default reasoning effort"
                  testID="effort-chip-default"
                  onPress={() => setSelectedEffort(null)}
                  style={[styles.effortChip, selectedEffort === null && styles.effortChipActive]}>
                  <Text style={[styles.effortChipText, selectedEffort === null && styles.effortChipTextActive]}>
                    Default
                  </Text>
                </Pressable>
                {supportedEfforts.map((level) => {
                  const active = selectedEffort === level;
                  const label = level.charAt(0).toUpperCase() + level.slice(1);
                  return (
                    <Pressable
                      key={level}
                      accessibilityRole="button"
                      accessibilityLabel={`Reasoning effort ${label}`}
                      testID={`effort-chip-${level}`}
                      onPress={() => setSelectedEffort(level)}
                      style={[styles.effortChip, active && styles.effortChipActive]}>
                      <Text style={[styles.effortChipText, active && styles.effortChipTextActive]}>
                        {label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            ) : (
              <Text style={styles.hint}>
                Reasoning effort is not configurable for this model.
              </Text>
            )}
          </View>

          {/* Apply Button */}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Apply selection"
            testID="apply-model-effort-btn"
            style={({ pressed }) => [styles.applyBtn, pressed && styles.applyBtnPressed]}
            onPress={handleApply}>
            <Text style={styles.applyBtnText}>Apply</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    overlay: {
      flex: 1,
      justifyContent: 'flex-end',
      backgroundColor: 'rgba(0, 0, 0, 0.45)',
    },
    backdrop: {
      ...StyleSheet.absoluteFill,
    },
    sheet: {
      backgroundColor: p.surface,
      borderTopLeftRadius: radius.card,
      borderTopRightRadius: radius.card,
      paddingHorizontal: 20,
      paddingTop: 12,
      maxHeight: '85%',
    },
    sheetHeader: {
      alignItems: 'center',
      marginBottom: 12,
    },
    sheetHandle: {
      width: 36,
      height: 4,
      borderRadius: 2,
      backgroundColor: p.border,
      marginBottom: 10,
    },
    sheetHeaderRow: {
      width: '100%',
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
    sheetTitle: {
      fontSize: 18,
      fontWeight: '600',
      color: p.text,
    },
    closeBtn: {
      minWidth: touch.min,
      minHeight: touch.min,
      alignItems: 'center',
      justifyContent: 'center',
    },
    section: {
      marginTop: 10,
      marginBottom: 8,
    },
    sectionLabel: {
      fontSize: 13,
      fontWeight: '600',
      color: p.muted,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
      marginBottom: 8,
    },
    engineChips: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      marginBottom: 4,
    },
    engineChip: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: radius.field,
      backgroundColor: p.surfaceStrong,
      borderWidth: 1,
      borderColor: p.border,
      minHeight: touch.min,
      justifyContent: 'center',
    },
    engineChipActive: {
      backgroundColor: p.accent,
      borderColor: p.accent,
    },
    engineChipText: {
      fontSize: 14,
      fontWeight: '500',
      color: p.text,
    },
    engineChipTextActive: {
      color: p.onAccent,
    },
    searchContainer: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: p.surfaceStrong,
      borderRadius: radius.field,
      borderWidth: 1,
      borderColor: p.border,
      paddingHorizontal: 10,
      height: 40,
      marginBottom: 10,
    },
    searchIcon: {
      marginRight: 8,
    },
    searchInput: {
      flex: 1,
      fontSize: 14,
      color: p.text,
      paddingVertical: 4,
    },
    modelList: {
      maxHeight: 200,
    },
    optionRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingVertical: 10,
      paddingHorizontal: 12,
      borderRadius: radius.field,
      minHeight: touch.min,
      marginBottom: 4,
    },
    optionRowActive: {
      backgroundColor: p.surfaceStrong,
    },
    optionInfo: {
      flex: 1,
      marginRight: 10,
    },
    labelRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    optionTitle: {
      fontSize: 15,
      fontWeight: '500',
      color: p.text,
    },
    optionTitleActive: {
      color: p.accentText,
      fontWeight: '600',
    },
    optionSubtitle: {
      fontSize: 12,
      color: p.muted,
      marginTop: 2,
    },
    badge: {
      fontSize: 11,
      fontWeight: '600',
      color: p.onAccent,
      backgroundColor: p.accent,
      paddingHorizontal: 6,
      paddingVertical: 2,
      borderRadius: 4,
    },
    effortChips: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
    },
    effortChip: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: radius.field,
      backgroundColor: p.surfaceStrong,
      borderWidth: 1,
      borderColor: p.border,
      minHeight: touch.min,
      justifyContent: 'center',
    },
    effortChipActive: {
      backgroundColor: p.accent,
      borderColor: p.accent,
    },
    effortChipText: {
      fontSize: 14,
      fontWeight: '500',
      color: p.text,
    },
    effortChipTextActive: {
      color: p.onAccent,
    },
    hint: {
      fontSize: 13,
      color: p.muted,
      fontStyle: 'italic',
    },
    applyBtn: {
      backgroundColor: p.accent,
      borderRadius: radius.field,
      height: 48,
      alignItems: 'center',
      justifyContent: 'center',
      marginTop: 14,
    },
    applyBtnPressed: {
      opacity: 0.85,
    },
    applyBtnText: {
      fontSize: 16,
      fontWeight: '600',
      color: p.onAccent,
    },
  });