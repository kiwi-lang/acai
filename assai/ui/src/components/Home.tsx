import { useState, useEffect, useRef } from 'react';
import { Box, VStack, Spinner, Text } from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import { Message } from '../services/types';
import { assaiAPI } from '../services/api';

const Home = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = 'ASSAI - AI Assistant';
  }, []);

  useEffect(() => {
    // Scroll to bottom when new messages arrive, but don't steal focus
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [messages]);

  const handleSendMessage = async (content: string, imageFile?: File, audioFile?: File) => {
    // Handle file uploads first if present
    let imageUrl: string | undefined;
    let audioUrl: string | undefined;

    try {
      if (imageFile) {
        const imageResult = await assaiAPI.uploadImage(imageFile);
        imageUrl = imageResult.url;
      }

      if (audioFile) {
        const audioResult = await assaiAPI.uploadAudio(audioFile);
        audioUrl = audioResult.url;
      }
    } catch (error) {
      console.error('Failed to upload files:', error);
      // Continue with text message even if upload fails
    }

    // Add user message
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content,
      timestamp: new Date(),
      type: imageUrl ? 'image' : audioUrl ? 'audio' : 'text',
      imageUrl,
      audioUrl,
    };

    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      // Send message to backend
      const response = await assaiAPI.sendMessage({
        message: content,
        conversationId: conversationId || undefined,
      });

      // Update conversation ID if new
      if (!conversationId) {
        setConversationId(response.conversationId);
      }

      // Add assistant message
      setMessages(prev => [...prev, response.message]);
    } catch (error) {
      console.error('Failed to send message:', error);

      // Add error message
      const errorMessage: Message = {
        id: Date.now().toString(),
        role: 'assistant',
        content: 'Sorry, I encountered an error processing your message. Please try again.',
        timestamp: new Date(),
        type: 'text',
      };

      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const EmptyState = () => (
    <VStack
      flex={1}
      justify="center"
      align="center"
      p={8}
      gap={6}
    >
      <Box
        w="64px"
        h="64px"
        bg="green.500"
        borderRadius="xl"
        display="flex"
        alignItems="center"
        justifyContent="center"
        fontSize="2xl"
        color="white"
        fontWeight="bold"
      >
        AI
      </Box>

      <VStack gap={2} textAlign="center">
        <Text fontSize="2xl" fontWeight="semibold" color="white">
          Welcome to ASSAI
        </Text>
        <Text fontSize="md" color="gray.400" maxW="md">
          Your AI-powered multi-modal assistant. Ask me anything, generate images,
          convert speech to text, and more.
        </Text>
      </VStack>

      <VStack gap={3} w="100%" maxW="2xl" mt={4}>
        <Text fontSize="sm" fontWeight="semibold" color="gray.300">
          Try asking me to:
        </Text>
        <VStack gap={2} w="100%">
          {[
            '💬 Have a conversation',
            '🎨 Generate an image',
            '🎵 Create audio',
            '📝 Analyze text or images',
          ].map((example) => (
            <Box
              key={example}
              p={3}
              bg="gray.800"
              borderRadius="lg"
              w="100%"
              fontSize="sm"
              color="gray.200"
            >
              {example}
            </Box>
          ))}
        </VStack>
      </VStack>
    </VStack>
  );

  return (
    <Box
      display="flex"
      flexDirection="column"
      h="100vh"
      w="100%"
      bg="gray.900"
    >
      {/* Messages Area */}
      <Box
        flex={1}
        overflowY="auto"
        w="100%"
      >
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <VStack gap={0} w="100%">
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}

            {isLoading && (
              <Box
                w="100%"
                bg="gray.800"
                py={6}
                px={4}
              >
                <Box maxW="48rem" mx="auto" display="flex" gap={4}>
                  <Box
                    w="32px"
                    h="32px"
                    bg="green.500"
                    borderRadius="sm"
                    display="flex"
                    alignItems="center"
                    justifyContent="center"
                    color="white"
                    fontWeight="bold"
                    fontSize="sm"
                  >
                    AI
                  </Box>
                  <Spinner size="sm" mt={1} color="green.500" />
                </Box>
              </Box>
            )}

            <div ref={messagesEndRef} />
          </VStack>
        )}
      </Box>

      {/* Input Area */}
      <ChatInput
        onSendMessage={handleSendMessage}
        disabled={isLoading}
        placeholder={messages.length === 0 ? "Start a conversation..." : "Send a message..."}
      />
    </Box>
  );
};

export default Home;