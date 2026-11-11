import { useFonts } from 'expo-font';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ActivityIndicator, useColorScheme, View } from 'react-native';
import { KeyboardProvider } from 'react-native-keyboard-controller';

import { HostedGate } from '@/components/hosted-gate';
import { PairingLinkHandler } from '@/components/pairing-link-handler';
import { FontsLoadedContext, useFont } from '@/components/text';
import { AuthProvider, useAuth } from '@/lib/auth-context';
import { FONT_FILES } from '@/lib/font-assets';
import { useTheme } from '@/lib/theme';

function RootStack({ ready }: { ready: boolean }) {
  const { state } = useAuth();
  const theme = useTheme();
  const headerTitleStyle = useFont({ fontWeight: '600' });
  if (state.status === 'loading' || !ready) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.background }}>
        <ActivityIndicator />
      </View>
    );
  }
  const paired = state.status === 'paired';
  return (
    <HostedGate>
    <Stack screenOptions={{ headerTitleStyle }}>
      <Stack.Protected guard={paired}>
        <Stack.Screen name="index" options={{ title: 'Suparagent' }} />
        <Stack.Screen name="search" options={{ title: 'Search' }} />
        <Stack.Screen name="rooms/index" options={{ title: 'Rooms' }} />
        <Stack.Screen name="rooms/[id]" options={{ title: 'Room' }} />
        <Stack.Screen name="thread/[id]" options={{ title: 'Conversation' }} />
        <Stack.Screen name="files/[id]/index" options={{ title: 'Files' }} />
        <Stack.Screen name="files/[id]/view" options={{ title: 'File' }} />
        <Stack.Screen name="new" options={{ title: 'New conversation', presentation: 'modal' }} />
        <Stack.Screen name="settings" options={{ title: 'Settings' }} />
        <Stack.Screen name="agents/index" options={{ title: 'Agents' }} />
        <Stack.Screen name="agents/[id]" options={{ title: 'Agent' }} />
        <Stack.Screen name="room-settings" options={{ title: 'Room' }} />
        <Stack.Screen name="room-details" options={{ title: 'Room details' }} />
      </Stack.Protected>
      <Stack.Protected guard={!paired}>
        <Stack.Screen name="pair" options={{ title: 'Suparagent' }} />
        <Stack.Screen name="hosted" options={{ title: 'Hosted workspace' }} />
        <Stack.Screen name="scan" options={{ title: 'Scan QR code', presentation: 'modal' }} />
      </Stack.Protected>
    </Stack>
    </HostedGate>
  );
}

// Headers, screen backgrounds and borders come from the same palette as the screens themselves.
function useNavigationTheme() {
  const theme = useTheme();
  const base = useColorScheme() === 'dark' ? DarkTheme : DefaultTheme;
  return { ...base, colors: { ...base.colors, background: theme.background, card: theme.background, text: theme.text, border: theme.border, primary: theme.accentText } };
}

export default function RootLayout() {
  const navigationTheme = useNavigationTheme();
  // The screens wait for the fonts; if they cannot load, the app carries on in the system font.
  const [loaded, failed] = useFonts(FONT_FILES);
  return (
    <KeyboardProvider>
      <ThemeProvider value={navigationTheme}>
        <FontsLoadedContext.Provider value={loaded}>
          <AuthProvider>
            <StatusBar style="auto" />
            <PairingLinkHandler />
            <RootStack ready={loaded || failed !== null} />
          </AuthProvider>
        </FontsLoadedContext.Provider>
      </ThemeProvider>
    </KeyboardProvider>
  );
}
