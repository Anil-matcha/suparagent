import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { MobileSidebar } from '@/components/mobile-sidebar';
import type { Thread } from '@/lib/api';

const mockPush = jest.fn();
const mockApi = {
  listThreads: jest.fn(),
  listAgents: jest.fn(),
};

let mockAuthState: Record<string, unknown> = {
  status: 'paired',
  server: 'https://dots.example.com',
  token: 't',
  label: 'My iPhone',
};

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
}));

jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ state: mockAuthState }),
}));

jest.mock('@/lib/use-api', () => ({
  useApi: () => mockApi,
}));

const mockThread = (id: number, title: string | null): Thread => ({
  id,
  title,
  provider: 'claude',
  model: 'sonnet-3.7',
  session_state: 'active',
  agent_id: null,
  last_seq: 1,
  archived_at: null,
  created_at: '2026-10-27T10:00:00Z',
  updated_at: new Date().toISOString(),
});

describe('MobileSidebar', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuthState = {
      status: 'paired',
      server: 'https://dots.example.com',
      token: 't',
      label: 'My iPhone',
    };
    mockApi.listThreads.mockResolvedValue([
      mockThread(1, 'First thread'),
      mockThread(2, 'Second thread'),
    ]);
    mockApi.listAgents.mockResolvedValue([]);
  });

  it('renders nothing when visible is false', async () => {
    const { toJSON } = await render(
      <MobileSidebar
        visible={false}
        onClose={jest.fn()}
        onNewChat={jest.fn()}
        onSelectThread={jest.fn()}
      />,
    );
    expect(toJSON()).toBeNull();
  });

  it('renders header brand, new conversation button, and threads when visible is true', async () => {
    const onNewChat = jest.fn();
    const onClose = jest.fn();

    await render(
      <MobileSidebar
        visible={true}
        onClose={onClose}
        onNewChat={onNewChat}
        onSelectThread={jest.fn()}
      />,
    );

    expect(screen.getByText('Suparagent')).toBeTruthy();
    expect(screen.getByText('New conversation')).toBeTruthy();
    expect(screen.getByText('Search conversations')).toBeTruthy();
    expect(screen.getByText('Rooms')).toBeTruthy();
    expect(screen.getByText('Conversations')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText('First thread')).toBeTruthy();
      expect(screen.getByText('Second thread')).toBeTruthy();
    });

    // Clicking New conversation triggers onNewChat and onClose
    fireEvent.press(screen.getByTestId('sidebar-new-chat-btn'));
    expect(onClose).toHaveBeenCalled();
    expect(onNewChat).toHaveBeenCalled();
  });

  it('selects a conversation from the list', async () => {
    const onSelectThread = jest.fn();
    const onClose = jest.fn();

    await render(
      <MobileSidebar
        visible={true}
        onClose={onClose}
        onNewChat={jest.fn()}
        onSelectThread={onSelectThread}
        activeThreadId={1}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Second thread')).toBeTruthy();
    });

    fireEvent.press(screen.getByText('Second thread'));
    expect(onClose).toHaveBeenCalled();
    expect(onSelectThread).toHaveBeenCalledWith(2);
  });

  it('navigates to Search from sidebar', async () => {
    const onClose = jest.fn();
    await render(
      <MobileSidebar
        visible={true}
        onClose={onClose}
        onNewChat={jest.fn()}
        onSelectThread={jest.fn()}
      />,
    );

    await screen.findByText('First thread');
    fireEvent.press(screen.getByTestId('sidebar-search-btn'));
    expect(onClose).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith('/search');
  });

  it('navigates to Rooms from sidebar', async () => {
    const onClose = jest.fn();
    await render(
      <MobileSidebar
        visible={true}
        onClose={onClose}
        onNewChat={jest.fn()}
        onSelectThread={jest.fn()}
      />,
    );

    await screen.findByText('First thread');
    fireEvent.press(screen.getByTestId('sidebar-rooms-btn'));
    expect(onClose).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith('/rooms');
  });

  it('renders the Linear profile option at the bottom and navigates to settings', async () => {
    const onClose = jest.fn();
    await render(
      <MobileSidebar
        visible={true}
        onClose={onClose}
        onNewChat={jest.fn()}
        onSelectThread={jest.fn()}
      />,
    );

    await screen.findByText('First thread');
    expect(await screen.findByTestId('sidebar-profile-card')).toBeTruthy();
    expect(await screen.findByText('My iPhone')).toBeTruthy();
    expect(await screen.findByText('dots.example.com')).toBeTruthy();

    fireEvent.press(screen.getByTestId('sidebar-profile-card'));
    expect(onClose).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith('/settings');
  });

  it('displays user email in profile card when using hosted account', async () => {
    mockAuthState = {
      status: 'paired',
      server: 'https://hosted.example.com',
      token: 't',
      hosted: { email: 'alex@example.com', accountToken: 'tok' },
    };

    await render(
      <MobileSidebar
        visible={true}
        onClose={jest.fn()}
        onNewChat={jest.fn()}
        onSelectThread={jest.fn()}
      />,
    );

    await screen.findByText('First thread');
    expect(await screen.findByText('alex@example.com')).toBeTruthy();
    expect(await screen.findByText('Hosted workspace')).toBeTruthy();
  });
});

