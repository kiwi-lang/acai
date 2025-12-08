import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Home = () => {
  const { sessionId } = useWebSocket();

  const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
    // Use the multimodal chat endpoint
    const response = await assaiAPI.sendMultimodalMessage(
      { messages: [userMessage] },
      sessionId ?? undefined,
      actionId
    );
    return { message: response.message };
  };

  const config: ChatComponentConfig = {
    title: 'ASSAI - AI Assistant',
    description: 'Your AI-powered multi-modal assistant. Ask me anything, generate images, convert speech to text, and more.',
    allowedInputTypes: ['text', 'image', 'audio'],
    expectedOutputType: 'text', // Multimodal can return any type, but default to text
    placeholder: 'Start a conversation...',
    emptyStateTitle: 'Welcome to ASSAI',
    emptyStateDescription: 'Your AI-powered multi-modal assistant. Ask me anything, generate images, convert speech to text, and more.',
    emptyStateExamples: [
      '💬 Have a conversation',
      '🎨 Generate an image',
      '🎵 Create audio',
      '📝 Analyze text or images',
    ],
    onSendMessage: handleSendMessage,
    defaultShowSettings: false, // Multimodal doesn't have settings panel
  };

  return <ChatComponent config={config} />;
};

export default Home;
