import { useState, useEffect, useRef, useCallback } from 'react';
import { Box, VStack, Spinner, Text } from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import LogDisplay from './LogDisplay';
import { Message } from '../services/types';
import { MultimodalConversation, MultimodalMessage, Input } from '../services/types';
import { assaiAPI } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Home = () => {
  const { socket, sessionId } = useWebSocket();
  const [messages, setMessages] = useState<Message[]>([]);
  const [multimodalConversation, setMultimodalConversation] = useState<MultimodalConversation>({ messages: [] });
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const actionIdCounterRef = useRef<number>(0);
  const actionIdToMessageIdRef = useRef<Map<number, string>>(new Map());

  // Memoize handlers to prevent duplicate listeners
  const handleStdout = useCallback((data: { id: number; line: string }) => {
    const messageId = actionIdToMessageIdRef.current.get(data.id);
    if (messageId) {
      setMessages(prev => prev.map(msg => {
        if (msg.id === messageId) {
          return {
            ...msg,
            logs: [
              ...(msg.logs || []),
              { type: 'stdout' as const, line: data.line, timestamp: new Date() }
            ]
          };
        }
        return msg;
      }));
    }
  }, []);

  const handleStderr = useCallback((data: { id: number; line: string }) => {
    const messageId = actionIdToMessageIdRef.current.get(data.id);
    if (messageId) {
      setMessages(prev => prev.map(msg => {
        if (msg.id === messageId) {
          return {
            ...msg,
            logs: [
              ...(msg.logs || []),
              { type: 'stderr' as const, line: data.line, timestamp: new Date() }
            ]
          };
        }
        return msg;
      }));
    }
  }, []);

  useEffect(() => {
    document.title = 'ASSAI - AI Assistant';
  }, []);

  useEffect(() => {
    if (!socket) {
      return;
    }

    // Set up log listeners for stdout/stderr
    socket.off('stdout', handleStdout);
    socket.off('stderr', handleStderr);
    socket.on('stdout', handleStdout);
    socket.on('stderr', handleStderr);

    // Cleanup on unmount
    return () => {
      if (socket) {
        socket.off('stdout', handleStdout);
        socket.off('stderr', handleStderr);
      }
    };
  }, [socket, handleStdout, handleStderr]);

  useEffect(() => {
    // Scroll to bottom when new messages arrive, but don't steal focus
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [messages]);

  // Helper function to convert file to base64 data URL
  const fileToDataUrl = (file: File, kind: 'image' | 'audio'): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        resolve(reader.result as string);
      };
      reader.onerror = reject;
      if (kind === 'image') {
        reader.readAsDataURL(file);
      } else {
        reader.readAsDataURL(file);
      }
    });
  };

  const handleSendMessage = async (content: string, imageFile?: File, audioFile?: File) => {
    if (!content.trim() && !imageFile && !audioFile) {
      return;
    }

    // Generate unique action ID for this request
    const actionId = ++actionIdCounterRef.current;
    const userMessageId = Date.now().toString();
    const assistantMessageId = (Date.now() + 1).toString();

    // Determine input type and create Input object
    let input: Input;
    if (imageFile) {
      const dataUrl = await fileToDataUrl(imageFile, 'image');
      input = {
        kind: 'image',
        encoding: 'data_url',
        data: dataUrl
      };
    } else if (audioFile) {
      const dataUrl = await fileToDataUrl(audioFile, 'audio');
      input = {
        kind: 'audio',
        encoding: 'data_url',
        data: dataUrl
      };
    } else {
      input = {
        kind: 'text',
        encoding: 'utf8',
        data: content.trim()
      };
    }

    // Create multimodal message
    const userMultimodalMessage: MultimodalMessage = {
      role: 'user',
      content: input
    };

    // Update multimodal conversation
    const updatedConversation: MultimodalConversation = {
      messages: [...multimodalConversation.messages, userMultimodalMessage]
    };
    setMultimodalConversation(updatedConversation);

    // Create UI message for display
    const userMessage: Message = {
      id: userMessageId,
      role: 'user',
      content: content.trim() || (imageFile ? 'Image' : 'Audio'),
      timestamp: new Date(),
      type: imageFile ? 'image' : audioFile ? 'audio' : 'text',
      imageUrl: imageFile ? URL.createObjectURL(imageFile) : undefined,
      audioUrl: audioFile ? URL.createObjectURL(audioFile) : undefined,
    };
    setMessages(prev => [...prev, userMessage]);

    // Create placeholder message for assistant response
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      type: 'text',
      actionId,
      logs: [],
      isGenerating: true,
    };
    setMessages(prev => [...prev, assistantMessage]);

    // Map action ID to message ID for log routing
    actionIdToMessageIdRef.current.set(actionId, assistantMessageId);

    setIsLoading(true);

    try {
      // Send multimodal conversation to backend
      const response = await assaiAPI.sendMultimodalMessage(
        updatedConversation,
        sessionId ?? undefined,
        actionId
      );

      // Update conversation ID if new
      if (!conversationId) {
        setConversationId(response.conversation_id);
      }

      // Extract response content
      const responseInput = response.message.content;
      let responseText = '';
      let responseImageUrls: string[] | undefined;
      let responseAudioUrl: string | undefined;

      if (responseInput.kind === 'text') {
        responseText = responseInput.data;
      } else if (responseInput.kind === 'image') {
        responseImageUrls = [responseInput.data];
        responseText = 'Generated image';
      } else if (responseInput.kind === 'audio') {
        responseAudioUrl = responseInput.data;
        responseText = 'Generated audio';
      }

      // Update assistant message with response
      setMessages(prev => prev.map(msg => {
        if (msg.id === assistantMessageId) {
          return {
            ...msg,
            content: responseText,
            imageUrls: responseImageUrls,
            audioUrl: responseAudioUrl,
            logs: undefined,
            isGenerating: false,
          };
        }
        return msg;
      }));

      // Update multimodal conversation with assistant response
      setMultimodalConversation({
        messages: [...updatedConversation.messages, response.message]
      });

    } catch (error) {
      console.error('Failed to send message:', error);
      const errorMessage = error instanceof Error ? error.message : 'Failed to send message';

      // Update assistant message with error
      setMessages(prev => prev.map(msg => {
        if (msg.id === assistantMessageId) {
          return {
            ...msg,
            content: `Sorry, I encountered an error: ${errorMessage}. Please try again.`,
            logs: undefined,
            isGenerating: false,
          };
        }
        return msg;
      }));

      // Clean up action ID mapping
      actionIdToMessageIdRef.current.delete(actionId);
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
      overflow="hidden"
    >
      {/* Messages Area */}
      <Box
        flex={1}
        overflowY="auto"
        w="100%"
        minH={0}
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

      {/* Log Display */}
      <LogDisplay />
    </Box>
  );
};

export default Home;
