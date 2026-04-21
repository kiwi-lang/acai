import { useState, useEffect, useRef, useCallback, ReactNode } from 'react';
import {
    Box,
    VStack,
    HStack,
    Text,
    IconButton,
} from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import LogDisplay from './LogDisplay';
import { Message, Input as InputTypeDef } from '../services/types';
import { useWebSocket } from '../contexts/WebSocketContext';

const SettingsIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v6m0 6v6M5.64 5.64l4.24 4.24m4.24 4.24l4.24 4.24M1 12h6m6 0h6M5.64 18.36l4.24-4.24m4.24-4.24l4.24-4.24" />
    </svg>
);

const XIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

export type ChatInputType = 'text' | 'image' | 'audio';
export type ChatOutputType = 'text' | 'image' | 'audio' | 'video' | 'mesh';

export interface ChatComponentConfig {
    title: string;
    description?: string;
    allowedInputTypes: ChatInputType[];
    expectedOutputType: ChatOutputType;
    placeholder?: string;
    emptyStateTitle?: string;
    emptyStateDescription?: string;
    emptyStateExamples?: string[];
    onSendMessage: (message: Message, actionId: number) => Promise<{ message: Message }>;
    onRetry?: (prompt: string, messageId?: string | number) => void;
    settingsPanel?: ReactNode;
    showSettings?: boolean;
    defaultShowSettings?: boolean;
    customInput?: ReactNode; // Custom input component (e.g., for audio recording)
    modelSelector?: ReactNode; // Model selector component (e.g., dropdown for model selection)
}

interface ChatComponentProps {
    config: ChatComponentConfig;
}

