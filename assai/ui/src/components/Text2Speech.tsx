import { useState, useEffect, useRef, useCallback } from 'react';
import {
    Box,
    VStack,
    HStack,
    Text,
    Button,
    Input,
    IconButton,
} from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import LogDisplay from './LogDisplay';
import { Message } from '../services/types';
import { assaiAPI, SpeechGenerationParams } from '../services/api';
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

const Text2Speech = () => {
    const { socket, sessionId } = useWebSocket();
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [showSettings, setShowSettings] = useState(true);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const currentPromptRef = useRef<string>('');
    const actionIdCounterRef = useRef<number>(0);
    const actionIdToMessageIdRef = useRef<Map<number, string>>(new Map());

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<SpeechGenerationParams>({
        speed: 1.0,
        pitch: 0.0,
        sample_rate: 22050,
    });

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
        document.title = 'Text to Speech - ASSAI';

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
        // Scroll to bottom when new messages arrive
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSendMessage = async (content: string, _imageFile?: File, _audioFile?: File) => {
        if (!content.trim()) {
            return;
        }

        // Generate unique action ID for this request
        const actionId = ++actionIdCounterRef.current;
        const messageId = Date.now().toString();

        currentPromptRef.current = content.trim();

        // Add user message
        const userMessage: Message = {
            id: messageId,
            role: 'user',
            content: content.trim(),
            timestamp: new Date(),
            type: 'text',
        };
        setMessages(prev => [...prev, userMessage]);

        // Create placeholder message for logs/audio
        const assistantMessageId = (Date.now() + 1).toString();
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: ``,
            timestamp: new Date(),
            type: 'audio',
            actionId,
            logs: [],
            isGenerating: true,
        };
        setMessages(prev => [...prev, assistantMessage]);

        // Map action ID to message ID for log routing
        actionIdToMessageIdRef.current.set(actionId, assistantMessageId);

        setIsLoading(true);

        try {
            // Generate speech from prompt with current generation parameters and action_id
            const audioDataUris = await assaiAPI.generateSpeech(
                content.trim(),
                generationParams,
                undefined,
                sessionId ?? undefined,
                actionId
            );

            // Handle HTTP response - replace logs with audio
            if (audioDataUris && audioDataUris.length > 0) {
                setMessages(prev => prev.map(msg => {
                    if (msg.id === assistantMessageId) {
                        return {
                            ...msg,
                            audioUrl: audioDataUris[0], // Use first audio
                            logs: undefined, // Remove logs when audio is ready
                            isGenerating: false,
                            content: ``,
                        };
                    }
                    return msg;
                }));
            } else {
                throw new Error('No audio data received from server');
            }

            setIsLoading(false);

            // Clean up action ID mapping after a delay
            setTimeout(() => {
                actionIdToMessageIdRef.current.delete(actionId);
            }, 5000);
        } catch (error) {
            console.error('Failed to generate speech:', error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to generate speech';

            setIsLoading(false);

            // Update the placeholder message with error
            setMessages(prev => prev.map(msg => {
                if (msg.id === assistantMessageId) {
                    return {
                        ...msg,
                        content: `Sorry, I encountered an error: ${errorMessage}. Please try again.`,
                        type: 'text',
                        retryPrompt: content.trim(),
                        logs: undefined,
                        isGenerating: false,
                    };
                }
                return msg;
            }));

            // Clean up action ID mapping
            actionIdToMessageIdRef.current.delete(actionId);
        }
    };

    const handleRetry = async (prompt: string, errorMessageId?: string) => {
        // Remove the error message if we know its ID
        if (errorMessageId) {
            setMessages(prev => prev.filter(msg => msg.id !== errorMessageId));
        }
        // Retry the prompt with current generation parameters
        await handleSendMessage(prompt);
    };

    const resetToDefaults = () => {
        setGenerationParams({
            speed: 1.0,
            pitch: 0.0,
            sample_rate: 22050,
        });
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
                🔊
            </Box>

            <VStack gap={2} textAlign="center">
                <Text fontSize="2xl" fontWeight="semibold" color="white">
                    Text to Speech
                </Text>
                <Text fontSize="md" color="gray.400" maxW="md">
                    Enter text and I'll convert it to speech. Each prompt will generate a new audio file.
                </Text>
            </VStack>

            <VStack gap={3} w="100%" maxW="2xl" mt={4}>
                <Text fontSize="sm" fontWeight="semibold" color="gray.300">
                    Try prompts like:
                </Text>
                <VStack gap={2} w="100%">
                    {[
                        'Hello, this is a test of the text to speech system.',
                        'The quick brown fox jumps over the lazy dog.',
                        'Welcome to ASSAI, your AI assistant platform.',
                        'Text to speech conversion is now available.',
                    ].map((example) => (
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
                        <ChatInput
                            onSendMessage={handleSendMessage}
                            disabled={isLoading}
                            placeholder={messages.length === 0 ? "Enter text to convert to speech..." : "Enter more text..."}
                        />
                        {/* Settings Toggle Button */}
                        <IconButton
                            aria-label="Toggle settings"
                            position="absolute"
                            right={4}
                            top="50%"
                            transform="translateY(-50%)"
                            size="sm"
                            variant="ghost"
                            colorScheme="gray"
                            onClick={() => setShowSettings(!showSettings)}
                            zIndex={10}
                        >
                            <SettingsIcon />
                        </IconButton>
                    </Box>
                </Box>

                {/* Right Settings Panel - Part of chat area */}
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
                                <VStack gap={4} align="stretch">
                                    {/* Speed */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">
                                            Speed: {generationParams.speed?.toFixed(1)}x
                                        </Text>
                                        <Input
                                            type="number"
                                            value={generationParams.speed}
                                            onChange={(e) => {
                                                const value = parseFloat(e.target.value) || 1.0;
                                                setGenerationParams(prev => ({ ...prev, speed: value }));
                                            }}
                                            min={0.5}
                                            max={2.0}
                                            step={0.1}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    {/* Pitch */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">
                                            Pitch: {generationParams.pitch?.toFixed(1)} semitones
                                        </Text>
                                        <Input
                                            type="number"
                                            value={generationParams.pitch}
                                            onChange={(e) => {
                                                const value = parseFloat(e.target.value) || 0.0;
                                                setGenerationParams(prev => ({ ...prev, pitch: value }));
                                            }}
                                            min={-12.0}
                                            max={12.0}
                                            step={0.5}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    {/* Sample Rate */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">Sample Rate (Hz)</Text>
                                        <Input
                                            type="number"
                                            value={generationParams.sample_rate}
                                            onChange={(e) => {
                                                const value = parseInt(e.target.value) || 22050;
                                                setGenerationParams(prev => ({ ...prev, sample_rate: value }));
                                            }}
                                            min={8000}
                                            max={48000}
                                            step={1000}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={resetToDefaults}
                                        w="100%"
                                        color="gray.200"
                                        borderColor="gray.600"
                                        _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
                                    >
                                        Reset to Defaults
                                    </Button>
                                </VStack>
                            </Box>
                        </>
                    )}
                </Box>
            </Box>

            {/* Log Display - Separate from chat area, collapsible at bottom */}
            <LogDisplay />
        </Box>
    );
};

export default Text2Speech;

