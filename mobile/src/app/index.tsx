import { ChatView } from '@/components/chat-view';

export { ConversationsList as Conversations } from '@/components/conversations-list';

export default function Index() {
  return <ChatView threadId={null} />;
}
