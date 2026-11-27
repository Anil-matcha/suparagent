import Feather from '@expo/vector-icons/Feather';
import * as Clipboard from 'expo-clipboard';
import { useMemo, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, useWindowDimensions, View } from 'react-native';

import { Text } from '@/components/text';
import { MONO } from '@/lib/fonts';
import { parseMarkdown, type Align, type Span } from '@/lib/markdown';
import { radius, useStyles, useTheme, type Palette } from '@/lib/theme';

const HEADING_SIZE = [0, 22, 20, 18, 16, 15, 14];

function Spans({ spans }: { spans: Span[] }) {
  const styles = useStyles(createStyles);
  return (
    <>
      {spans.map((span, index) => (
        <Text
          key={index}
          style={[
            span.bold && styles.bold,
            span.italic && styles.italic,
            span.del && styles.del,
            span.code && styles.inlineCode,
            span.href ? styles.link : null,
          ]}
          // Opened by the system, never inside the app. A failure to open is not worth an error here.
          onPress={span.href ? () => void Linking.openURL(span.href!).catch(() => undefined) : undefined}
          accessibilityRole={span.href ? 'link' : undefined}>
          {span.text}
        </Text>
      ))}
    </>
  );
}

const spanText = (spans: Span[]) => spans.map((span) => span.text).join('');

/** Width of each column from its longest cell, scaled with the person's text size. Long cells wrap. */
export function columnWidths(header: Span[][], rows: Span[][][], fontScale: number): number[] {
  return header.map((_, column) => {
    const longest = Math.max(spanText(header[column]).length, ...rows.map((row) => spanText(row[column]).length));
    return Math.round(Math.min(240, Math.max(72, longest * 8.5 + 24)) * Math.max(1, fontScale));
  });
}

function CodeBlock({ text, lang }: { text: string; lang?: string }) {
  const styles = useStyles(createStyles);
  const theme = useTheme();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await Clipboard.setStringAsync(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <View style={styles.codeBlock}>
      <View style={styles.codeHeader}>
        <Text style={styles.codeLang}>{lang || 'code'}</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Copy code"
          hitSlop={8}
          onPress={() => void copy()}
          style={styles.copyButton}>
          <Feather name={copied ? 'check' : 'copy'} size={12} color={copied ? theme.success : theme.muted} />
          <Text style={[styles.copyText, copied && styles.copyTextSuccess]}>{copied ? 'Copied' : 'Copy'}</Text>
        </Pressable>
      </View>
      <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator style={styles.codeScroll}>
        <Text selectable style={styles.codeText}>
          {text}
        </Text>
      </ScrollView>
    </View>
  );
}

