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
import { assaiAPI, ImageGenerationParams } from '../services/api';
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

const Text2Image = () => {
    const { socket, sessionId } = useWebSocket();
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [showSettings, setShowSettings] = useState(true);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const currentPromptRef = useRef<string>('');
    const actionIdCounterRef = useRef<number>(0);
    const actionIdToMessageIdRef = useRef<Map<number, string>>(new Map());

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<ImageGenerationParams>({
        height: 256,
        width: 256,
        guidance_scale: 3.5,
        num_inference_steps: 50,
        max_sequence_length: 512,
        seed: 0,
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
        document.title = 'Text to Image - ASSAI';

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

        // Create placeholder message for logs/image
        const assistantMessageId = (Date.now() + 1).toString();
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: ``,
            timestamp: new Date(),
            type: 'image',
            actionId,
            logs: [],
            isGenerating: true,
        };
        setMessages(prev => [...prev, assistantMessage]);

        // Map action ID to message ID for log routing
        actionIdToMessageIdRef.current.set(actionId, assistantMessageId);

        setIsLoading(true);

        try {
            // Use session ID from context

            // Generate image from prompt with current generation parameters and action_id
            const imageDataUris = await assaiAPI.generateImage(
                content.trim(),
                generationParams,
                undefined,
                sessionId ?? undefined,
                actionId
            );

            // Handle HTTP response - replace logs with images
            if (imageDataUris && imageDataUris.length > 0) {
                setMessages(prev => prev.map(msg => {
                    if (msg.id === assistantMessageId) {
                        return {
                            ...msg,
                            imageUrls: imageDataUris, // Store all images
                            imageUrl: imageDataUris[0], // Keep first for backward compatibility
                            logs: undefined, // Remove logs when images are ready
                            isGenerating: false,
                            content: ``,
                        };
                    }
                    return msg;
                }));
            } else {
                throw new Error('No image data received from server');
            }

            setIsLoading(false);

            // Clean up action ID mapping after a delay
            setTimeout(() => {
                actionIdToMessageIdRef.current.delete(actionId);
            }, 5000);
        } catch (error) {
            console.error('Failed to generate image:', error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to generate image';

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
            height: 256,
            width: 256,
            guidance_scale: 3.5,
            num_inference_steps: 50,
            max_sequence_length: 512,
            seed: 0,
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
                bg="purple.500"
                borderRadius="xl"
                display="flex"
                alignItems="center"
                justifyContent="center"
                fontSize="2xl"
                color="white"
                fontWeight="bold"
            >
                🎨
            </Box>

            <VStack gap={2} textAlign="center">
                <Text fontSize="2xl" fontWeight="semibold" color="white">
                    Text to Image
                </Text>
                <Text fontSize="md" color="gray.400" maxW="md">
                    Describe the image you want to generate, and I'll create it for you.
                    Each prompt will generate a new image.
                </Text>
            </VStack>

            <VStack gap={3} w="100%" maxW="2xl" mt={4}>
                <Text fontSize="sm" fontWeight="semibold" color="gray.300">
                    Try prompts like:
                </Text>
                <VStack gap={2} w="100%">
                    {[
                        'A serene sunset over mountains',
                        'A futuristic cityscape at night',
                        'A cute cat playing with yarn',
                        'An abstract painting with vibrant colors',
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
                            placeholder={messages.length === 0 ? "Describe the image you want to generate..." : "Describe another image..."}
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
                                    {/* Width and Height */}
                                    <HStack gap={4}>
                                        <VStack align="flex-start" gap={1} flex={1}>
                                            <Text fontSize="sm" fontWeight="medium" color="gray.300">Width</Text>
                                            <Input
                                                type="number"
                                                value={generationParams.width}
                                                onChange={(e) => {
                                                    const value = parseInt(e.target.value) || 256;
                                                    setGenerationParams(prev => ({ ...prev, width: value }));
                                                }}
                                                min={64}
                                                max={2048}
                                                step={64}
                                                size="sm"
                                                bg="gray.700"
                                                borderColor="gray.600"
                                                color="gray.100"
                                                _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                                            />
                                        </VStack>

                                        <VStack align="flex-start" gap={1} flex={1}>
                                            <Text fontSize="sm" fontWeight="medium" color="gray.300">Height</Text>
                                            <Input
                                                type="number"
                                                value={generationParams.height}
                                                onChange={(e) => {
                                                    const value = parseInt(e.target.value) || 256;
                                                    setGenerationParams(prev => ({ ...prev, height: value }));
                                                }}
                                                min={64}
                                                max={2048}
                                                step={64}
                                                size="sm"
                                                bg="gray.700"
                                                borderColor="gray.600"
                                                color="gray.100"
                                                _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                                            />
                                        </VStack>
                                    </HStack>

                                    {/* Guidance Scale */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">
                                            Guidance Scale: {generationParams.guidance_scale?.toFixed(1)}
                                        </Text>
                                        <Input
                                            type="number"
                                            value={generationParams.guidance_scale}
                                            onChange={(e) => {
                                                const value = parseFloat(e.target.value) || 3.5;
                                                setGenerationParams(prev => ({ ...prev, guidance_scale: value }));
                                            }}
                                            min={1}
                                            max={20}
                                            step={0.1}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    {/* Inference Steps */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">Inference Steps</Text>
                                        <Input
                                            type="number"
                                            value={generationParams.num_inference_steps}
                                            onChange={(e) => {
                                                const value = parseInt(e.target.value) || 50;
                                                setGenerationParams(prev => ({ ...prev, num_inference_steps: value }));
                                            }}
                                            min={1}
                                            max={100}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    {/* Max Sequence Length */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">Max Sequence Length</Text>
                                        <Input
                                            type="number"
                                            value={generationParams.max_sequence_length}
                                            onChange={(e) => {
                                                const value = parseInt(e.target.value) || 512;
                                                setGenerationParams(prev => ({ ...prev, max_sequence_length: value }));
                                            }}
                                            min={128}
                                            max={2048}
                                            step={128}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                                        />
                                    </VStack>

                                    {/* Seed */}
                                    <VStack align="flex-start" gap={1}>
                                        <Text fontSize="sm" fontWeight="medium" color="gray.300">Seed (0 = random)</Text>
                                        <Input
                                            type="number"
                                            value={generationParams.seed}
                                            onChange={(e) => {
                                                const value = parseInt(e.target.value) || 0;
                                                setGenerationParams(prev => ({ ...prev, seed: value }));
                                            }}
                                            min={0}
                                            max={2147483647}
                                            size="sm"
                                            bg="gray.700"
                                            borderColor="gray.600"
                                            color="gray.100"
                                            _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
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

export default Text2Image;