const ChatComponent = ({ config }: ChatComponentProps) => {
    const { socket, sessionId } = useWebSocket();
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [showSettings, setShowSettings] = useState(config.defaultShowSettings ?? true);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const actionIdCounterRef = useRef<number>(0);

    // Memoize handlers to prevent duplicate listeners
    // These handlers route logs to messages based on action_id
    const handleStdout = useCallback((data: { id: number; line: string; thread_id?: number }) => {
        // Find the message with matching action_id
        setMessages(prev => prev.map(msg => {
            if (msg.action_id === data.id && msg.isGenerating) {
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
    }, []);

    const handleStderr = useCallback((data: { id: number; line: string; thread_id?: number }) => {
        // Find the message with matching action_id
        setMessages(prev => prev.map(msg => {
            if (msg.action_id === data.id && msg.isGenerating) {
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
    }, []);

    const handlePreview = useCallback((data: { id: number; thread_id?: number; images?: string[]; text?: string; is_complete?: boolean }) => {
        // Find the message with matching action_id and update with preview content
        setMessages(prev => prev.map(msg => {
            if (msg.action_id === data.id && msg.isGenerating) {
                // Handle text streaming (text2text)
                if (data.text !== undefined) {
                    return {
                        ...msg,
                        content: {
                            kind: 'text',
                            encoding: 'utf8',
                            data: data.text
                        },
                        isGenerating: !data.is_complete
                    };
                }

                // Handle image previews (text2image)
                if (data.images && data.images.length > 0) {
                    // Update the message with preview images
                    // These will be replaced by the final image when generation completes
                    return {
                        ...msg,
                        imageUrls: data.images,
                        imageUrl: data.images[0],
                        // Also update content for consistency
                        content: {
                            kind: 'image',
                            encoding: 'data_url',
                            data: data.images[0]
                        }
                    };
                }
            }
            return msg;
        }));
    }, []);

    useEffect(() => {
        document.title = `${config.title} - Açaí`;

        if (!socket) {
            return;
        }

        // Set up log listeners for stdout/stderr
        socket.off('stdout', handleStdout);
        socket.off('stderr', handleStderr);
        socket.on('stdout', handleStdout);
        socket.on('stderr', handleStderr);

        // Set up preview listener for text2image preview updates and text2text streaming
        socket.off('preview', handlePreview);
        socket.on('preview', handlePreview);

        // Cleanup on unmount
        return () => {
            if (socket) {
                socket.off('stdout', handleStdout);
                socket.off('stderr', handleStderr);
                socket.off('preview', handlePreview);
            }
        };
    }, [socket, handleStdout, handleStderr, handlePreview]);

    useEffect(() => {
        // Scroll to bottom when new messages arrive, but don't steal focus
        if (messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [messages]);

    const handleSendMessage = async (content: string, imageFile?: File, audioFile?: File) => {
        // Validate input based on allowed types
        if (!content.trim() && !imageFile && !audioFile) {
            return;
        }

        // Determine input type and create Input object
        let input: InputTypeDef;
        const trimmedContent = content.trim();
        if (imageFile && config.allowedInputTypes.includes('image')) {
            const reader = new FileReader();
            const dataUrl = await new Promise<string>((resolve, reject) => {
                reader.onloadend = () => resolve(reader.result as string);
                reader.onerror = reject;
                reader.readAsDataURL(imageFile);
            });
            input = {
                kind: 'image',
                encoding: 'data_url',
                data: dataUrl
            };
        } else if (audioFile && config.allowedInputTypes.includes('audio')) {
            const reader = new FileReader();
            const dataUrl = await new Promise<string>((resolve, reject) => {
                reader.onloadend = () => resolve(reader.result as string);
                reader.onerror = reject;
                reader.readAsDataURL(audioFile);
            });
            input = {
                kind: 'audio',
                encoding: 'data_url',
                data: dataUrl
            };
        } else if (config.allowedInputTypes.includes('text')) {
            input = {
                kind: 'text',
                encoding: 'utf8',
                data: trimmedContent
            };
        } else {
            return; // No valid input type
        }

        // Generate unique action ID for this request
        const actionId = ++actionIdCounterRef.current;
        const messageId = Date.now();

        // Create user message in unified Input format
        const userMessage: Message = {
            id: messageId,
            role: 'user',
            content: input,
            timestamp: new Date().toISOString(),
            type: input.kind as 'text' | 'image' | 'audio' | 'video', // UI extension
        };
        setMessages(prev => [...prev, userMessage]);

        // Create placeholder message for assistant response
        const assistantMessageId = Date.now() + 1;
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: {
                kind: config.expectedOutputType,
                encoding: config.expectedOutputType === 'text' ? 'utf8' : 'data_url',
                data: ''
            },
            timestamp: new Date().toISOString(),
            type: config.expectedOutputType, // UI extension
            action_id: actionId,
            logs: [],
            isGenerating: true,
        };
        setMessages(prev => [...prev, assistantMessage]);

        setIsLoading(true);

        try {
            // Call the configured send handler with the actionId for WebSocket log routing
            const response = await config.onSendMessage(userMessage, actionId);

            // Handle HTTP response - update assistant message with response
            if (response && response.message) {
                const responseMessage = response.message;
                setMessages(prev => prev.map(msg => {
                    if (msg.id === assistantMessageId) {
                        // Extract display values from Input for UI compatibility
                        const input = responseMessage.content;
                        const displayMessage: Message = {
                            ...responseMessage,
                            id: assistantMessageId, // Keep our ID
                            logs: undefined, // Remove logs when response is ready
                            isGenerating: false,
                            type: input.kind === 'text' ? 'text' : input.kind === 'image' ? 'image' : input.kind === 'audio' ? 'audio' : 'text',
                            // Extract display values for backward compatibility
                            ...(input.kind === 'image' && { imageUrl: input.data, imageUrls: [input.data] }),
                            ...(input.kind === 'audio' && { audioUrl: input.data })
                        };
                        // Handle multiple images if backend returns them in response
                        if ((response as any).images && Array.isArray((response as any).images)) {
                            displayMessage.imageUrls = (response as any).images;
                            displayMessage.imageUrl = (response as any).images[0];
                        }
                        return displayMessage;
                    }
                    return msg;
                }));
            } else {
                throw new Error('No message received from server');
            }

            setIsLoading(false);

            // No cleanup needed - logs are matched by action_id directly
        } catch (error) {
            console.error(`Failed to process message:`, error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to process message';

            setIsLoading(false);

            // Update the placeholder message with error
            const errorInput: InputTypeDef = {
                kind: 'text',
                encoding: 'utf8',
                data: `Sorry, I encountered an error: ${errorMessage}. Please try again.`
            };

            setMessages(prev => prev.map(msg => {
                if (msg.id === assistantMessageId) {
                    return {
                        ...msg,
                        content: errorInput,
                        type: 'text',
                        retryPrompt: typeof input === 'object' && input.kind === 'text' ? input.data : trimmedContent,
                        logs: undefined,
                        isGenerating: false,
                    };
                }
                return msg;
            }));

            // No cleanup needed - logs are matched by action_id directly
        }
    };

    const handleRetry = async (prompt: string, errorMessageId?: string | number) => {
        // Remove the error message if we know its ID
        if (errorMessageId !== undefined) {
            setMessages(prev => prev.filter(msg => String(msg.id) !== String(errorMessageId)));
        }
        // Retry the prompt
        if (config.onRetry) {
            await config.onRetry(prompt, errorMessageId);
        } else {
            // Default retry behavior
            await handleSendMessage(prompt);
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
                bg="blue.500"
                borderRadius="xl"
                display="flex"
                alignItems="center"
                justifyContent="center"
                fontSize="2xl"
                color="white"
                fontWeight="bold"
            >
                💬
            </Box>

            <VStack gap={2} textAlign="center">
                <Text fontSize="2xl" fontWeight="semibold" color="white">
                    {config.emptyStateTitle || config.title}
                </Text>
                <Text fontSize="md" color="gray.400" maxW="md">
                    {config.emptyStateDescription || config.description}
                </Text>
            </VStack>

            {config.emptyStateExamples && config.emptyStateExamples.length > 0 && (
                <VStack gap={3} w="100%" maxW="2xl" mt={4}>
                    <Text fontSize="sm" fontWeight="semibold" color="gray.300">
                        Try prompts like:
                    </Text>
                    <VStack gap={2} w="100%">
                        {config.emptyStateExamples.map((example) => (
                            <Box
                                key={example}
                                p={3}
                                bg="gray.800"
                                borderRadius="lg"
                                w="100%"
                                fontSize="sm"
                                color="gray.300"
                                cursor="pointer"
                                _hover={{ bg: 'gray.700' }}
                                onClick={() => handleSendMessage(example)}
                            >
                                {example}
                            </Box>
                        ))}
                    </VStack>
                </VStack>
            )}
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
            {/* Chat Area - Conversation + Input + Settings */}
            <Box
                display="flex"
                flexDirection="row"
                flex={1}
                minH={0}
                overflow="hidden"
            >
                {/* Conversation + Input Column */}
                <Box
                    display="flex"
                    flexDirection="column"
                    flex={1}
                    minW={0}
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
                                    <ChatMessage
                                        key={message.id}
                                        message={message}
                                        onRetry={handleRetry}
                                    />
                                ))}
                                <div ref={messagesEndRef} />
                            </VStack>
                        )}
                    </Box>

                    {/* Input Area */}
                    <Box position="relative" borderTop="1px solid" borderColor="gray.700">
                        {/* Model Selector */}
                        {config.modelSelector && (
                            <Box px={4} py={2} borderBottom="1px solid" borderColor="gray.700">
                                {config.modelSelector}
                            </Box>
                        )}
                        {config.customInput ? (
                            config.customInput
                        ) : (
                            <ChatInput
                                onSendMessage={handleSendMessage}
                                disabled={isLoading}
                                placeholder={config.placeholder || (messages.length === 0 ? `Start a conversation...` : `Continue the conversation...`)}
                            />
                        )}
                        {/* Settings Toggle Button */}
                        {config.settingsPanel && (
                            <IconButton
                                aria-label="Toggle settings"
                                position="absolute"
                                right={4}
                                top={config.modelSelector ? "calc(50% + 20px)" : "50%"}
                                transform="translateY(-50%)"
                                size="sm"
                                variant="ghost"
                                colorScheme="gray"
                                onClick={() => setShowSettings(!showSettings)}
                                zIndex={10}
                            >
                                <SettingsIcon />
                            </IconButton>
                        )}
                    </Box>
                </Box>

                {/* Right Settings Panel - Part of chat area */}
                {config.settingsPanel && (
                    <Box
                        w={showSettings ? "320px" : "0"}
                        borderLeft="1px solid"
                        borderColor="gray.700"
                        bg="gray.800"
                        overflow="hidden"
                        transition="width 0.3s ease-in-out"
                        display="flex"
                        flexDirection="column"
                        flexShrink={0}
                    >
                        {showSettings && (
                            <>
                                {/* Settings Header */}
                                <HStack
                                    px={4}
                                    py={3}
                                    justify="space-between"
                                    borderBottom="1px solid"
                                    borderColor="gray.700"
                                >
                                    <HStack gap={2}>
                                        <SettingsIcon />
                                        <Text fontSize="sm" fontWeight="semibold" color="gray.200">
                                            Settings
                                        </Text>
                                    </HStack>
                                    <IconButton
                                        aria-label="Close settings"
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => setShowSettings(false)}
                                    >
                                        <XIcon />
                                    </IconButton>
                                </HStack>

                                {/* Settings Content */}
                                <Box
                                    flex={1}
                                    overflowY="auto"
                                    px={4}
                                    py={4}
                                >
                                    {config.settingsPanel}
                                </Box>
                            </>
                        )}
                    </Box>
                )}
            </Box>

            {/* Log Display - Separate from chat area, collapsible at bottom */}
            <LogDisplay />
        </Box>
    );
};

export default ChatComponent;