// Wider than the screen is fine: the table scrolls sideways, so no column is ever cut off.
function Table({ header, rows, align }: { header: Span[][]; rows: Span[][][]; align: Align[] }) {
  const styles = useStyles(createStyles);
  const { fontScale } = useWindowDimensions();
  const widths = columnWidths(header, rows, fontScale);
  // The scroll view is given the table's own height: left to itself it can come out taller than its rows.
  const [height, setHeight] = useState<number | null>(null);
  const line = (cells: Span[][], head: boolean) =>
    cells.map((spans, column) => (
      <View key={column} style={[styles.cell, head && styles.headCell, { width: widths[column] }]}>
        <Text selectable style={[styles.text, styles.cellText, head && styles.bold, { textAlign: align[column] }]}>
          <Spans spans={spans} />
        </Text>
      </View>
    ));
  return (
    <View style={styles.tableWrapper}>
      <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator style={[styles.scroll, height !== null && { height }]}>
        <View style={styles.table} onLayout={(event) => setHeight(Math.ceil(event.nativeEvent.layout.height))}>
          <View style={styles.row}>{line(header, true)}</View>
          {rows.map((row, index) => (
            <View key={index} style={styles.row}>
              {line(row, false)}
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

export function Markdown({ text }: { text: string }) {
  const styles = useStyles(createStyles);
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <View style={styles.root}>
      {blocks.map((block, index) => {
        switch (block.type) {
          case 'heading':
            return (
              <Text key={index} accessibilityRole="header" style={[styles.text, styles.bold, styles.heading, { fontSize: HEADING_SIZE[block.level] }]}>
                <Spans spans={block.spans} />
              </Text>
            );
          case 'code':
            return <CodeBlock key={index} text={block.text} lang={block.lang} />;
          case 'quote':
            return (
              <View key={index} style={styles.quote}>
                <Text selectable style={styles.quoteText}>
                  <Spans spans={block.spans} />
                </Text>
              </View>
            );
          case 'table':
            return <Table key={index} header={block.header} rows={block.rows} align={block.align} />;
          case 'list':
            return (
              <View key={index} style={styles.list}>
                {block.items.map((item, itemIndex) => (
                  <View key={itemIndex} style={styles.item}>
                    <Text style={styles.bullet}>{block.ordered ? `${itemIndex + 1}.` : '•'}</Text>
                    <Text selectable style={[styles.text, styles.itemText]}>
                      <Spans spans={item} />
                    </Text>
                  </View>
                ))}
              </View>
            );
          case 'rule':
            return <View key={index} style={styles.rule} />;
          default:
            return (
              <Text key={index} selectable style={styles.text}>
                <Spans spans={block.spans} />
              </Text>
            );
        }
      })}
    </View>
  );
}

const createStyles = (p: Palette) =>
  StyleSheet.create({
    root: { gap: 8 },
    text: { color: p.text, fontSize: 16, lineHeight: 23 },
    bold: { fontWeight: '700' },
    italic: { fontStyle: 'italic' },
    del: { textDecorationLine: 'line-through' },
    link: { color: p.accentText, textDecorationLine: 'underline' },
    inlineCode: {
      fontFamily: MONO,
      backgroundColor: p.surfaceStrong,
      fontSize: 14,
      borderRadius: 4,
      paddingHorizontal: 5,
      paddingVertical: 1.5,
    },
    codeBlock: {
      backgroundColor: p.surfaceStrong,
      borderRadius: radius.card,
      borderWidth: 1,
      borderColor: p.border,
      overflow: 'hidden',
      marginVertical: 4,
    },
    codeHeader: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      backgroundColor: p.surface,
      paddingHorizontal: 12,
      paddingVertical: 5,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: p.border,
    },
    codeLang: {
      fontSize: 12,
      fontWeight: '600',
      color: p.muted,
      fontFamily: MONO,
      textTransform: 'lowercase',
    },
    copyButton: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      paddingHorizontal: 6,
      paddingVertical: 2,
      borderRadius: 4,
    },
    copyText: {
      fontSize: 12,
      color: p.muted,
    },
    copyTextSuccess: {
      color: p.success,
      fontWeight: '600',
    },
    codeScroll: {
      padding: 10,
    },
    codeText: {
      fontFamily: MONO,
      fontSize: 13,
      lineHeight: 19,
      color: p.text,
    },
    quote: {
      borderLeftWidth: 3,
      borderLeftColor: p.quoteBar,
      paddingLeft: 10,
      paddingVertical: 2,
      marginVertical: 2,
    },
    quoteText: {
      color: p.muted,
      fontSize: 15,
      lineHeight: 22,
      fontStyle: 'italic',
    },
    list: { gap: 4, marginVertical: 2 },
    item: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
    bullet: { fontSize: 16, lineHeight: 23, color: p.muted, minWidth: 16 },
    itemText: { flex: 1 },
    heading: { marginTop: 6, marginBottom: 2 },
    rule: {
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: p.border,
      marginVertical: 8,
    },
    tableWrapper: {
      marginVertical: 6,
      borderRadius: radius.field,
      borderWidth: 1,
      borderColor: p.border,
      overflow: 'hidden',
      alignSelf: 'flex-start',
    },
    scroll: { flexGrow: 0 },
    table: {
      alignSelf: 'flex-start',
    },
    row: { flexDirection: 'row' },
    cell: {
      paddingHorizontal: 10,
      paddingVertical: 6,
      borderRightWidth: StyleSheet.hairlineWidth,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderColor: p.border,
    },
    headCell: { backgroundColor: p.surfaceStrong },
    cellText: { fontSize: 14, lineHeight: 20 },
  });
