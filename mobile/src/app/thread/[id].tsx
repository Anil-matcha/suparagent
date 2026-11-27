import { useLocalSearchParams } from 'expo-router';
import { ChatView } from '@/components/chat-view';

export default function Thread() {
  const { id, seq } = useLocalSearchParams<{ id: string; seq?: string }>();
  return <ChatView threadId={Number(id)} seq={seq} />;
}
